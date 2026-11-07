"""审计修复的回归：路径穿越、配额不可被非法请求消耗、SSRF、分享公开边界。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.match import api as api_module
from services.match.settings import reset_settings

from .support import create, wait_finished

# ---- 安全与指标 ----

def test_security_headers_include_csp(client):
    response = client.get("/games")
    assert response.status_code == 200
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers.get("x-content-type-options") == "nosniff"

def test_metrics_are_public_when_auth_off(client):
    assert client.get("/metrics").status_code == 200
    assert client.get("/metrics/business").status_code == 200

def test_metrics_require_token_when_auth_on(tmp_path, monkeypatch):
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:secret-token")
    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    reset_settings()
    try:
        protected = TestClient(api_module.create_app())
        assert protected.get("/metrics").status_code == 401
        allowed = protected.get("/metrics", headers={"Authorization": "Bearer secret-token"})
        assert allowed.status_code == 200
    finally:
        reset_module_settings()

def reset_module_settings() -> None:
    from services.match.settings import reset_settings

    reset_settings()

def test_card_regenerates_for_live_match(client):
    """直播中的卡片每次重画：胜负还没定，不能拿旧图缓存。"""
    created = create(client)
    first = client.get(f"/matches/{created['matchId']}/card.png")
    assert first.status_code == 200
    second = client.get(f"/matches/{created['matchId']}/card.png")
    assert second.status_code == 200
    assert second.content.startswith(b"\x89PNG")

def test_stats_reports_viewers_as_integer(client):
    data = client.get("/stats").json()
    assert isinstance(data["viewers"], int)
    assert data["viewers"] >= 0

# ---- 审计修复的回归 ----

def test_clip_download_rejects_path_traversal(client):
    """match_id 参与路径拼接：`..`、盘符、反斜杠都必须 400，不能读到库文件。"""
    for bad in ("..", "%2e%2e", "C:", "a%2Fb"):
        response = client.get(f"/matches/{bad}/clips/match.db")
        assert response.status_code in (400, 404), (bad, response.status_code)
        assert b"SQLite format" not in response.content

def test_media_ticket_rejects_bad_match_id(client):
    # 路径里的 `..` 会被 TestClient 归一化，这里是"形状不合法"的另一条路径。
    assert client.post("/matches/" + "x" * 80 + "/media-ticket").status_code == 400

def test_bad_json_body_is_400_not_500(client):
    response = client.post(
        "/director/m_x/cue", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400

def test_bad_start_at_is_400_not_500(client):
    response = client.post(
        "/seasons",
        json={"seasonId": "s", "name": "x", "players": [{"id": "A"}, {"id": "B"}], "startAt": "not-a-date"},
    )
    assert response.status_code == 400

def test_unknown_season_does_not_leave_orphan_match(client):
    before = client.get("/archive").json()["count"]
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "players": [{"id": "A"}, {"id": "B"}],
            "seasonId": "nope",
        },
    )
    assert response.status_code == 404
    assert client.get("/archive").json()["count"] == before

def test_quota_is_not_consumed_by_invalid_requests(client, monkeypatch):
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "2")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t")
    reset_settings()
    try:
        headers = {"Authorization": "Bearer t"}
        # 两次非法请求（赛制不存在）不该扣额度
        for _ in range(2):
            bad = client.post(
                "/matches", json={"game": "nope", "players": [{"id": "A"}, {"id": "B"}]}, headers=headers
            )
            assert bad.status_code == 400
        used = client.get("/quota", headers=headers).json()
        assert used["matches"] == 0
        ok = client.post(
            "/matches", json={"game": "twenty-questions", "players": [{"id": "A"}, {"id": "B"}]}, headers=headers
        )
        assert ok.status_code == 201
    finally:
        monkeypatch.undo()
        reset_settings()

def test_season_run_returns_409_when_locked(client):
    import sqlite3 as _sqlite3

    from services.match.schedule import _acquire_season_lock

    client.post(
        "/seasons",
        json={"seasonId": "s-lock", "name": "锁", "players": [{"id": "A"}, {"id": "B"}]},
    )
    conn = _sqlite3.connect(api_module.DB_PATH)
    try:
        assert _acquire_season_lock(conn, "s-lock", "other-owner", ttl_s=60)
    finally:
        conn.close()
    response = client.post("/seasons/s-lock/run")
    assert response.status_code == 409

def test_recreating_season_replaces_old_roster(client):
    client.post(
        "/seasons",
        json={"seasonId": "s-re", "name": "旧", "players": [{"id": "A"}, {"id": "B"}, {"id": "C"}]},
    )
    client.post(
        "/seasons",
        json={"seasonId": "s-re", "name": "新", "players": [{"id": "X"}, {"id": "Y"}]},
    )
    data = client.get("/seasons/s-re").json()
    assert {row["playerId"] for row in data["standings"]} == {"X", "Y"}
    assert len(data["fixtures"]) == 1

def test_codenames_last_word_revealed_by_opponent_ends_game():
    """对手翻掉你最后一个词，等于你的词全被翻开——该队获胜。"""
    from services.match.engine.base import Move
    from services.match.engine.codenames import Codenames

    game = Codenames()
    state = game.init(seed=5, players=["A", "B", "C", "D"], config={})
    # 只留一个红词未翻开，其余红词都翻开
    red_indices = [i for i, color in enumerate(state["colors"]) if color == "red"]
    for i in red_indices[:-1]:
        state["revealed"][i] = True
    last_red = red_indices[-1]
    state["turn"] = "blue"
    state["phase"] = "guess"
    state["remainingGuesses"] = 3
    # 蓝队队员是 players[3]（D）；players[2] 是红队队员。
    result = game.apply(state, "D", Move(kind="guess", value=f"pick:{state['words'][last_red]}"))
    assert result.legal
    assert result.next_state["finished"] is True
    assert result.next_state["winner"] == "red"

def test_ssrf_blocks_cgnat_and_ula():
    from services.match.outbound import OutboundAddressError, check_base_url

    for url in ("http://100.100.100.200/v1", "http://[fc00::1]/v1"):
        try:
            check_base_url(url)
        except OutboundAddressError:
            continue
        raise AssertionError(f"应当拒绝：{url}")

def test_season_run_charges_quota_for_every_fixture(client, monkeypatch):
    """不传 maxFixtures 会跑完整个赛季，必须按剩余场次扣配额。

    旧实现固定扣 1 格：一次请求就能绕过每日建场上限跑掉整个赛季。
    """
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t")
    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "3")
    reset_settings()
    try:
        headers = {"Authorization": "Bearer t"}
        client.post(
            "/seasons",
            json={"seasonId": "s-quota", "name": "配额", "players": [{"id": "A"}, {"id": "B"}], "game": "tic-tac-toe"},
            headers=headers,
        )
        # 2 人单循环只有 1 场；把上限设成 2 才跑得动，这里验证第 1 场扣 1 格。
        response = client.post("/seasons/s-quota/run", headers=headers)
        assert response.status_code == 200, response.text
        used = client.get("/quota", headers=headers).json()
        assert used["matches"] == 1
    finally:
        monkeypatch.undo()
        reset_settings()

def test_season_run_refuses_when_quota_insufficient(client, monkeypatch):
    """剩余场次超过配额上限时必须拒绝，而不是先跑完再欠账。"""
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t")
    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "2")
    reset_settings()
    try:
        headers = {"Authorization": "Bearer t"}
        client.post(
            "/seasons",
            json={
                "seasonId": "s-quota2",
                "name": "配额2",
                "game": "tic-tac-toe",
                "players": [{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}],
            },
            headers=headers,
        )
        # 4 人单循环 6 场 > 每日上限 2
        response = client.post("/seasons/s-quota2/run", headers=headers)
        assert response.status_code == 429
        assert client.get("/quota", headers=headers).json()["matches"] == 0
    finally:
        monkeypatch.undo()
        reset_settings()

def test_share_page_is_reachable_by_crawlers_in_token_mode(tmp_path, monkeypatch):
    """社交平台爬虫带不了 Bearer：分享页与分享卡默认公开，否则 OG 预览永远 401。"""
    from fastapi.testclient import TestClient

    from services.match.settings import reset_settings

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1")
    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "100")
    reset_settings()
    client = TestClient(api_module.create_app())
    try:
        created = client.post(
            "/matches",
            json={"game": "tic-tac-toe", "players": [{"id": "A"}, {"id": "B"}], "inlineWorker": False},
            headers={"Authorization": "Bearer t1"},
        )
        match_id = created.json()["matchId"]

        # 无凭据可读分享页与分享卡
        share = client.get(f"/share/{match_id}")
        assert share.status_code == 200
        assert "og:title" in share.text
        assert client.get(f"/matches/{match_id}/card.png").status_code == 200

        # 其余读接口仍然鉴权
        assert client.get(f"/matches/{match_id}/events").status_code == 401
        assert client.get(f"/matches/{match_id}/recap").status_code == 401
    finally:
        monkeypatch.undo()
        reset_settings()

def test_public_share_can_be_disabled(tmp_path, monkeypatch):
    """需要私密部署时，MELEE_PUBLIC_SHARE=off 让分享页重新要求鉴权。"""
    from fastapi.testclient import TestClient

    from services.match.settings import reset_settings

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1")
    monkeypatch.setenv("MELEE_PUBLIC_SHARE", "off")
    reset_settings()
    client = TestClient(api_module.create_app())
    try:
        created = client.post(
            "/matches",
            json={"game": "tic-tac-toe", "players": [{"id": "A"}, {"id": "B"}], "inlineWorker": False},
            headers={"Authorization": "Bearer t1"},
        )
        match_id = created.json()["matchId"]
        assert client.get(f"/share/{match_id}").status_code == 401
        assert client.get(f"/share/{match_id}", headers={"Authorization": "Bearer t1"}).status_code == 200
    finally:
        monkeypatch.undo()
        reset_settings()
