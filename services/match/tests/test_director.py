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


# ---------- 每条切镜规则的可达性 ----------
# 这些用例存在的理由：导播规则是一张"效果类型 -> 机位"的映射表，
# 某条规则没有案例时，改错机位或漏传参数都不会有人发现。
# 断言的是 shot 与模板键，不是某一种语言的 trigger 文案。

def _cue_for(effect_kind: str, detail: dict, *, public_state=None, finished=False, player="A"):
    from services.match.director import Director
    from services.match.engine.base import AppliedEffect

    director = Director()
    return director.cue_for(
        player=player,
        effects=[AppliedEffect(kind=effect_kind, detail=detail)],
        public_state=public_state or {},
        finished=finished,
    )


def test_guess_cue_uses_slowmo_when_it_ends_the_match():
    cue = _cue_for("guess", {"value": "x", "correct": True}, finished=True)
    assert cue is not None
    assert cue.shot == "slowmo"
    assert cue.trigger_key == "cue.last_move"


def test_guess_cue_uses_reveal_mid_match():
    cue = _cue_for("guess", {"value": "x", "correct": False}, finished=False)
    assert cue is not None
    assert cue.shot == "reveal"
    assert cue.trigger_key == "cue.guess_reveal"


def test_action_cue_only_after_eighth_step():
    early = _cue_for("action", {"action": "[4]"}, public_state={"turnIndex": 3}, finished=False)
    assert early is None, "开局几手不该切镜"

    late = _cue_for("action", {"action": "[4]"}, public_state={"turnIndex": 9}, finished=False)
    assert late is not None
    assert late.shot == "close"
    assert late.trigger_params == {"step": 9}

    last = _cue_for("action", {"action": "[4]"}, public_state={"turnIndex": 9}, finished=True)
    assert last is not None and last.shot == "slowmo"


def test_hint_cue_is_always_closeup():
    cue = _cue_for("hint", {"word": "海", "count": 2})
    assert cue is not None
    assert cue.shot == "close"
    assert cue.trigger_key == "cue.hint_closeup"


def test_reveal_cue_matrix():
    assassin = _cue_for("reveal", {"color": "assassin", "team": "red", "word": "w"})
    assert assassin.shot == "slowmo" and assassin.trigger_key == "cue.assassin"

    own = _cue_for("reveal", {"color": "red", "team": "red", "word": "w"})
    assert own.shot == "reveal" and own.trigger_key == "cue.own_word"

    wrong = _cue_for("reveal", {"color": "blue", "team": "red", "word": "w"})
    assert wrong.shot == "close" and wrong.trigger_key == "cue.wrong_flip"

    last = _cue_for("reveal", {"color": "blue", "team": "red", "word": "w"}, finished=True)
    assert last.shot == "slowmo" and last.trigger_key == "cue.last_flip"


def test_unknown_effect_produces_no_cue():
    assert _cue_for("nonsense", {}) is None


def test_cue_trigger_is_localisable():
    """同一条切镜能按语言重渲染：事件流里存英文，中文请求拿到中文。"""
    from services.match.messages import localize_payload

    cue = _cue_for("reveal", {"color": "assassin", "team": "red", "word": "钟楼"})
    payload = cue.payload()
    assert payload["trigger"] == "assassin revealed — slow motion"

    zh = localize_payload(payload, "zh")
    assert zh["trigger"] == "翻到刺客，慢镜收尾"
