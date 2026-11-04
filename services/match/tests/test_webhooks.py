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
