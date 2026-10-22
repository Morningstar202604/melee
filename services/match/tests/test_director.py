from __future__ import annotations

import tempfile
from pathlib import Path

from services.match.agents.scripted import GreedyAgent, ScriptedAgent
from services.match.director import Director, apply_director
from services.match.engine.base import Move
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator
from services.match.runner import Runner


def play(seed: int = 7, *, agents=None, director=None):
    game = TwentyQuestions()
    players = ["A", "B"]
    state = game.init(seed=seed, players=players, config={})
    tmp = tempfile.mkdtemp()
    log = EventLog.open(Path(tmp) / "m.db", f"m{seed}")
    log.create_match(game=game.game_id, seed=seed, players=players)
    agents = agents or {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)}
    Runner(game, log, agents, narrator=RuleNarrator(), director=director).play(state)
    events = log.read()
    log.close()
    return events


def test_director_emits_cues():
    events = play(director=Director())
    cues = [e for e in events if e["type"] == "director.cue"]
    assert cues, "应当至少产生一条镜头"


def test_every_cue_has_reachable_trigger():
    events = play(director=Director())
    for cue in (e for e in events if e["type"] == "director.cue"):
        payload = cue["payload"]
        assert payload["shot"] in {"wide", "close", "thought", "reveal", "slowmo"}
        assert payload["durationMs"] > 0
        assert payload["trigger"], "每条 cue 必须说明为什么切"


def test_cue_sequence_is_deterministic():
    first = play(seed=11, director=Director())
    second = play(seed=11, director=Director())
    shots = lambda events: [e["payload"]["shot"] for e in events if e["type"] == "director.cue"]
    assert shots(first) == shots(second)


def test_offline_director_matches_live_director():
    """离线补 cue 与实时产出的镜头序列必须一致，否则回放会和直播不一样。"""
    live = play(seed=9, director=Director())

    offline_events = play(seed=9)
    offline = apply_director(offline_events, Director())

    live_shots = [e["payload"]["shot"] for e in live if e["type"] == "director.cue"]
    offline_shots = [e["payload"]["shot"] for e in offline if e["type"] == "director.cue"]
    assert live_shots == offline_shots


def test_reveal_when_someone_guesses():
    agents = {
        "A": ScriptedAgent("A", [Move(kind="guess", value="灯塔")]),
        "B": ScriptedAgent("B", []),
    }
    events = play(seed=3, agents=agents, director=Director())
    shots = [e["payload"]["shot"] for e in events if e["type"] == "director.cue"]
    assert "reveal" in shots or "slowmo" in shots


def test_thought_cue_on_long_reasoning():
    director = Director()
    assert director.cue_for_thought("A", "短") is None
    cue = director.cue_for_thought("A", "这已经是一段足够长的推理过程，值得切一个内心戏特写")
    assert cue is not None and cue.shot == "thought"


def test_thought_cue_on_question_mark():
    director = Director()
    cue = director.cue_for_thought("A", "会不会是灯塔？")
    assert cue is not None and cue.shot == "thought"
