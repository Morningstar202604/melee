from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from services.match import auth, ratelimit
from services.match.auth import AuthError, Principal, QuotaConfig, authenticate, check_and_consume, usage
from services.match.ratelimit import Limiter
from services.match.secrets import SecretCipher, cipher_from_env, fingerprint, generate_key, open_specs, seal_specs


# ---------- 凭据封存 ----------

def test_secret_roundtrip():
    cipher = SecretCipher(generate_key())
    sealed = cipher.seal("sk-very-secret")
    assert "sk-very-secret" not in sealed
    assert sealed.startswith("enc:v1:")
    assert cipher.open(sealed) == "sk-very-secret"


def test_cipher_disabled_passes_through():
    cipher = SecretCipher(None)
    assert cipher.seal("plain") == "plain"
    assert cipher.open("plain") == "plain"


def test_cannot_open_without_key():
    key = generate_key()
    sealed = SecretCipher(key).seal("x")
    with pytest.raises(RuntimeError):
        SecretCipher(None).open(sealed)


def test_seal_specs_hides_api_key():
    cipher = SecretCipher(generate_key())
    specs = [
        {"id": "A", "model": {"baseUrl": "http://x", "model": "m", "apiKey": "sk-abc"}},
        {"id": "B", "script": []},
    ]
    sealed = seal_specs(specs, cipher)
    assert sealed.encrypted is True
    assert "sk-abc" not in str(sealed.data)
    opened = open_specs(sealed.as_dict(), cipher)
    assert opened[0]["model"]["apiKey"] == "sk-abc"
    assert opened[1]["id"] == "B"


def test_open_specs_accepts_legacy_list():
    cipher = SecretCipher(None)
    specs = [{"id": "A"}]
    assert open_specs(specs, cipher) == specs


def test_fingerprint_is_stable_and_short():
    assert fingerprint("sk-abc") == fingerprint("sk-abc")
    assert fingerprint("sk-abc") != fingerprint("sk-abd")
    assert len(fingerprint("sk-abc")) == 8


def test_cipher_from_env_warns_without_key(monkeypatch):
    monkeypatch.delenv("MELEE_SECRET_KEY", raising=False)
    cipher = cipher_from_env()
    assert cipher.enabled is False


# ---------- 鉴权 ----------

def test_auth_off_allows_everyone():
    principal = authenticate(QuotaConfig(mode="off"), None)
    assert principal.authenticated and principal.name == "local"


def test_auth_requires_bearer():
    config = QuotaConfig(mode="token", tokens={"alice": "t1"})
    with pytest.raises(AuthError) as exc:
        authenticate(config, None)
    assert exc.value.status == 401


def test_auth_rejects_wrong_token():
    config = QuotaConfig(mode="token", tokens={"alice": "t1"})
    with pytest.raises(AuthError):
        authenticate(config, "Bearer nope")


def test_auth_accepts_valid_token():
    config = QuotaConfig(mode="token", tokens={"alice": "t1"})
    principal = authenticate(config, "Bearer t1")
    assert principal.name == "alice"


def test_auth_parses_env_tokens(monkeypatch):
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1, bob:t2")
    config = QuotaConfig.from_env()
    assert config.tokens == {"alice": "t1", "bob": "t2"}


# ---------- 配额 ----------

def test_quota_allows_until_limit(tmp_path):
    db = tmp_path / "q.db"
    principal = Principal("alice", True)
    config = QuotaConfig(mode="token", matches_per_day=2)
    check_and_consume(db, principal, "matches", config)
    check_and_consume(db, principal, "matches", config)
    with pytest.raises(AuthError) as exc:
        check_and_consume(db, principal, "matches", config)
    assert exc.value.status == 429


def test_quota_separates_kinds(tmp_path):
    db = tmp_path / "q.db"
    principal = Principal("alice", True)
    config = QuotaConfig(mode="token", matches_per_day=1, renders_per_day=1)
    check_and_consume(db, principal, "matches", config)
    check_and_consume(db, principal, "renders", config)
    assert usage(db, principal)["matches"] == 1
    assert usage(db, principal)["renders"] == 1


def test_quota_zero_disables(tmp_path):
    db = tmp_path / "q.db"
    principal = Principal("alice", True)
    with pytest.raises(AuthError) as exc:
        check_and_consume(db, principal, "matches", QuotaConfig(mode="token", matches_per_day=0))
    assert exc.value.status == 403


def test_quota_isolated_per_principal(tmp_path):
    db = tmp_path / "q.db"
    config = QuotaConfig(mode="token", matches_per_day=1)
    check_and_consume(db, Principal("alice", True), "matches", config)
    check_and_consume(db, Principal("bob", True), "matches", config)  # 不共享


# ---------- 限流 ----------
# 实现交给 limits 库（MovingWindowRateLimiter + MemoryStorage/RedisStorage），
# 这里只测我们的分档与封装语义。

def test_limiter_allows_within_limit():
    limiter = Limiter()
    for _ in range(10):
        assert limiter.hit("k", "write_match").allowed


def test_limiter_blocks_over_limit():
    limiter = Limiter()
    for _ in range(10):
        limiter.hit("k", "write_match")
    decision = limiter.hit("k", "write_match")
    assert not decision.allowed
    assert decision.remaining == 0


def test_limiter_isolates_keys():
    limiter = Limiter()
    limiter.hit("a", "write_season")
    assert limiter.hit("b", "write_season").allowed


def test_limiter_profiles_have_expected_limits():
    from services.match.ratelimit import PROFILES

    assert PROFILES["write_match"] == "10/minute"
    assert PROFILES["read"] == "120/minute"
    assert PROFILES["stream"] == "30/minute"


class FakeRequest:
    def __init__(self, headers: dict[str, str], host: str = "1.2.3.4") -> None:
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_uses_last_hop(monkeypatch):
    monkeypatch.setenv("MELEE_TRUSTED_PROXY_HOPS", "1")
    request = FakeRequest({"x-forwarded-for": "bogus, 9.9.9.9"})
    assert ratelimit.client_ip(request) == "9.9.9.9"


def test_client_ip_falls_back_to_socket():
    assert ratelimit.client_ip(FakeRequest({})) == "1.2.3.4"


def test_client_ip_ignores_header_by_default(monkeypatch):
    """默认不信任 XFF：否则每次换一个值就能绕过每 IP 限流。"""
    monkeypatch.delenv("MELEE_TRUSTED_PROXY_HOPS", raising=False)
    request = FakeRequest({"x-forwarded-for": "9.9.9.9"})
    assert ratelimit.client_ip(request) == "1.2.3.4"


def test_client_ip_with_zero_hops_ignores_header(monkeypatch):
    monkeypatch.setenv("MELEE_TRUSTED_PROXY_HOPS", "0")
    request = FakeRequest({"x-forwarded-for": "9.9.9.9"})
    assert ratelimit.client_ip(request) == "1.2.3.4"


def test_rotating_forwarded_for_cannot_bypass_rate_limit(tmp_path, monkeypatch):
    """真实复现：12 次建场，每次换一个 XFF，仍然必须在第 11 次被限流。"""
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.delenv("MELEE_TRUSTED_PROXY_HOPS", raising=False)
    monkeypatch.setenv("MELEE_AUTH", "off")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())

    codes = []
    for index in range(12):
        response = client.post(
            "/matches",
            json={"game": "twenty-questions", "players": [{"id": "A"}, {"id": "B"}], "inlineWorker": False},
            headers={"X-Forwarded-For": f"10.0.0.{index}"},
        )
        codes.append(response.status_code)
    assert codes[:10] == [201] * 10, codes
    assert codes[10:] == [429, 429], codes


# ---------- 端到端：token 模式 ----------

def test_token_mode_end_to_end(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())

    assert client.get("/matches").status_code == 401
    assert client.get("/matches", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/matches", headers={"Authorization": "Bearer t1"})
    assert ok.status_code == 200


# ---------- 管理接口鉴权与任务载荷脱敏 ----------

def test_admin_endpoints_require_token_and_never_echo_api_key(tmp_path, monkeypatch, allow_local_endpoints):
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1")
    monkeypatch.delenv("MELEE_SECRET_KEY", raising=False)
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())

    body = {
        "game": "twenty-questions",
        "inlineWorker": False,
        "players": [
            {
                "id": "A",
                "model": {
                    "baseUrl": "http://127.0.0.1:1/v1",
                    "model": "m",
                    "apiKey": "sk-plaintext-leak",
                },
            },
            {"id": "B"},
        ],
    }
    created = client.post("/matches", json=body, headers={"Authorization": "Bearer t1"})
    assert created.status_code == 201, created.text
    job_id = created.json()["jobId"]

    assert client.get("/jobs").status_code == 401
    assert client.get(f"/jobs/{job_id}").status_code == 401
    assert client.get("/scheduler").status_code == 401
    assert client.post("/scheduler/tick").status_code == 401
    assert client.get("/seasons/s1").status_code == 401

    listing = client.get("/jobs", headers={"Authorization": "Bearer t1"})
    assert listing.status_code == 200
    assert "sk-plaintext-leak" not in listing.text
    detail = client.get(f"/jobs/{job_id}", headers={"Authorization": "Bearer t1"})
    assert detail.status_code == 200
    assert "sk-plaintext-leak" not in detail.text


# ---------- 媒体票据（<video> 拉片） ----------

def test_media_ticket_is_reusable_and_bound_to_match():
    from services.match.auth import issue_media_ticket, redeem_media_ticket

    principal = Principal("alice", True)
    ticket = issue_media_ticket(principal, "m1")
    # Range 请求会反复打同一 URL，所以票据必须可复用
    assert redeem_media_ticket(ticket, "m1") is not None
    assert redeem_media_ticket(ticket, "m1") is not None
    # 但不能拿去读别的比赛
    assert redeem_media_ticket(ticket, "m2") is None
    assert redeem_media_ticket("nope", "m1") is None


def test_media_ticket_expires():
    from services.match.auth import issue_media_ticket, redeem_media_ticket

    ticket = issue_media_ticket(Principal("alice", True), "m1", ttl_s=-1)
    assert redeem_media_ticket(ticket, "m1") is None


def test_clip_download_requires_credentials_in_token_mode(tmp_path, monkeypatch):
    """视频下载必须鉴权：token 模式下无凭据 401，带媒体票据 200。"""
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())

    clips = api_module.DB_PATH.parent / "clips" / "m1"
    clips.mkdir(parents=True)
    (clips / "01-guess.mp4").write_bytes(b"fake-mp4")

    assert client.get("/matches/m1/clips/01-guess.mp4").status_code == 401
    assert client.get("/matches/m1/clips/01-guess.mp4", headers={"Authorization": "Bearer t1"}).status_code == 200

    ticket = client.post(
        "/matches/m1/media-ticket", headers={"Authorization": "Bearer t1"}
    ).json()["ticket"]
    assert client.get(f"/matches/m1/clips/01-guess.mp4?ticket={ticket}").status_code == 200
    assert client.get("/matches/m2/clips/01-guess.mp4?ticket=" + ticket).status_code == 401


def test_ratelimit_redis_backend_shares_state_across_instances():
    """MELEE_RATELIMIT=redis：两个 Limiter 实例共享同一份计数。"""
    from limits.storage import RedisStorage

    from services.match.ratelimit import Limiter

    url = os.environ.get("MELEE_TEST_REDIS_URL")
    if not url:
        pytest.skip("未提供 MELEE_TEST_REDIS_URL")
    from services.match.tests.test_extras import _redis_reachable

    if not _redis_reachable(url):
        pytest.skip(f"Redis 不可达：{url}")
    first = Limiter(RedisStorage(url))
    second = Limiter(RedisStorage(url))
    first.reset()
    for _ in range(10):
        assert first.hit("shared", "write_match").allowed
    assert second.hit("shared", "write_match").allowed is False
    first.reset()


def test_ratelimit_backend_selection(monkeypatch):
    """MELEE_RATELIMIT 决定后端；缺 URL 或连不上时退回内存，不让启动失败。"""
    from limits.storage import MemoryStorage, RedisStorage

    from services.match import ratelimit

    ratelimit.reset_limiter()
    monkeypatch.setenv("MELEE_RATELIMIT", "redis")
    monkeypatch.delenv("MELEE_REDIS_URL", raising=False)
    assert isinstance(ratelimit.limiter()._storage, MemoryStorage)

    monkeypatch.setenv("MELEE_REDIS_URL", "redis://127.0.0.1:6399/0")
    from services.match.settings import reset_settings

    reset_settings()
    ratelimit.reset_limiter()
    assert isinstance(ratelimit.limiter()._storage, RedisStorage)
    ratelimit.reset_limiter()


def test_oauth_like_errors_do_not_leak_internal_details(tmp_path, monkeypatch):
    """校验失败只回 422，不回显内部路径 / 栈信息。"""
    from fastapi.testclient import TestClient

    from services.match import api as api_module

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())
    response = client.post("/matches", json={"game": "twenty-questions", "players": []})
    assert response.status_code == 422
    body = response.text
    assert "Traceback" not in body
    assert "site-packages" not in body


def test_tickets_are_signed_not_stored(monkeypatch):
    """票据是签名串：改一个字符就失效；两个进程用同一密钥也能互相验证。"""
    from services.match.auth import issue_media_ticket, redeem_media_ticket

    monkeypatch.setenv("MELEE_TICKET_SECRET", "fixed-secret-for-test")
    ticket = issue_media_ticket(Principal("alice", True), "m1")
    head, _, rest = ticket.partition(".")
    assert rest  # itsdangerous 的 payload.timestamp.signature 三段
    # 改 payload 里的一个字符：签名必然对不上。
    # 不能改签名尾部：base64 的最后两个字符可能只影响填充位（Linux 上实测过）。
    tampered = ("b" if head[0] != "b" else "c") + head[1:] + "." + rest
    assert redeem_media_ticket(tampered, "m1") is None


def test_ticket_survives_process_restart(monkeypatch):
    """无状态票据：同一密钥下，重新构造的 serializer 也能验证（跨副本/重启可用）。"""
    from services.match.auth import _ticket_serializer, issue_media_ticket

    monkeypatch.setenv("MELEE_TICKET_SECRET", "another-fixed-secret")
    ticket = issue_media_ticket(Principal("bob", True), "m2")
    data = _ticket_serializer().loads(ticket)
    assert data["n"] == "bob" and data["m"] == "m2" and data["k"] == "media"


def test_quota_is_atomic_under_concurrency(tmp_path):
    """并发占额度不能超发：20 个线程抢 5 个名额，只能成 5 个。"""
    import threading

    db = tmp_path / "q.db"
    principal = Principal("alice", True)
    config = QuotaConfig(mode="token", matches_per_day=5)
    ok: list[int] = []
    denied: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            check_and_consume(db, principal, "matches", config)
        except AuthError as exc:
            with lock:
                denied.append(exc.status)
        else:
            with lock:
                ok.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(ok) == 5, f"放行了 {len(ok)} 次，应该恰好 5 次"
    assert len(denied) == 15
