"""切片五：turnTimeoutMs 的真实强制。

- 不设置：行为与旧行为一致（deadline 只写事件，不执行）。
- 设置：慢选手被墙钟打断 → action.rejected + 弃权判负，对手获胜。
- API 层：非法值 422；合法值随 config 落进 match.created。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.match.engine.twenty_questions import TwentyQuestions
from services.match.runner import Runner, TurnTimeout


class _SlowHandler(BaseHTTPRequestHandler):
    """每次请求都睡 5s 再回：给 worker 的墙钟留出触发窗口。

    必须显式 Connection: close：HTTP/1.1 的 keep-alive 会让 serve_forever
    停在 select 等下一个请求，shutdown() 永远等不到 handler 返回。
    """

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("content-length", 0))
        self.rfile.read(length)
        time.sleep(5.0)
        body = json.dumps({"move": "ask:place=城市"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def log_message(self, *_args) -> None:
        return


@pytest.fixture()
def server_slow_http():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/act"
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()


class _Log:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.next_seq = 0

    def append(self, type_: str, payload: dict, **kw) -> dict:
        event = {"type": type_, "payload": payload, **kw}
        self.events.append(event)
        self.next_seq += 1
        return event

    def mark_ended(self) -> None:
        pass


class _Output:
    def __init__(self, move=None, raw: str = "") -> None:
        from services.match.engine.base import Move

        self.move = move or Move(kind="ask", value="place=城市")
        self.raw = raw
        self.elapsed_ms = 1


class _SlowAgent:
    """sleep 超过任何测试 deadline 的选手。"""

    name = "slow"

    def __init__(self, sleep_s: float = 30.0) -> None:
        self._sleep_s = sleep_s

    def act(self, observation, legal, on_thought=None):
        time.sleep(self._sleep_s)
        return _Output()


class _FastAgent:
    name = "fast"

    def act(self, observation, legal, on_thought=None):
        return _Output()


def _state(seed: int = 7):
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={"maxQuestions": 4})
    return game, state


class TestEnforceDeadline:
    def test_slow_agent_times_out_and_forfeits(self):
        """超时 → action.rejected（reason=回合超时）→ 弃权，对手胜。"""
        game, state = _state()
        log = _Log()
        runner = Runner(
            game, log,
            {"A": _FastAgent(), "B": _SlowAgent(30.0)},
            deadline_ms=200,
            enforce_deadline=True,
        )
        result = runner.play(state)
        assert result.winner == "A"
        types = [e["type"] for e in log.events]
        assert "action.rejected" in types
        rejected = next(e for e in log.events if e["type"] == "action.rejected")
        assert "超时" in rejected["payload"]["reason"]
        assert rejected["payload"]["retryable"] is False
        ended = next(e for e in log.events if e["type"] == "match.ended")
        assert ended["payload"]["winner"] == "A"

    def test_timeout_event_chain_matches_forfeit_path(self):
        """超时弃权写出的 action.applied 是 forfeit（与非法路径同构）。"""
        game, state = _state()
        log = _Log()
        runner = Runner(
            game, log,
            {"A": _SlowAgent(30.0), "B": _FastAgent()},
            deadline_ms=150,
            enforce_deadline=True,
        )
        runner.play(state)
        applied = [
            e for e in log.events
            if e["type"] == "action.applied" and e.get("player") == "A"
        ]
        assert applied and applied[-1]["payload"]["action"]["kind"] == "forfeit"

    def test_turn_started_carries_real_deadline(self):
        """设置了 turnTimeoutMs 时 turn.started 写真实值。"""
        game, state = _state()
        log = _Log()
        Runner(
            game, log,
            {"A": _FastAgent(), "B": _FastAgent()},
            deadline_ms=4500,
            enforce_deadline=True,
        ).play(state)
        started = [e for e in log.events if e["type"] == "turn.started"]
        assert started and all(e["payload"]["deadlineMs"] == 4500 for e in started)

    def test_disabled_deadline_keeps_old_behavior(self):
        """不强制时：慢选手也能跑完（等待不设上限），deadline 只进事件。"""
        game, state = _state()
        log = _Log()
        runner = Runner(
            game, log,
            {"A": _SlowAgent(0.4), "B": _FastAgent()},
            deadline_ms=100,
            enforce_deadline=False,
        )
        result = runner.play(state)
        started = [e for e in log.events if e["type"] == "turn.started"]
        assert started and started[0]["payload"]["deadlineMs"] == 100
        # 没有因超时产生的弃权。
        assert not any(
            e["type"] == "action.rejected" and "超时" in str(e["payload"].get("reason"))
            for e in log.events
        )
        assert result.endReason != "回合超时"

    def test_http_agent_receives_deadline_when_enforced(self):
        from services.match.agents.http_agent import HTTPAgent, HTTPAgentConfig

        captured: list[int | None] = []

        class _ProbeHTTPAgent(HTTPAgent):
            def set_deadline(self, deadline_ms):
                captured.append(deadline_ms)
                super().set_deadline(deadline_ms)

        game, state = _state()
        log = _Log()
        agent = _ProbeHTTPAgent("A", HTTPAgentConfig(endpoint="http://127.0.0.1:1/act"))
        Runner(
            game, log,
            {"A": agent, "B": _FastAgent()},
            deadline_ms=1000,
            enforce_deadline=True,
        ).play(state)
        assert captured and captured[0] == 1000

    def test_http_agent_not_notified_without_enforce(self):
        from services.match.agents.http_agent import HTTPAgent, HTTPAgentConfig

        captured: list[int | None] = []

        class _ProbeHTTPAgent(HTTPAgent):
            def set_deadline(self, deadline_ms):
                captured.append(deadline_ms)
                super().set_deadline(deadline_ms)

        game, state = _state()
        log = _Log()
        agent = _ProbeHTTPAgent("A", HTTPAgentConfig(endpoint="http://127.0.0.1:1/act"))
        Runner(
            game, log,
            {"A": agent, "B": _FastAgent()},
            deadline_ms=30000,
            enforce_deadline=False,
        ).play(state)
        assert captured == []

    def test_on_thought_after_timeout_does_not_duplicate_action(self):
        """超时后孤儿线程可能再补一条思考，但绝不会再写 action。"""
        game, state = _state()
        log = _Log()
        Runner(
            game, log,
            {"A": _FastAgent(), "B": _SlowAgent(30.0)},
            deadline_ms=150,
            enforce_deadline=True,
        ).play(state)
        proposed = [e for e in log.events if e["type"] == "action.proposed" and e.get("player") == "B"]
        assert len(proposed) <= 1


class TestWorkerConfigPlumbing:
    def test_turn_timeout_of_reads_config(self):
        from services.match.worker import _turn_timeout_of

        assert _turn_timeout_of({"config": {"turnTimeoutMs": 5000}}) == 5000
        assert _turn_timeout_of({"config": {}}) is None
        assert _turn_timeout_of({}) is None
        assert _turn_timeout_of({"config": {"turnTimeoutMs": 0}}) is None

    def test_prepare_strips_turn_timeout_from_game_config(self, tmp_path):
        from services.match.worker import _prepare

        db_path = tmp_path / "m.db"
        payload = {
            "game": "twenty-questions",
            "seed": 7,
            "players": ["A", "B"],
            "config": {"turnTimeoutMs": 5000, "maxQuestions": 4},
        }
        _game, state = _prepare(db_path, payload, "m_tt", ["A", "B"])
        assert "turnTimeoutMs" not in (state.get("config") or {})


class TestApiContract:
    def test_invalid_turn_timeout_is_422(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "players": [{"id": "A"}, {"id": "B"}],
                "config": {"turnTimeoutMs": 999},
                "inlineWorker": False,
            },
        )
        assert response.status_code == 422

    def test_valid_turn_timeout_creates_match(self, client):
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "seed": 7,
                "maxQuestions": 4,
                "players": [{"id": "A"}, {"id": "B"}],
                "config": {"turnTimeoutMs": 5000},
                "inlineWorker": False,
            },
        )
        assert response.status_code == 201, response.text
        job_id = response.json()["jobId"]
        job = client.get(f"/jobs/{job_id}").json()
        payload = job["payload"] if "payload" in job else job.get("job", {}).get("payload", {})
        assert payload["config"].get("turnTimeoutMs") == 5000

    def test_timeout_enforced_end_to_end_with_slow_http(self, local_client, server_slow_http):
        """全链路：慢 HTTP agent → worker 墙钟强制 → 对手获胜。"""
        response = local_client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "seed": 7,
                "maxQuestions": 4,
                "players": [
                    {"id": "A", "http": {"endpoint": server_slow_http, "timeoutS": 60}},
                    {"id": "B"},
                ],
                "config": {"turnTimeoutMs": 1000},
            },
        )
        assert response.status_code == 201, response.text
        match_id = response.json()["matchId"]
        deadline = time.monotonic() + 30
        events: list[dict] = []
        winner = None
        while time.monotonic() < deadline:
            events = local_client.get(f"/matches/{match_id}/events").json()["events"]
            ended = next((e for e in events if e["type"] == "match.ended"), None)
            if ended:
                winner = ended["payload"]["winner"]
                break
            time.sleep(0.1)
        assert winner == "B", f"比赛没有按超时弃权收尾：{events[-3:] if events else events}"
