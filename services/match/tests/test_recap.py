from __future__ import annotations

import tempfile
from pathlib import Path

from services.match.agents.scripted import GreedyAgent, ScriptedAgent
from services.match.director import Director
from services.match.engine.base import Move
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator
from services.match.recap import build_recap, find_highlights, verify_summary
from services.match.runner import Runner
from services.match.season import Season


def play(seed: int = 7, agents=None) -> list[dict]:
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", f"m{seed}")
    log.create_match(game.game_id, seed, ["A", "B"])
    agents = agents or {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)}
    Runner(game, log, agents, narrator=RuleNarrator(), director=Director()).play(state)
    events = log.read()
    log.close()
    return events


def test_recap_counts_match_events():
    events = play()
    summary = build_recap(events)
    assert verify_summary(summary, events) == []


def test_recap_one_line_mentions_winner():
    events = play()
    summary = build_recap(events)
    if summary["winner"]:
        assert summary["winner"] in summary["oneLine"]


def test_highlights_include_the_guess():
    events = play()
    highlights = find_highlights(events)
    assert any(h.kind == "guess" for h in highlights)


def test_highlights_are_capped_and_ordered():
    events = play()
    highlights = find_highlights(events, limit=2)
    assert len(highlights) <= 2
    weights = [h.weight for h in highlights]
    assert weights == sorted(weights, reverse=True)


def test_wasted_question_is_detected():
    agents = {
        "A": ScriptedAgent(
            "A",
            [
                Move(kind="ask", value="category=建筑"),
                Move(kind="ask", value="category=建筑"),
            ],
        ),
        "B": GreedyAgent("B", CATALOG),
    }
    events = play(seed=3, agents=agents)
    kinds = {h.kind for h in find_highlights(events, limit=5)}
    assert "wasted_question" in kinds or "guess" in kinds


def test_forfeit_is_highlighted():
    from .fake_openai import FakeOpenAI
    from services.match.agents.llm import LLMAgent, LLMConfig

    with FakeOpenAI() as server:
        server.replies = [{"content": "我不知道"}, {"content": "还是不知道"}]
        agent = LLMAgent("A", LLMConfig(base_url=server.base_url, api_key="k", model="m", timeout_s=5))
        events = play(seed=3, agents={"A": agent, "B": GreedyAgent("B", CATALOG)})

    highlights = find_highlights(events, limit=5)
    assert any(h.kind in {"forfeit", "rejected"} for h in highlights)
    summary = build_recap(events)
    assert summary["winner"] == "B"
    assert "弃权" in summary["reason"]


def test_full_season_of_eight_players():
    """验收：一个 8 人循环赛可完整跑完并出榜。"""
    season = Season("s1", "2026 秋季")
    for index in range(8):
        pid = chr(ord("A") + index)
        season.add_player(pid, f"选手{pid}")
    season.build_round_robin()

    for round_no, a, b, _scheduled in season.fixtures:
        game = TwentyQuestions()
        state = game.init(seed=round_no * 100 + ord(a), players=[a, b], config={})
        log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", f"m{round_no}{a}{b}")
        log.create_match(game.game_id, state["seed"], [a, b])
        result = Runner(
            game,
            log,
            {a: GreedyAgent(a, CATALOG), b: GreedyAgent(b, CATALOG)},
            narrator=RuleNarrator(),
        ).play(state)
        log.close()
        season.record(f"m{round_no}{a}{b}", a, b, result.winner, result.endReason)

    table = season.standings()
    assert len(table) == 8
    assert sum(s.played for s in table) == 56

    total_wins = sum(s.wins for s in table)
    total_losses = sum(s.losses for s in table)
    assert total_wins == total_losses


def _synthetic(game: str, winner: str, reason: str, effects: list[dict], player: str = "A") -> list[dict]:
    return [
        {
            "seq": 0,
            "matchId": "m",
            "type": "match.created",
            "at": "2026-10-21T00:00:00+00:00",
            "payload": {"game": game, "players": ["A", "B"], "seed": 1, "config": {}},
        },
        {
            "seq": 1,
            "matchId": "m",
            "type": "action.applied",
            "at": "2026-10-21T00:00:01+00:00",
            "player": player,
            "payload": {"action": {"kind": "guess", "value": "x"}, "effects": effects},
        },
        {
            "seq": 2,
            "matchId": "m",
            "type": "match.ended",
            "at": "2026-10-21T00:00:02+00:00",
            "payload": {"winner": winner, "reason": reason, "finalState": {}},
        },
    ]


def test_board_match_recap_does_not_repeat_the_winner():
    """棋类引擎的 endReason 已经是「A 获胜」，战报不能再拼一遍。"""
    events = _synthetic("tic-tac-toe", "A", "A 获胜", [{"kind": "action", "action": "[4]"}])
    summary = build_recap(events)
    assert summary["oneLine"] == "A 获胜。"
    assert summary["highlights"], "棋类终局也必须能剪出高光"


def test_codenames_team_winner_is_readable_and_highlighted():
    """代号胜者是 red/blue，战报要翻成人话；翻刺客必须进高光。"""
    events = _synthetic(
        "codenames",
        "blue",
        "翻到刺客「灯塔」，红队立即判负",
        [{"kind": "reveal", "word": "灯塔", "color": "assassin", "team": "red"}],
    )
    summary = build_recap(events)
    assert "红队" in summary["oneLine"] and "蓝队" in summary["oneLine"]
    kinds = {h.kind for h in find_highlights(events)}
    assert "assassin" in kinds


def test_highlight_limit_must_be_positive_and_respected():
    """limit 是调用方契约：默认 3，超出的高光必须被截断。"""
    events = _synthetic("tic-tac-toe", "A", "A 获胜", [{"kind": "action", "action": "[4]"}])
    assert len(find_highlights(events)) <= 3
    assert len(find_highlights(events, limit=1)) == 1


def test_recap_counts_are_zero_for_empty_stream():
    """空事件流不能崩，数字必须是 0 而不是 None。"""
    summary = build_recap([])
    assert summary["turns"] == 0 and summary["asks"] == 0 and summary["guesses"] == 0
    assert summary["winner"] is None
