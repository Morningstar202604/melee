"""跨赛制一致性：解说 / 导播 / 战报 / 渲染不能只对二十问成立。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.match.agents.scripted import BoardAgent, CodenamesAgent, GreedyAgent
from services.match.director import Director, apply_director
from services.match.engine import registry
from services.match.engine.twenty_questions import CATALOG
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator
from services.match.recap import build_recap, verify_summary
from services.match.render import build_context, draw_frame
from services.match.runner import Runner


def play(game_id: str, seed: int = 5, with_director: bool = True) -> list[dict]:
    players = ["A", "B", "C", "D"] if game_id == "codenames" else ["A", "B"]
    game = registry.get(game_id)
    state = game.init(seed=seed, players=players, config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", "m")
    log.create_match(game.game_id, seed, players)
    if game_id == "codenames":
        agents = {p: CodenamesAgent(p) for p in players}
    elif game_id == "twenty-questions":
        agents = {p: GreedyAgent(p, CATALOG) for p in players}
    else:
        agents = {p: BoardAgent(p, game_id) for p in players}
    Runner(
        game,
        log,
        agents,
        narrator=RuleNarrator(),
        director=Director() if with_director else None,
    ).play(state)
    events = log.read()
    log.close()
    return events


BOARD_GAMES = ["tic-tac-toe", "connect-four", "othello", "nim"]


@pytest.mark.parametrize("game_id", BOARD_GAMES)
def test_board_games_get_narration_for_every_move(game_id):
    events = play(game_id)
    applied = [e for e in events if e["type"] == "action.applied"]
    lines = [e for e in events if e["type"] == "narration.line"]
    assert applied, "至少要有一步"
    assert len(lines) >= len(applied), f"{game_id} 动作 {len(applied)} 条、解说 {len(lines)} 条"


@pytest.mark.parametrize("game_id", BOARD_GAMES + ["codenames", "twenty-questions"])
def test_every_game_gets_director_cues(game_id):
    events = play(game_id)
    cues = [e for e in events if e["type"] == "director.cue"]
    assert cues, f"{game_id} 一条镜头都没有"
    for cue in cues:
        assert cue["payload"]["trigger"], "每条 cue 必须说明原因"


@pytest.mark.parametrize("game_id", BOARD_GAMES + ["codenames", "twenty-questions"])
def test_offline_director_matches_live_for_every_game(game_id):
    live = play(game_id)
    stream = [e for e in live if e["type"] != "director.cue"]
    offline = apply_director(stream, Director())
    live_shots = [e["payload"]["shot"] for e in live if e["type"] == "director.cue"]
    offline_shots = [e["payload"]["shot"] for e in offline if e["type"] == "director.cue"]
    assert live_shots == offline_shots


@pytest.mark.parametrize("game_id", BOARD_GAMES + ["codenames"])
def test_recap_is_game_aware_and_verifies(game_id):
    events = play(game_id)
    recap = build_recap(events)
    assert recap["game"] == game_id
    assert recap["moves"] == len([e for e in events if e["type"] == "action.applied"])
    assert recap["problems"] == [], recap["problems"]
    assert verify_summary(recap, events) == []


def test_codenames_team_winner_passes_verification():
    events = play("codenames")
    recap = build_recap(events)
    assert recap["winner"] in {"red", "blue"}
    assert verify_summary(recap, events) == []


@pytest.mark.parametrize("game_id", BOARD_GAMES + ["codenames"])
def test_render_context_uses_real_game_terms(game_id):
    events = play(game_id)
    context = build_context(events)
    assert context["gameKind"] == game_id
    assert context["players"], "渲染面板需要知道有哪些选手"
    if game_id == "codenames":
        assert context["plays"], "代号要渲染翻牌记录"
        assert context["plays"][0]["kind"] in {"hint", "reveal"}
    else:
        assert context["plays"], "棋类要渲染落子记录"


@pytest.mark.parametrize("game_id", BOARD_GAMES + ["codenames"])
def test_frames_render_without_twenty_questions_assumptions(game_id):
    from services.match.clips import build_clips

    events = play(game_id)
    clips = build_clips(events, limit=1)
    assert clips, "每种赛制都应能剪出高光"
    context = build_context(events)
    frame = draw_frame(clips[0].events[0], clip=clips[0], context={**context, "index": 0})
    assert frame.size == (1600, 900)
