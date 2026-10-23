from __future__ import annotations

import json

import pytest

from services.match.agents.llm import LLMAgent, LLMConfig, parse_action
from services.match.agents.base import AgentOutput
from services.match.engine.base import Move
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator
from services.match.runner import Runner

from .fake_openai import FakeOpenAI

import tempfile
from pathlib import Path

SECRET = "sk-test-secret-value"


def observation(seed: int = 7):
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    return game, state, game.observe(state, "A")


def make_agent(server: FakeOpenAI) -> LLMAgent:
    return LLMAgent(
        "A",
        LLMConfig(base_url=server.base_url, api_key=SECRET, model="fake-model", timeout_s=5.0),
    )


def test_parses_plain_json():
    assert parse_action('{"kind":"ask","value":"place=海边"}', []) == Move("ask", "place=海边")


def test_parses_fenced_json():
    text = '```json\n{"kind":"guess","value":"灯塔"}\n```'
    assert parse_action(text, []) == Move("guess", "灯塔")


def test_parses_json_with_leading_chatter():
    text = '我想问这个：{"kind":"ask","value":"scale=大型"}'
    assert parse_action(text, []) == Move("ask", "scale=大型")


def test_unparseable_becomes_illegal_not_guessed():
    move = parse_action("我觉得应该是灯塔吧？", [])
    assert move.kind == "ask" and move.value == ""


def test_unknown_kind_becomes_illegal():
    move = parse_action('{"kind":"attack","value":"x"}', [])
    assert move.value == ""


def test_ask_value_is_normalized():
    move = parse_action('{"kind":"ask","value":" place = 海边 "}', [])
    assert move == Move("ask", "place=海边")


def test_request_uses_chat_completions_and_bearer_auth():
    with FakeOpenAI() as server:
        agent = make_agent(server)
        _game, _state, obs = observation()
        agent.act(obs, [])
        request = server.requests[0]
        assert request["path"].endswith("/chat/completions")
        assert request["authorization"] == f"Bearer {SECRET}"
        assert request["body"]["model"] == "fake-model"
        assert request["body"]["response_format"] == {"type": "json_object"}


def test_reasoning_field_becomes_thought():
    with FakeOpenAI() as server:
        server.replies.append({"content": '{"kind":"ask","value":"place=海边"}', "reasoning": "先缩小地点"})
        agent = make_agent(server)
        _game, _state, obs = observation()
        thoughts: list[str] = []
        agent.act(obs, [], thoughts.append)
        assert thoughts == ["先缩小地点"]


def test_thought_field_is_used_when_no_reasoning():
    with FakeOpenAI() as server:
        server.replies.append({"content": '{"kind":"guess","value":"灯塔","thought":"范围已经够小了"}'})
        agent = make_agent(server)
        _game, _state, obs = observation()
        thoughts: list[str] = []
        agent.act(obs, [], thoughts.append)
        assert thoughts == ["范围已经够小了"]


def test_http_error_does_not_leak_key():
    with FakeOpenAI() as server:
        server.status = 500
        agent = make_agent(server)
        _game, _state, obs = observation()
        output = agent.act(obs, [])
        assert SECRET not in output.raw
        assert "***" in output.raw


def test_transport_failure_is_reported_as_raw():
    config = LLMConfig(base_url="http://127.0.0.1:1/v1", api_key=SECRET, model="m", timeout_s=0.5)
    agent = LLMAgent("A", config)
    _game, _state, obs = observation()
    output = agent.act(obs, [])
    assert output.raw.startswith("[transport-error]")
    assert SECRET not in output.raw


def test_repr_hides_key():
    config = LLMConfig(base_url="http://x/v1", api_key=SECRET, model="m")
    assert SECRET not in repr(LLMAgent("A", config))


def test_prompt_contains_no_secret_and_no_target():
    with FakeOpenAI() as server:
        agent = make_agent(server)
        game, state, obs = observation()
        agent.act(obs, [])
        sent = json.dumps(server.requests[0]["body"], ensure_ascii=False)
        assert SECRET not in sent
        assert state["target"] not in sent


def test_llm_agent_plays_a_full_match_against_greedy():
    with FakeOpenAI() as server:
        server.replies = [{"content": '{"kind":"ask","value":"category=建筑","thought":"先砍一半"}'}]
        llm = make_agent(server)

        from services.match.agents.scripted import GreedyAgent

        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={})
        tmp = tempfile.mkdtemp()
        log = EventLog.open(Path(tmp) / "m.db", "m")
        log.create_match(game=game.game_id, seed=3, players=["A", "B"])
        Runner(game, log, {"A": llm, "B": GreedyAgent("B", CATALOG)}, narrator=RuleNarrator()).play(state)
        events = log.read()
        log.close()

    kinds = [e["type"] for e in events]
    assert "thought.delta" in kinds
    assert "match.ended" in kinds
    thought = next(e for e in events if e["type"] == "thought.delta")
    assert thought["player"] == "A"
    assert thought["payload"]["text"] == "先砍一半"
    proposed = next(e for e in events if e["type"] == "action.proposed" and e["player"] == "A")
    assert "category=建筑" in proposed["payload"]["raw"]


def test_illegal_llm_output_is_rejected_then_forfeits():
    with FakeOpenAI() as server:
        server.replies = [
            {"content": "我觉得应该是灯塔吧？"},
            {"content": "还是不知道"},
        ]
        llm = make_agent(server)
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={})
        tmp = tempfile.mkdtemp()
        log = EventLog.open(Path(tmp) / "m.db", "m")
        log.create_match(game=game.game_id, seed=3, players=["A", "B"])

        from services.match.agents.scripted import GreedyAgent

        result = Runner(game, log, {"A": llm, "B": GreedyAgent("B", CATALOG)}).play(state)
        events = log.read()
        log.close()

    rejected = [e for e in events if e["type"] == "action.rejected"]
    assert len(rejected) == 2
    assert result.winner == "B"
    assert result.endReason == "弃权"


def test_board_prompt_lists_legal_moves():
    """棋类提示词必须列出合法动作：只给格式提示时模型会挑已占用的格子。"""
    agent = LLMAgent("A", LLMConfig(base_url="http://x/v1", api_key="k", model="m"))
    observation = {"you": "A", "opponent": ["B"], "observations": ["棋盘文本"], "isYourTurn": True}
    messages = agent.build_messages(observation, ["[0]", "[2]", "[4]"])
    system = messages[0]["content"]
    for move in ("[0]", "[2]", "[4]"):
        assert move in system
    assert "只能从下面列出的可行动作里选" in system


def test_codenames_prompt_branches():
    """代号的两套提示词（队长给线索 / 队员猜词）都要能生成。"""
    agent = LLMAgent("A", LLMConfig(base_url="http://x/v1", api_key="k", model="m"))
    captain_obs = {
        "you": "A", "team": "red", "isCaptain": True, "phase": "hint",
        "words": ["灯塔", "海洋"], "publicBoard": [{"word": "灯塔", "revealed": False, "color": None}],
        "secretColors": ["red", "blue"],
    }
    hint_messages = agent.build_messages(captain_obs, ["hint:<线索词> <数量>"])
    assert "队长" in hint_messages[0]["content"]
    assert "hint:" in hint_messages[0]["content"]

    agent_obs = {
        "you": "C", "team": "red", "isCaptain": False, "phase": "guess",
        "words": ["灯塔", "海洋"], "publicBoard": [{"word": "灯塔", "revealed": False, "color": None}],
        "hint": {"word": "海边", "count": 1},
    }
    guess_messages = agent.build_messages(agent_obs, ["pick:灯塔"])
    assert "队员" in guess_messages[0]["content"]
    assert "海边" in guess_messages[1]["content"]


def test_streaming_without_thought_callback_falls_back_to_blocking(monkeypatch):
    """stream=True 但没给 on_thought 时走非流式路径，不能崩。"""
    with FakeOpenAI() as server:
        server.replies = [{"content": '{"kind":"ask","value":"category=建筑"}'}]
        agent = LLMAgent("A", LLMConfig(base_url=server.base_url, api_key="k", model="m",
                                        stream=True, timeout_s=5))
        output = agent.act({"aspects": {"category": ["建筑"]}, "you": "A", "opponent": "B"}, [], None)
    assert output.move.value == "category=建筑"


def test_empty_content_is_reported_as_raw():
    with FakeOpenAI() as server:
        server.replies = [{"content": ""}]
        agent = LLMAgent("A", LLMConfig(base_url=server.base_url, api_key="k", model="m", timeout_s=5))
        output = agent.act({"aspects": {}, "you": "A", "opponent": "B"}, [], None)
    assert "[empty-content]" in output.raw
