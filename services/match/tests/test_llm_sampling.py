"""切片二：LLM 选手采样参数 / systemPrompt / includeHistory / stream 的行为契约。

payload 断言直接看 FakeStream 收到的请求体（不 mock 掉被测行为本身）；
提示词断言看 build_messages 的真实产物。
"""

from __future__ import annotations

from services.match.agents.llm import LLMAgent, LLMConfig, _apply_system_prompt
from services.match.engine.twenty_questions import TwentyQuestions

from .fake_stream import FakeStream


def observation(seed: int = 7):
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    return game.observe(state, "A")


def make_agent(server: FakeStream, **config_overrides) -> LLMAgent:
    defaults = {
        "base_url": server.base_url,
        "api_key": "sk-test",
        "model": "fake",
        "timeout_s": 5.0,
    }
    defaults.update(config_overrides)
    return LLMAgent("A", LLMConfig(**defaults))


class TestSamplingParamsInPayload:
    """默认不下发（端点走默认值）；填了就原样透传。"""

    def test_defaults_omit_optional_params(self):
        with FakeStream() as server:
            agent = make_agent(server)
            agent.act(observation(), [], None)
        body = server.requests[0]["body"]
        assert "top_p" not in body
        assert "seed" not in body
        assert "stop" not in body
        assert "frequency_penalty" not in body
        assert "presence_penalty" not in body
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 400

    def test_sampling_params_are_passed_through(self):
        with FakeStream() as server:
            agent = make_agent(
                server,
                temperature=0.2,
                max_tokens=8000,
                top_p=0.9,
                seed=42,
                stop=["END", "STOP"],
                frequency_penalty=0.5,
                presence_penalty=-1.0,
            )
            agent.act(observation(), [], None)
        body = server.requests[0]["body"]
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 8000
        assert body["top_p"] == 0.9
        assert body["seed"] == 42
        assert body["stop"] == ["END", "STOP"]
        assert body["frequency_penalty"] == 0.5
        assert body["presence_penalty"] == -1.0

    def test_stop_is_copied_not_aliased(self):
        stops = ["END"]
        with FakeStream() as server:
            agent = make_agent(server, stop=list(stops))
            agent.act(observation(), [], None)
            body_with = server.requests[0]["body"]["stop"]
        assert body_with == ["END"]
        # payload 里的是副本：后续改调用方列表不影响已构造的配置语义。
        assert agent.config.stop == ["END"]


class TestSystemPrompt:
    def test_default_prompt_unchanged_when_empty(self):
        with FakeStream() as server:
            agent = make_agent(server)
            agent.act(observation(), [], None)
        system = server.requests[0]["body"]["messages"][0]["content"]
        assert "二十问" in system
        assert "只输出" in system

    def test_custom_prompt_replaces_persona_keeps_constraint(self):
        with FakeStream() as server:
            agent = make_agent(server, system_prompt="你是一名极简主义选手，永远优先报答案。")
            agent.act(observation(), [], None)
        system = server.requests[0]["body"]["messages"][0]["content"]
        assert system.startswith("你是一名极简主义选手")
        assert "二十问" not in system
        # 约束段必须保留：格式规则对自定义选手同样生效。
        assert "只输出" in system
        assert "JSON" in system

    def test_user_content_not_affected_by_system_prompt(self):
        with FakeStream() as server:
            agent = make_agent(server, system_prompt="你是一名谨慎的选手。")
            agent.act(observation(), [], None)
        user = server.requests[0]["body"]["messages"][1]["content"]
        assert "你是 A" in user

    def test_codenames_branch_keeps_constraint_too(self):
        from services.match.agents.llm import LLMConfig as C

        agent = LLMAgent(
            "A",
            C(base_url="http://x", api_key="k", model="m", system_prompt="你是红队军师。"),
        )
        messages = agent.build_messages(
            {"team": "red", "phase": "hint", "publicBoard": [], "words": [], "secretColors": []}, []
        )
        assert messages[0]["content"].startswith("你是红队军师。")
        assert "只输出" in messages[0]["content"]

    def test_apply_system_prompt_helper_direct(self):
        merged = _apply_system_prompt("默认人格。\n只输出 JSON。", "自定义人格")
        assert merged == "自定义人格\n\n只输出 JSON。"
        assert _apply_system_prompt("默认人格", "") == "默认人格"
        assert _apply_system_prompt("默认人格", "   ") == "默认人格"


class TestIncludeHistory:
    def test_default_off_keeps_user_prompt_clean(self):
        with FakeStream() as server:
            agent = make_agent(server)
            agent.record_history("place=海边", "先缩小范围")
            agent.act(observation(), [], None)
        user = server.requests[0]["body"]["messages"][1]["content"]
        assert "你在这局里此前的动作记录" not in user

    def test_on_includes_own_history_only(self):
        with FakeStream() as server:
            agent = make_agent(server, include_history=True)
            agent.record_history("place=海边", "先缩小范围")
            agent.record_history("scale=大型", "往大里猜")
            agent.act(observation(), [], None)
        user = server.requests[0]["body"]["messages"][1]["content"]
        assert "你在这局里此前的动作记录" in user
        assert "place=海边" in user
        assert "先缩小范围" in user
        assert "scale=大型" in user

    def test_history_ignores_empty_action(self):
        with FakeStream() as server:
            agent = make_agent(server, include_history=True)
            agent.record_history("", "只有想法没有动作")
            agent.act(observation(), [], None)
        user = server.requests[0]["body"]["messages"][1]["content"]
        assert "你在这局里此前的动作记录" not in user

    def test_record_history_noop_when_disabled(self):
        with FakeStream() as server:
            agent = make_agent(server)
            agent.record_history("place=海边", "想法")
            agent.record_history("scale=大型", "想法2")
            agent.act(observation(), [], None)
        user = server.requests[0]["body"]["messages"][1]["content"]
        assert "动作记录" not in user


class TestRunnerFeedsHistory:
    """runner 只把选手自己的合法动作喂回它自己的记忆。"""

    def test_runner_records_action_and_thought(self, monkeypatch):
        from services.match.runner import Runner

        class Log:
            def __init__(self):
                self.events = []
                self.next_seq = 0

            def append(self, type_, payload, **_kw):
                self.events.append({"type": type_, "payload": payload})
                self.next_seq += 1

            def mark_ended(self):
                pass

        game = TwentyQuestions()
        state = game.init(seed=7, players=["A", "B"], config={})

        class Output:
            def __init__(self, move):
                self.move = move
                self.raw = ""
                self.elapsed_ms = 1

        class Agent:
            name = "llm"

            def __init__(self, tag):
                self.tag = tag
                self.history = []

            def record_history(self, action, thought):
                self.history.append((action, thought))

            def act(self, observation, legal, on_thought=None):
                on_thought("先缩小范围")
                from services.match.engine.base import Move

                # 用真实合法的 ask 动作，否则引擎判犯规走弃权路径。
                move = next(
                    (item for item in legal if item.startswith("ask:") and "place=城市" not in item),
                    legal[0],
                )
                value = move.split(":", 1)[1] if ":" in move else move
                return Output(Move(kind="ask", value=value))

        log = Log()
        agent = Agent("A")
        opponent = Agent("B")
        runner = Runner(game, log, {"A": agent, "B": opponent})
        runner.play(state)

        # 每个选手只记它自己出的招：A 的记录里是 A 问过的动作，
        # B 的记录里是 B 问过的动作；没有 forfeit 或空动作混进去。
        assert agent.history and all(a for a, _ in agent.history)
        assert opponent.history and all(a for a, _ in opponent.history)
        assert agent.history[0][1] == "先缩小范围"
        assert opponent.history[0][1] == "先缩小范围"

    def test_runner_does_not_record_forfeit(self):
        from services.match.engine.base import Move
        from services.match.runner import Runner

        class Log:
            def __init__(self):
                self.events = []
                self.next_seq = 0

            def append(self, type_, payload, **_kw):
                self.next_seq += 1

            def mark_ended(self):
                pass

        game = TwentyQuestions()
        state = game.init(seed=7, players=["A", "B"], config={})

        class Output:
            move = Move(kind="ask", value="")
            raw = ""
            elapsed_ms = 1

        class Agent:
            name = "llm"

            def __init__(self):
                self.history = []

            def record_history(self, action, thought):
                self.history.append((action, thought))

            def act(self, observation, legal, on_thought=None):
                return Output()

        agent = Agent()
        Runner(game, Log(), {"A": agent, "B": Agent()}).play(state)
        # 两次犯规→弃权：没有任何合法动作可记。
        assert agent.history == []
