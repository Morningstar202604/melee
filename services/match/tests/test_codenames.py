from __future__ import annotations

import pytest

from services.match.agents.scripted import CodenamesAgent
from services.match.engine.base import Move
from services.match.engine.codenames import (
    GRID_SIZE,
    LAYOUT,
    TEAM_ASSASSIN,
    TEAM_BLUE,
    TEAM_NEUTRAL,
    TEAM_RED,
    Codenames,
)


def start(seed: int = 3):
    game = Codenames()
    state = game.init(seed=seed, players=["A", "B", "C", "D"], config={})
    return game, state


# ---------- 初始化 ----------

def test_board_has_25_distinct_words():
    _game, state = start()
    assert len(state["words"]) == GRID_SIZE
    assert len(set(state["words"])) == GRID_SIZE


def test_colors_match_layout():
    _game, state = start()
    colors = state["colors"]
    assert colors.count(TEAM_RED) == LAYOUT[TEAM_RED]
    assert colors.count(TEAM_BLUE) == LAYOUT[TEAM_BLUE]
    assert colors.count(TEAM_NEUTRAL) == LAYOUT[TEAM_NEUTRAL]
    assert colors.count(TEAM_ASSASSIN) == LAYOUT[TEAM_ASSASSIN]


def test_same_seed_gives_same_board():
    _g1, a = start(7)
    _g2, b = start(7)
    assert a["words"] == b["words"]
    assert a["colors"] == b["colors"]


def test_different_seed_gives_different_board():
    _g1, a = start(1)
    _g2, b = start(2)
    assert a["words"] != b["words"] or a["colors"] != b["colors"]


def test_requires_four_players():
    game = Codenames()
    with pytest.raises(ValueError):
        game.init(seed=1, players=["A", "B"], config={})


# ---------- 隐藏信息（本赛制的核心） ----------

def test_captain_sees_colors_agent_does_not():
    game, state = start()
    captain = game.observe(state, "A")   # 红队队长
    agent = game.observe(state, "C")     # 红队队员
    assert "secretColors" in captain
    assert "secretColors" not in agent


def test_public_board_hides_unrevealed_colors():
    game, state = start()
    public = game.public_state(state)
    assert all(row["color"] is None for row in public["board"])


def test_public_board_reveals_color_after_pick():
    game, state = start()
    # 队长给线索 → 队员翻一个词
    r1 = game.apply(state, "A", Move("guess", "hint:测试 2"))
    state = r1.next_state
    word = state["words"][0]
    r2 = game.apply(state, "C", Move("guess", f"pick:{word}"))
    state = r2.next_state

    public = game.public_state(state)
    assert public["board"][0]["color"] is not None


def test_agent_observation_never_contains_full_palette():
    """队员视角不能推断出任何未翻开词的颜色。

    用结构断言而不是字符串计数：颜色可能作为 team / turn 的取值正常出现，
    真正的判据是"没有一份与棋盘等长的配色数组"。
    """
    game, state = start()
    observation = game.observe(state, "C")

    assert "secretColors" not in observation
    # 任何字段都不能是长度 25、元素为颜色名的列表
    palette = {TEAM_RED, TEAM_BLUE, TEAM_NEUTRAL, TEAM_ASSASSIN}
    for value in observation.values():
        if not isinstance(value, list) or len(value) != GRID_SIZE:
            continue
        flat = [item for item in value if isinstance(item, str)]
        assert not any(item in palette for item in flat), "发现了整盘配色"
    # 公开棋盘里未翻开的词一律无颜色
    assert all(row["color"] is None for row in observation["publicBoard"])


# ---------- 回合与动作 ----------

def test_red_captain_starts_with_hint_phase():
    game, state = start()
    assert state["turn"] == TEAM_RED
    assert state["phase"] == "hint"
    assert game.current_player(state) == "A"


def test_hint_cannot_be_a_board_word():
    game, state = start()
    word = state["words"][0]
    result = game.apply(state, "A", Move("guess", f"hint:{word} 1"))
    assert not result.legal


def test_hint_requires_number():
    game, state = start()
    result = game.apply(state, "A", Move("guess", "hint:测试"))
    assert not result.legal


def test_hint_number_out_of_range_rejected():
    game, state = start()
    assert not game.apply(state, "A", Move("guess", "hint:测试 0")).legal
    assert not game.apply(state, "A", Move("guess", "hint:测试 99")).legal


def test_valid_hint_moves_to_guess_phase():
    game, state = start()
    result = game.apply(state, "A", Move("guess", "hint:测试 2"))
    assert result.legal
    assert result.next_state["phase"] == "guess"
    assert game.current_player(result.next_state) == "C"


def test_wrong_player_cannot_act():
    game, state = start()
    result = game.apply(state, "B", Move("guess", "hint:测试 2"))
    assert not result.legal


def test_pick_unknown_word_rejected():
    game, state = start()
    state = game.apply(state, "A", Move("guess", "hint:测试 2")).next_state
    result = game.apply(state, "C", Move("guess", "pick:不存在的词"))
    assert not result.legal


def test_cannot_pick_same_word_twice():
    game, state = start()
    state = game.apply(state, "A", Move("guess", "hint:测试 3")).next_state
    word = state["words"][0]
    state = game.apply(state, "C", Move("guess", f"pick:{word}")).next_state

    # 若还轮到红队队员，再猜同一个词应当被拒
    if game.current_player(state) == "C":
        assert not game.apply(state, "C", Move("guess", f"pick:{word}")).legal


# ---------- 胜负 ----------

def test_hitting_assassin_loses_immediately():
    game, state = start()
    assassin_index = state["colors"].index(TEAM_ASSASSIN)
    word = state["words"][assassin_index]

    state = game.apply(state, "A", Move("guess", "hint:测试 3")).next_state
    result = game.apply(state, "C", Move("guess", f"pick:{word}"))
    assert result.legal
    assert result.next_state["finished"] is True
    assert result.next_state["winner"] == TEAM_BLUE


def test_wrong_guess_switches_turn():
    game, state = start()
    # 找一个蓝队的词
    blue_index = state["colors"].index(TEAM_BLUE)
    word = state["words"][blue_index]

    state = game.apply(state, "A", Move("guess", "hint:测试 3")).next_state
    result = game.apply(state, "C", Move("guess", f"pick:{word}"))
    assert result.legal
    assert result.next_state["turn"] == TEAM_BLUE
    assert result.next_state["phase"] == "hint"


def test_forfeit_hands_win_to_other_team():
    game, state = start()
    result = game.apply(state, "A", Move("forfeit", ""))
    assert result.legal
    assert result.next_state["winner"] == TEAM_BLUE


# ---------- 默认选手能打完 ----------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_default_agent_finishes_without_illegal_moves(seed):
    game, state = start(seed)
    steps = 0
    for _ in range(400):
        if state["finished"]:
            break
        current = game.current_player(state)
        observation = game.observe(state, current)
        output = CodenamesAgent(current).act(observation, [])
        result = game.apply(state, current, output.move)
        assert result.legal, f"第 {steps} 步非法：{result.reason}"
        state = result.next_state
        steps += 1

    assert state["finished"] is True
    assert state["winner"] in {TEAM_RED, TEAM_BLUE}


# ---------- 解说密度 ----------

def _play_with_narrator(seed: int = 3):
    import tempfile
    from pathlib import Path

    from services.match.eventlog import EventLog
    from services.match.narrator import RuleNarrator
    from services.match.runner import Runner

    game = Codenames()
    state = game.init(seed=seed, players=["A", "B", "C", "D"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", "m")
    log.create_match(game.game_id, seed, ["A", "B", "C", "D"])
    Runner(
        game,
        log,
        {p: CodenamesAgent(p) for p in ["A", "B", "C", "D"]},
        narrator=RuleNarrator(),
    ).play(state)
    events = log.read()
    log.close()
    return events


def test_every_applied_action_gets_a_narration_line():
    """解说密度：每一步都该有话说，不能只在终局开口。"""
    events = _play_with_narrator()
    applied = [e for e in events if e["type"] == "action.applied"]
    lines = [e for e in events if e["type"] == "narration.line"]
    assert applied, "这一局必须有动作"
    assert len(lines) >= len(applied), f"动作 {len(applied)} 条，解说只有 {len(lines)} 条"


def test_hint_is_narrated():
    events = _play_with_narrator()
    texts = [e["payload"]["text"] for e in events if e["type"] == "narration.line"]
    assert any("线索" in t for t in texts)


def test_reveal_is_narrated():
    events = _play_with_narrator()
    texts = [e["payload"]["text"] for e in events if e["type"] == "narration.line"]
    assert any("翻中" in t or "踩到" in t or "刺客" in t for t in texts)


def test_situation_reports_leader_correctly():
    from services.match.narrator import RuleNarrator

    narrator = RuleNarrator()
    assert "蓝队领先" in narrator.situation({"remaining": {"red": 9, "blue": 8}}, game_id="codenames")
    assert "红队领先" in narrator.situation({"remaining": {"red": 7, "blue": 9}}, game_id="codenames")
    assert "双方" in narrator.situation({"remaining": {"red": 5, "blue": 5}}, game_id="codenames")


def test_situation_is_not_emitted_every_step():
    """局势播报要有节制：不能每条动作后面都跟一句。"""
    events = _play_with_narrator()
    situations = [
        e for e in events
        if e["type"] == "narration.line" and e["payload"].get("tone") == "turn"
    ]
    applied = [e for e in events if e["type"] == "action.applied"]
    assert len(situations) < len(applied)


# ---------- 历史记录 ----------

def test_history_records_every_action():
    """选手卡要用 history 显示动作记录，必须每步都写。"""
    import tempfile
    from pathlib import Path

    from services.match.eventlog import EventLog
    from services.match.runner import Runner

    game = Codenames()
    state = game.init(seed=5, players=["A", "B", "C", "D"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", "m")
    log.create_match(game.game_id, 5, ["A", "B", "C", "D"])
    Runner(game, log, {p: CodenamesAgent(p) for p in ["A", "B", "C", "D"]}).play(state)
    events = log.read()
    log.close()

    last = [e for e in events if e["type"] == "state.changed"][-1]
    history = last["payload"]["publicState"]["history"]
    applied = [e for e in events if e["type"] == "action.applied"]

    assert len(history) == len(applied), f"动作 {len(applied)} 条，history 只有 {len(history)} 条"


def test_history_has_hint_and_pick_kinds():
    import tempfile
    from pathlib import Path

    from services.match.eventlog import EventLog
    from services.match.runner import Runner

    game = Codenames()
    state = game.init(seed=5, players=["A", "B", "C", "D"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", "m")
    log.create_match(game.game_id, 5, ["A", "B", "C", "D"])
    Runner(game, log, {p: CodenamesAgent(p) for p in ["A", "B", "C", "D"]}).play(state)
    events = log.read()
    log.close()

    history = [e for e in events if e["type"] == "state.changed"][-1]["payload"]["publicState"]["history"]
    kinds = {item["kind"] for item in history}
    assert "hint" in kinds and "pick" in kinds


def test_legal_moves_exclude_revealed_words():
    """已翻开的词不能再进可选列表：模型照着选会连犯两次规然后弃权。"""
    game, state = start()
    r1 = game.apply(state, "A", Move("guess", "hint:测试 2"))
    state = r1.next_state
    word = state["words"][0]
    r2 = game.apply(state, "C", Move("guess", f"pick:{word}"))
    state = r2.next_state

    # 猜错会换手回到对方队长；推进到下一次猜词阶段再断言。
    r3 = game.apply(state, game.current_player(state), Move("guess", "hint:测试 1"))
    state = r3.next_state
    picker = game.current_player(state)
    options = game.legal_moves(state, picker)

    assert f"pick:{word}" not in options
    remaining = {
        w for w, revealed in zip(state["words"], state["revealed"]) if not revealed
    }
    assert {opt.split(":", 1)[1] for opt in options} == remaining


def test_layout_numbers_are_absolute_not_self_referential():
    """配色数量必须写死成规则值：只断言 counts == LAYOUT 时，改常量也测不出来。"""
    game, state = start()
    colors = state["colors"]
    assert colors.count(TEAM_RED) == 9
    assert colors.count(TEAM_BLUE) == 8
    assert colors.count(TEAM_NEUTRAL) == 7
    assert colors.count(TEAM_ASSASSIN) == 1
    assert len(colors) == 25
    assert GRID_SIZE == 25


def test_board_always_has_exactly_25_words():
    """棋盘少一格会让 UI 的 5×5 网格错位。"""
    for seed in (1, 2, 3, 99):
        game = Codenames()
        state = game.init(seed=seed, players=list("ABCD"), config={})
        assert len(state["words"]) == 25
        assert len(set(state["words"])) == 25
