"""缺口补齐测试：能力面新增分支的边界行为。

覆盖点（对应 coverage 门禁缺口）：
- runner：回合代际闸门（超时孤儿线程不得再写 thought.delta）、_announce_deadline、
  enforce_deadline 时 match.created.config、director/narrator 空值分支。
- llm：采样参数全字段下发、stop 转原生协议字段、native usage 换算、
  systemPrompt 无"只输出"锚点时的兜底、budget<=0 关闭预算。
"""

from __future__ import annotations

import threading
import time

import pytest

from services.match.agents.llm import LLMConfig, _apply_system_prompt, _gemini_request, _native_content
from services.match.engine.base import Move
from services.match.engine.twenty_questions import TwentyQuestions
from services.match.player_specs import PacedAgent
from services.match.runner import Runner


class _Log:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, type_, payload, **kw):
        event = {"type": type_, "payload": payload, **kw}
        self.events.append(event)
        return event

    def mark_ended(self):
        pass


class _Out:
    def __init__(self, move=None, raw="", usage=None):
        self.move = move or Move(kind="ask", value="place=城市")
        self.raw = raw
        self.elapsed_ms = 1
        self.usage = usage


def _state(seed=7):
    game = TwentyQuestions()
    return game, game.init(seed=seed, players=["A", "B"], config={"maxQuestions": 4})


class TestTurnGenerationGate:
    """超时后的孤儿线程不能再往日志里补思考。"""

    def test_late_thought_after_timeout_is_dropped(self):
        release = threading.Event()
        collected: list[str] = []

        class _LateAgent:
            name = "late"

            def act(self, observation, legal, on_thought=None):
                def emit():
                    release.wait(5)
                    on_thought("迟到的思考")
                    collected.append("called")

                threading.Thread(target=emit, daemon=True).start()
                time.sleep(30)
                return _Out()

        class _Fast:
            name = "fast"

            def act(self, observation, legal, on_thought=None):
                return _Out()

        game, state = _state()
        log = _Log()
        Runner(game, log, {"A": _Fast(), "B": _LateAgent()}, deadline_ms=120, enforce_deadline=True).play(state)
        ended = len(log.events)
        release.set()
        time.sleep(0.3)
        # 回调确实被调用过（collected 有值），但事件没有增加。
        assert collected == ["called"]
        assert len(log.events) == ended
        assert not any(e["type"] == "thought.delta" and "迟到" in e["payload"]["text"] for e in log.events)

    def test_empty_thought_is_ignored(self):
        class _Noisy:
            name = "noisy"

            def act(self, observation, legal, on_thought=None):
                on_thought("")
                return _Out()

        game, state = _state()
        log = _Log()
        Runner(game, log, {"A": _Noisy(), "B": _Noisy()}).play(state)
        assert not any(e["type"] == "thought.delta" for e in log.events)


class TestDeadlinePlumbing:
    def test_announce_deadline_skips_agents_without_setter(self):
        class _Plain:
            name = "plain"

            def act(self, observation, legal, on_thought=None):
                return _Out()

        game, state = _state()
        # 没有 set_deadline 的选手不该让 _announce_deadline 抛错。
        result = Runner(game, _Log(), {"A": _Plain(), "B": _Plain()}, deadline_ms=1000, enforce_deadline=True).play(state)
        assert result is not None

    def test_match_created_records_turn_timeout(self):
        class _Plain:
            name = "plain"

            def act(self, observation, legal, on_thought=None):
                return _Out()

        game, state = _state()
        log = _Log()
        Runner(game, log, {"A": _Plain(), "B": _Plain()}, deadline_ms=4321, enforce_deadline=True).play(state)
        created = next(e for e in log.events if e["type"] == "match.created")
        assert created["payload"]["config"]["turnTimeoutMs"] == 4321

    def test_match_created_omits_turn_timeout_when_disabled(self):
        class _Plain:
            name = "plain"

            def act(self, observation, legal, on_thought=None):
                return _Out()

        game, state = _state()
        log = _Log()
        Runner(game, log, {"A": _Plain(), "B": _Plain()}, enforce_deadline=False).play(state)
        created = next(e for e in log.events if e["type"] == "match.created")
        assert "turnTimeoutMs" not in created["payload"]["config"]


class TestPacedAgentDelegation:
    def test_forwards_deadline_and_history(self):
        class _Inner:
            name = "inner"

            def __init__(self):
                self.deadlines = []
                self.history = []

            def set_deadline(self, value):
                self.deadlines.append(value)

            def record_history(self, action, thought=""):
                self.history.append((action, thought))

            def act(self, observation, legal, on_thought=None):
                return _Out()

        inner = _Inner()
        agent = PacedAgent(inner, 0)
        agent.set_deadline(1234)
        agent.record_history("place=城市", "先缩小")
        agent.act({}, [])
        assert inner.deadlines == [1234]
        assert inner.history == [("place=城市", "先缩小")]


class TestSystemPromptFallback:
    def test_uses_fallback_constraint_when_no_anchor(self):
        out = _apply_system_prompt("没有格式锚点的系统提示", "我的自定义人格")
        assert out.startswith("我的自定义人格")
        assert "JSON" in out

    def test_blank_custom_prompt_returns_default(self):
        assert _apply_system_prompt("原始系统提示", "   ") == "原始系统提示"

    def test_keeps_from_last_anchor(self):
        default = "人格段\n只输出 JSON A\n只输出 JSON B"
        out = _apply_system_prompt(default, "自定义")
        assert out == "自定义\n\n只输出 JSON B"


class TestNativeProtocolBodies:
    def test_gemini_request_carries_sampling(self):
        config = LLMConfig(base_url="https://x", api_key="k", model="m", top_p=0.5, stop=["END"], temperature=0.3)
        body = _gemini_request(config, [
            {"role": "system", "content": "规则"},
            {"role": "user", "content": "问题"},
        ])
        assert body["generationConfig"]["topP"] == 0.5
        assert body["generationConfig"]["stopSequences"] == ["END"]
        assert body["generationConfig"]["temperature"] == 0.3
        assert body["systemInstruction"]["parts"][0]["text"] == "规则"

    def test_gemini_request_without_system(self):
        config = LLMConfig(base_url="https://x", api_key="k", model="m")
        body = _gemini_request(config, [{"role": "user", "content": "问题"}])
        assert "systemInstruction" not in body

    def test_native_content_anthropic_usage(self):
        text, thought, usage = _native_content("anthropic", {
            "content": [
                {"type": "thinking", "thinking": "推理"},
                {"type": "text", "text": "答案"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })
        assert text == "答案"
        assert thought == "推理"
        assert usage["total_tokens"] == 15

    def test_native_content_gemini_usage(self):
        text, thought, usage = _native_content("gemini", {
            "candidates": [{"content": {"parts": [{"text": "答案", "thought": True}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
        })
        assert text == "答案"
        assert thought == "答案"
        assert usage["total_tokens"] == 10

    def test_native_content_missing_usage_fields(self):
        _text, _thought, usage = _native_content("anthropic", {"content": []})
        assert usage["requests"] == 1
        assert "total_tokens" not in usage


class TestBudgetOff:
    def test_zero_budget_never_raises(self):
        from services.match.agents.llm import LLMAgent

        agent = LLMAgent("A", LLMConfig(base_url="https://x", api_key="k", model="m", max_total_tokens=0))
        agent._ensure_budget()  # 不应抛 LLMBudgetExceeded


class TestSamplingPayload:
    def test_all_optional_params_present_in_payload(self, monkeypatch):
        from services.match.agents import llm as llm_mod

        captured: list[dict] = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": '{"kind":"ask","value":"place=城市"}'}}],
                        "usage": {"total_tokens": 5}}

        class _Client:
            def post(self, url, json=None, headers=None, timeout=None):
                captured.append(json)
                return _Resp()

        agent = llm_mod.LLMAgent("A", LLMConfig(
            base_url="https://x", api_key="k", model="m",
            top_p=0.8, seed=7, stop=["X"], frequency_penalty=0.1, presence_penalty=0.2,
        ))
        agent._client = _Client()
        game, state = _state()
        agent.act(game.observe(state, "A"), game.legal_moves(state, "A"))
        payload = captured[0]
        assert payload["top_p"] == 0.8
        assert payload["seed"] == 7
        assert payload["stop"] == ["X"]
        assert payload["frequency_penalty"] == 0.1
        assert payload["presence_penalty"] == 0.2

    def test_success_raw_redacts_secret_but_keeps_action_parse(self):
        from services.match.agents import llm as llm_mod

        game, state = _state()
        legal = game.legal_moves(state, "A")
        value = legal[0].split(":", 1)[1]

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [{"message": {"content":
                        '{"kind":"ask","value":"' + value + '","thought":"apiKey=sk-secret-value"}'}}],
                    "usage": {"total_tokens": 5},
                }

        class _Client:
            def post(self, url, json=None, headers=None, timeout=None):
                return _Resp()

        agent = llm_mod.LLMAgent("A", LLMConfig(base_url="https://x", api_key="k", model="m"))
        agent._client = _Client()
        output = agent.act(game.observe(state, "A"), legal)
        assert output.move.value == value
        assert "sk-secret-value" not in output.raw
        assert "***" in output.raw

    def test_absent_params_not_in_payload(self):
        from services.match.agents import llm as llm_mod

        captured: list[dict] = []

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": '{"kind":"ask","value":"place=城市"}'}}],
                        "usage": {"total_tokens": 5}}

        class _Client:
            def post(self, url, json=None, headers=None, timeout=None):
                captured.append(json)
                return _Resp()

        agent = llm_mod.LLMAgent("A", LLMConfig(base_url="https://x", api_key="k", model="m"))
        agent._client = _Client()
        game, state = _state()
        agent.act(game.observe(state, "A"), game.legal_moves(state, "A"))
        for key in ("top_p", "seed", "stop", "frequency_penalty", "presence_penalty"):
            assert key not in captured[0]
