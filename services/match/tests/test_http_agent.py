"""外部 AI 接入：任意 HTTP 端点参赛，安全与公平边界与内置选手一致。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


@pytest.fixture(autouse=True)
def _deterministic_dns(monkeypatch):
    """把 DNS 钉成确定公网地址：本机 fake-IP 隧道会把公网域名解析进
    保留网段（198.18.0.0/15），SSRF 校验随之拒绝合法 baseUrl，
    纯契约测试因此随机红。SSRF 语义本身由 test_resilience 专项负责。
    """
    import socket as _socket

    from services.match import outbound as _outbound

    _real = _socket.getaddrinfo

    def _public(host, *args, **kwargs):
        # 回环与私有地址照常解析：很多测试起本机 HTTP 服务。
        text = str(host)
        if text in {"127.0.0.1", "::1", "localhost"} or text.startswith("127."):
            return _real(host, *args, **kwargs)
        return _real("93.184.216.34", *args, **kwargs)

    monkeypatch.setattr(_outbound.socket, "getaddrinfo", _public)


from services.match.agents.http_agent import HTTPAgent, HTTPAgentConfig, _parse_response
from services.match.engine.base import Move
from services.match.player_specs import PlayerSpec


class _Recorder(BaseHTTPRequestHandler):
    """记录收到的请求，并按预设返回。"""

    received: list[dict] = []
    reply: dict = {"move": "[4]"}
    status: int = 200

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Recorder.received.append({"body": body, "headers": dict(self.headers)})
        out = json.dumps(_Recorder.reply).encode()
        self.send_response(_Recorder.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args) -> None:  # 静音
        return


@pytest.fixture()
def server():
    _Recorder.received = []
    _Recorder.reply = {"move": "[4]"}
    _Recorder.status = 200
    httpd = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/act"
    finally:
        httpd.shutdown()
        thread.join(timeout=3)


def _obs(**over):
    base = {
        "you": "A",
        "opponent": ["B"],
        "isYourTurn": True,
        "observations": ["board"],
        "turnIndex": 2,
    }
    base.update(over)
    return base


def test_http_agent_returns_move(server):
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    output = agent.act(_obs(), ["[4]", "[5]"])
    assert output.move == Move(kind="guess", value="[4]")
    assert output.elapsed_ms is not None


def test_http_agent_forwards_only_observation_fields(server):
    """请求体里只能有 observe() 给的字段，不能夹带引擎内部状态。"""
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    agent.act(_obs(gameState={"board": [["", "", ""]]}), ["[4]"])
    body = _Recorder.received[0]["body"]
    assert set(body) <= {
        "you", "opponent", "isYourTurn", "observations", "legalMoves",
        "turnIndex", "phase", "gameState", "aspects", "candidates",
    }
    assert "target" not in body
    assert "secretColors" not in body


def test_http_agent_sends_api_key_as_bearer(server):
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server, api_key="sekret"))
    agent.act(_obs(), ["[4]"])
    headers = {k.lower(): v for k, v in _Recorder.received[0]["headers"].items()}
    assert headers.get("authorization") == "Bearer sekret"


def test_http_agent_failure_becomes_illegal_move(server):
    _Recorder.status = 500
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server, max_retries=0))
    output = agent.act(_obs(), ["[4]"])
    assert output.move.kind == "ask" and output.move.value == ""
    assert "[http-500]" in output.raw
    assert agent.failures == 1


def test_http_agent_accepts_kind_value_format(server):
    _Recorder.reply = {"kind": "guess", "value": "[5]", "thought": "因为..."}
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    thoughts: list[str] = []
    output = agent.act(_obs(), ["[5]"], on_thought=thoughts.append)
    assert output.move.value == "[5]"
    assert thoughts == ["因为..."]


def test_http_agent_does_not_guess_intent():
    """解析不出来就是非法动作，绝不替外部 AI 猜。"""
    move, _ = _parse_response({"action": "我觉得应该走中间"}, ["[4]"], 4000)
    assert move.kind == "ask" and move.value == ""


def test_http_agent_accepts_bare_legal_move():
    """正文恰好等于某个合法动作时接受（与 LLM 选手同一条规则）。"""
    move, _ = _parse_response({"text": "[4]"}, ["[4]", "[5]"], 4000)
    assert move == Move(kind="guess", value="[4]")


def test_http_agent_rejects_non_object_json(server):
    _Recorder.reply = ["not", "an", "object"]
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server, max_retries=0))
    output = agent.act(_obs(), ["[4]"])
    assert "[bad-response]" in output.raw


def test_http_agent_truncates_huge_response(server):
    _Recorder.reply = {"move": "[4]", "thought": "x" * 10000}
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server, max_response_chars=100))
    output = agent.act(_obs(), ["[4]"])
    assert len(output.raw) <= 100


def test_http_spec_rejects_private_endpoint():
    """与 BYOK 同一套出站策略：私网地址必须被拒。"""
    with pytest.raises(ValueError, match="回环|私有"):
        PlayerSpec(id="A", http={"endpoint": "http://127.0.0.1:9000/act"})


def test_http_spec_is_repr_safe():
    """Key 不能出现在 repr 里（日志会打 repr）。"""
    spec = PlayerSpec(id="A", http={"endpoint": "https://example.com/act", "apiKey": "topsecret"})
    assert "topsecret" not in repr(spec)


def test_http_agent_payload_omits_deadline_by_default(server):
    """不设置 turnTimeoutMs 时 payload 不带 deadlineMs（行为不变）。"""
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    agent.act(_obs(), ["[4]"])
    assert "deadlineMs" not in _Recorder.received[0]["body"]


def test_http_agent_payload_carries_deadline_when_set(server):
    """turnTimeoutMs 设置后，外部 agent 能从 payload 读到本手墙钟。"""
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    agent.set_deadline(5000)
    agent.act(_obs(), ["[4]"])
    assert _Recorder.received[0]["body"]["deadlineMs"] == 5000


def test_http_agent_set_deadline_none_clears(server):
    agent = HTTPAgent("A", HTTPAgentConfig(endpoint=server))
    agent.set_deadline(5000)
    agent.set_deadline(None)
    agent.act(_obs(), ["[4]"])
    assert "deadlineMs" not in _Recorder.received[0]["body"]
