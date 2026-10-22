from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.match.agents.scripted import GreedyAgent
from services.match.control import CueError, auto_cue_summary, live_matches, push_cue, recent_cues
from services.match.director import Director
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog, read_match
from services.match.narrator import RuleNarrator
from services.match.runner import Runner


def make_match(running: bool = True) -> tuple[Path, str]:
    """造一场比赛：running=True 只跑前两手，False 跑完。"""
    db = Path(tempfile.mkdtemp()) / "m.db"
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    log = EventLog.open(db, "m1")
    log.create_match(game.game_id, 3, ["A", "B"])
    log.append("match.created", {"game": game.game_id, "players": ["A", "B"], "seed": 3})
    log.append("match.started", {"firstPlayer": "A"})

    from services.match.engine.base import Move

    for player, value in (("A", "category=建筑"), ("B", "place=城市")):
        result = game.apply(state, player, Move("ask", value))
        assert result.legal and result.next_state
        state = result.next_state
        log.append(
            "action.applied",
            {
                "action": {"kind": "ask", "value": value},
                "effects": [{"kind": e.kind, **e.detail} for e in result.effects],
            },
            player=player,
        )
        log.append("state.changed", {"publicState": result.public_state, "deltas": []})

    if not running:
        game2 = TwentyQuestions()
        state2 = game2.init(seed=3, players=["A", "B"], config={})
        Runner(
            game2,
            log,
            {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)},
            narrator=RuleNarrator(),
            director=Director(),
        ).play(state2)

    log.close()
    return db, "m1"


def test_push_cue_appends_event():
    db, match_id = make_match()
    event = push_cue(db, match_id, shot="close", target="A", duration_ms=900, note="看这里")
    assert event["type"] == "director.cue"
    assert event["payload"]["shot"] == "close"
    assert event["payload"]["trigger"].startswith("人工")


def test_cue_is_persisted_and_readable():
    db, match_id = make_match()
    push_cue(db, match_id, shot="reveal")
    events = read_match(db, match_id)
    assert any(e["type"] == "director.cue" for e in events)
    assert len(recent_cues(db, match_id)) == 1


def test_unknown_shot_is_rejected():
    db, match_id = make_match()
    with pytest.raises(CueError):
        push_cue(db, match_id, shot="zoom-in")


def test_negative_duration_is_rejected():
    db, match_id = make_match()
    with pytest.raises(CueError):
        push_cue(db, match_id, shot="wide", duration_ms=-1)


def test_cue_on_finished_match_is_rejected():
    db, match_id = make_match(running=False)
    with pytest.raises(CueError) as exc:
        push_cue(db, match_id, shot="wide")
    assert "已结束" in str(exc.value)


def test_cue_on_unknown_match_is_rejected():
    db, _ = make_match()
    with pytest.raises(CueError):
        push_cue(db, "nope", shot="wide")


def test_cue_seq_stays_contiguous():
    db, match_id = make_match()
    push_cue(db, match_id, shot="close")
    push_cue(db, match_id, shot="wide")
    seqs = [e["seq"] for e in read_match(db, match_id)]
    assert seqs == list(range(len(seqs)))


def test_auto_and_manual_are_counted_separately():
    db, match_id = make_match()
    push_cue(db, match_id, shot="close")
    counts = auto_cue_summary(db, match_id)
    assert counts["manual"] == 1


def test_live_matches_lists_running_only():
    db, match_id = make_match()
    listed = live_matches(db)
    assert [m["matchId"] for m in listed] == [match_id]


def test_live_matches_excludes_finished():
    db, _ = make_match(running=False)
    assert live_matches(db) == []
