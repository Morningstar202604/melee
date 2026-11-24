"""提示词协议层与教练指令注入的单元测试。"""

from __future__ import annotations

from services.match.agents.llm import LLMAgent, LLMConfig
from services.match.engine import registry
from services.match.prompting import PROFILES, coach_block, profile_for, protocol_for


def test_every_registered_game_has_a_prompt_profile():
    assert set(registry.available()) == set(PROFILES)


def test_unknown_game_falls_back_to_safe_generic_profile():
    profile = profile_for("not-a-game")
    assert profile.game_id == "unknown"
    assert "隐藏" in profile.rule
    assert "not-a-game" in protocol_for("not-a-game")


def test_protocol_text_is_nonempty_and_keeps_json_contract():
    for game_id in registry.available():
        text = protocol_for(game_id)
        assert "平台对抗协议" in text
        assert "JSON" in text
        assert game_id in text


def test_coach_block_keeps_rules_and_output_format():
    text = coach_block("先换马，再推兵")
    assert "教练席实时指令" in text
    assert "先换马，再推兵" in text
    assert "不违反" in text
    assert "JSON" in text
    assert coach_block("  ") == ""


def test_llm_messages_include_protocol_and_coach():
    agent = LLMAgent(
        "A",
        LLMConfig(
            base_url="http://example.invalid",
            api_key="test-key",
            model="test-model",
            game_id="othello",
        ),
    )
    observation = {"observations": ["board: ..."], "you": "A", "coachInstruction": "占住角位"}
    messages = agent.build_messages(observation, ["d3"])
    system = messages[0]["content"]
    user = messages[1]["content"]
    assert "平台对抗协议" in system
    assert "othello" in system
    assert "教练席实时指令" in user
    assert "占住角位" in user
    assert "JSON" in user


def test_custom_persona_does_not_remove_platform_guardrail():
    agent = LLMAgent(
        "A",
        LLMConfig(
            base_url="http://example.invalid",
            api_key="test-key",
            model="test-model",
            game_id="codenames",
            system_prompt="你是一个极具攻击性的队长。",
        ),
    )
    messages = agent.build_messages({"phase": "hint", "publicBoard": [], "team": "red"}, ["apple 2"])
    system = messages[0]["content"]
    assert "极具攻击性" in system
    assert "平台对抗协议" in system
