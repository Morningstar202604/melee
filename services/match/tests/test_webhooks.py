"""Webhook 分发：签名、重试、回环防护、终局载荷，用真实 HTTP 接收端验证。"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import threading
import time

import pytest

from services.match import webhooks
from services.match.settings import reset_settings, settings


class _Receiver(http.server.BaseHTTPRequestHandler):
    received: list = []
    fail_first = 0

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        _Receiver.received.append(
            {
                "raw": raw,
                "signature": self.headers.get("X-Melee-Signature"),
                "timestamp": self.headers.get("X-Melee-Timestamp"),
                "event": self.headers.get("X-Melee-Event"),
            }
        )
        if _Receiver.fail_first > 0:
            _Receiver.fail_first -= 1
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture()
def hook_url(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/hook" % server.server_address[1]
    monkeypatch.setenv("MELEE_ALLOW_PRIVATE_ENDPOINTS", "1")
    monkeypatch.setenv("MELEE_WEBHOOK_URLS", url)
    monkeypatch.setenv("MELEE_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("MELEE_WEBHOOK_TIMEOUT", "2")
    reset_settings()
    _Receiver.received.clear()
    _Receiver.fail_first = 0
    yield url
    server.shutdown()
    # 等线程真正退出再返回：shutdown() 只是让 serve_forever 停止接受新请求，
    # 已在处理的请求（含异步投递的迟到请求）还会往类属性里写一条。
    # 不清掉的话下个测试会读到上一条，表现为随机失败（实测出现一次）。
    thread.join(timeout=5)
    _Receiver.received.clear()


def _wait_ended(client, match_id, timeout_s: float = 60.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        events = client.get("/matches/" + match_id + "/events").json()["events"]
        if any(e["type"] == "match.ended" for e in events):
            return
        time.sleep(0.5)
    raise AssertionError("match never ended")


def _create_match(client):
    return client.post(
        "/matches",
        json={"game": "tic-tac-toe", "seed": 3, "players": [{"id": "A"}, {"id": "B"}]},
    ).json()["matchId"]


def test_webhook_delivers_signed_match_ended(hook_url, client):
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)
    assert results and results[0]["delivered"] is True
    assert _Receiver.received, "receiver must be called"

    record = _Receiver.received[-1]
    payload = json.loads(record["raw"])
    assert payload["event"] == "match.ended"
    assert payload["matchId"] == match_id
    assert record["event"] == "match.ended"
    expected = hmac.new(
        b"hook-secret",
        record["timestamp"].encode() + b"." + record["raw"],
        hashlib.sha256,
    ).hexdigest()
    assert record["signature"] == "sha256=" + expected


def test_webhook_retries_on_500(hook_url, client):
    _Receiver.fail_first = 1
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)
    assert results[0]["delivered"] is True
    assert len(_Receiver.received) >= 2, "must retry after a 500"


def test_webhook_skips_loopback_without_allowlist(hook_url, client, monkeypatch):
    """回环防护：默认拒绝 loopback 目标，显式放行才发送。"""
    monkeypatch.delenv("MELEE_ALLOW_PRIVATE_ENDPOINTS")
    reset_settings()
    assert webhooks._targets() == []
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)
    assert results == []
    assert _Receiver.received == []


def test_webhook_silent_without_targets(client, monkeypatch):
    monkeypatch.delenv("MELEE_WEBHOOK_URLS", raising=False)
    reset_settings()
    match_id = _create_match(client)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)
    assert results == []


def test_webhook_targets_reject_metadata_and_bad_urls(monkeypatch):
    """webhook 目标走共享 SSRF 校验：元数据地址与坏 URL 被拒收。"""
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_WEBHOOK_URLS",
                       "http://metadata.google.internal/hook, not-a-url")
    reset_settings()
    assert webhooks._targets() == []


def test_webhook_async_dispatch_offloads_thread(hook_url, client):
    """async_mode=True（默认）必须立刻返回且真实送达（等接收端收到）。"""
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=True)
    assert results[0]["dispatched"] is True
    import time as _t
    deadline = _t.time() + 10
    while _t.time() < deadline and not _Receiver.received:
        _t.sleep(0.2)
    assert _Receiver.received

# ---- 投递日志（1.9.2）----

def test_delivery_record_persists_and_is_queryable(hook_url, client):
    """一次成功投递要落库，并能从 API 读回来。

    旧实现只写服务器日志：进程重启就没了，后台与 CLI 都看不到，
    "昨天那个 webhook 为什么失败"只能靠翻日志文件。
    """
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)

    response = client.get(f"/webhooks/deliveries?match_id={match_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total"] >= 1
    assert data["items"], "投递记录为空"
    item = data["items"][0]
    assert item["matchId"] == match_id
    assert item["status"] == "ok"
    assert item["httpCode"] == 200
    # 不存请求体全文：只存摘要，避免把比赛数据复制到第二个地方
    assert len(item["payloadSha"]) == 16


def test_failed_delivery_records_the_reason(hook_url, client, monkeypatch):
    """失败要记录可诊断的原因，而不是只有一个 False。"""
    _Receiver.fail_first = 99  # 一直返回 500
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    results = webhooks.dispatch(client.app.state.db_path, match_id, async_mode=False)
    assert results[0]["delivered"] is False

    data = client.get(f"/webhooks/deliveries?match_id={match_id}").json()
    item = data["items"][0]
    assert item["status"] == "http_error"
    assert item["httpCode"] == 500
    assert "http 500" in item["error"]
    assert item["attempts"] == 3, "应记录真实尝试次数（1 次 + 2 次重试）"
    assert data["summary"]["failed"] >= 1
    assert data["summary"]["lastFailureAt"]
    _Receiver.fail_first = 0


def test_redispatch_resends_and_records(hook_url, client):
    """手动重发：接收方修好后能补投，且同样留下记录。"""
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    before = len(_Receiver.received)

    response = client.post("/webhooks/redispatch", json={"matchId": match_id})
    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["delivered"] is True
    assert len(_Receiver.received) > before, "重发没有真的调用接收方"

    data = client.get(f"/webhooks/deliveries?match_id={match_id}").json()
    assert data["summary"]["total"] >= 2, "重发也要留下记录"


def test_redispatch_without_targets_explains_why(client, monkeypatch):
    """没有配置目标时要明确告知，而不是返回空结果让人猜。"""
    monkeypatch.delenv("MELEE_WEBHOOK_URLS", raising=False)
    reset_settings()
    match_id = _create_match(client)
    _wait_ended(client, match_id)
    response = client.post("/webhooks/redispatch", json={"matchId": match_id})
    assert response.status_code == 409
    assert "MELEE_WEBHOOK_URLS" in response.json()["detail"]


def test_redispatch_requires_a_match_id(client):
    response = client.post("/webhooks/redispatch", json={})
    assert response.status_code == 422
    assert "matchId" in response.json()["detail"]


def test_delivery_log_is_bounded(tmp_path):
    """投递记录必须有上限：长期运行不能无限增长。"""
    import sqlite3

    from services.match import webhook_log

    db = tmp_path / "log.db"
    body = b'{"x":1}'
    for index in range(webhook_log.MAX_ROWS + 40):
        webhook_log.record(db, f"m_{index}", body, [
            webhook_log.Delivery(url="https://example.test/hook", status="ok", http_code=200)
        ])

    conn = sqlite3.connect(db)
    try:
        count = conn.execute("SELECT COUNT(*) FROM webhook_deliveries").fetchone()[0]
    finally:
        conn.close()
    assert count == webhook_log.MAX_ROWS, count
    # 保留的是最近的：最后一条必须在
    rows = webhook_log.recent(db, limit=1)
    assert rows[0]["matchId"] == f"m_{webhook_log.MAX_ROWS + 39}"

