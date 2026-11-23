"""导入层的边界分支：值解析、胜负判定、格式检测与棋步还原。"""

from __future__ import annotations

import pytest

from services.match.episode import (
    Episode,
    EpisodeStep,
    _action_text,
    _as_int,
    _as_iso,
    _board_from_value,
    _chess_move_between,
    _finalize,
    _kaggle_winner,
    _normalize_format,
    _normalize_game,
    _players_from,
    _resolve_player,
    _square_size,
    _text_from_value,
    _truncate,
    _unique,
    _winner_from,
    _winner_from_rewards,
    summarize_imported,
)

PLAYERS = ["A", "B"]


def test_winner_sources_cover_string_int_result_and_step():
    assert _winner_from({"winner": "A"}, PLAYERS, []) == "A"
    assert _winner_from({"winning_player": 1}, PLAYERS, []) == "B"
    assert _winner_from({"result": {"winner": "B"}}, PLAYERS, []) == "B"
    assert _winner_from({"result": {"winner": 0}}, PLAYERS, []) == "A"
    step = EpisodeStep(player="A", action="x", raw={"winner": 1})
    assert _winner_from({}, PLAYERS, [step]) == "B"
    assert _winner_from({}, PLAYERS, []) is None


def test_kaggle_winner_and_rewards():
    assert _kaggle_winner({"winner": "A"}, [], PLAYERS, []) == "A"
    assert _kaggle_winner({"info": {"Winner": 1}}, [], PLAYERS, []) == "B"
    assert _kaggle_winner({}, [{"reward": 1, "player": "A"}], PLAYERS, []) == "A"
    assert _kaggle_winner({}, [None], PLAYERS, []) is None
    assert _winner_from_rewards({"1": 1, "0": -1}, PLAYERS) == "B"
    assert _winner_from_rewards([-1, 1], PLAYERS) == "B"
    assert _winner_from_rewards("nope", PLAYERS) is None


def test_finalize_rejects_each_invalid_field():
    with pytest.raises(ValueError, match="missing game"):
        _finalize(Episode(source="x", game="", players=PLAYERS, seed=0, steps=[EpisodeStep("A", "x")]))
    with pytest.raises(ValueError, match="no steps"):
        _finalize(Episode(source="x", game="chess", players=PLAYERS, seed=0, steps=[]))
    with pytest.raises(ValueError, match="two players"):
        _finalize(Episode(source="x", game="chess", players=["A"], seed=0, steps=[EpisodeStep("A", "x")]))
    with pytest.raises(ValueError, match="not one of the players"):
        _finalize(Episode(source="x", game="chess", players=PLAYERS, seed=0, steps=[EpisodeStep("A", "x")], winner="C"))
    with pytest.raises(ValueError, match="unique"):
        _finalize(Episode(source="x", game="chess", players=["A", "A"], seed=0, steps=[EpisodeStep("A", "x")]))
    episode = Episode(source="x", game="chess", players=PLAYERS, seed=0, steps=[EpisodeStep("A", "x")], reason="")
    assert _finalize(episode).reason == "对局结束"


def test_normalize_format_and_game():
    assert _normalize_format("melee", {}) == "episode"
    assert _normalize_format("EPISODE", {}) == "episode"
    with pytest.raises(ValueError, match="unsupported"):
        _normalize_format("bad", {})
    assert _normalize_format("kaggle", {}) == "kaggle"
    assert _normalize_format("auto", {"format": "melee.episode/1"}) == "episode"
    assert _normalize_format("auto", {"format": "kaggle-x"}) == "kaggle"
    assert _normalize_format("auto", {"format": "textarena"}) == "textarena"
    assert _normalize_format("auto", {"root": {"configuration": {}, "steps": []}}) == "kaggle"
    assert _normalize_format("auto", {"turns": []}) == "textarena"
    assert _normalize_format("auto", {"steps": []}) == "episode"
    with pytest.raises(ValueError, match="cannot detect"):
        _normalize_format("auto", {})
    assert _normalize_game(None) == ""
    assert _normalize_game("chess") == "chess"
    assert _normalize_game("ticTacToe") == "tic-tac-toe"
    assert _normalize_game("open_spiel_chess") == "open-spiel-chess"


def test_players_and_resolve():
    assert _players_from({"b": "B", "a": "A"}) == ["A", "B"]
    assert _players_from([{"name": "A"}, {"Name": "B"}, "  A  "]) == ["A", "B", "A #2"]
    assert _players_from(None) == []
    assert _unique(["A", "A", "A"]) == ["A", "A #2", "A #3"]
    assert _resolve_player({"name": "A"}, PLAYERS, 0) == "A"
    assert _resolve_player("0", PLAYERS, 0) == "A"
    assert _resolve_player("player-1", PLAYERS, 0) == "B"
    assert _resolve_player("unknown", PLAYERS, 0) == "A"
    assert _resolve_player(3, PLAYERS, 0) == "B"


def test_action_text_and_values():
    assert _action_text(None) == ""
    assert _action_text(" e4 ") == "e4"
    assert _action_text({"value": "e4"}) == "e4"
    assert _action_text({"move": "Nf3"}) == "Nf3"
    assert _action_text([1]) == "[1]"
    assert _text_from_value({"text": "hi"}) == "hi"
    assert _text_from_value({"message": "  "}) == ""
    assert _text_from_value(" hi ") == "hi"
    assert _text_from_value(1) == ""
    assert _square_size(64) == 8
    assert _square_size(9) == 3
    assert _square_size(10) is None
    assert _as_int("3", 0) == 3
    assert _as_int("x", 7) == 7
    assert _as_iso("2026-01-01T00:00:00") == "2026-01-01T00:00:00"
    assert _as_iso("") is None
    assert _as_iso("nope") is None
    assert _truncate("abc", 3) == "abc"
    assert _truncate("abcd", 3) == "ab…"


def test_board_value_shapes():
    assert _board_from_value(None) is None
    assert _board_from_value({"board": [["A", "B"], ["C", "D"]]}) == [["A", "B"], ["C", "D"]]
    assert _board_from_value({"fen": "8/8/8/8/8/8/8/8"}) is not None
    assert _board_from_value({"other": 1}) is None
    grid = "ABC\nDEF\nGHI"
    assert _board_from_value(grid) == [["A", "B", "C"], ["D", "E", "F"], ["G", "H", "I"]]
    assert _board_from_value("ABCD") is None
    assert _board_from_value([]) is None
    assert _board_from_value([["A", "B"]]) == [["A", "B"]]
    assert _board_from_value(["AB", "CD"]) == [["A", "B"], ["C", "D"]]
    assert _board_from_value(list(range(9))) is not None
    assert _board_from_value([1, 2]) is None


def test_chess_move_between_branches():
    start = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    after = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    assert _chess_move_between(start, after) == ("e4", "e2", "e4")
    assert _chess_move_between(None, after) is None
    assert _chess_move_between("start", after) is None
    assert _chess_move_between(start, start) is None
    assert _chess_move_between("bad/fen", after) is None


def test_summarize_imported_paths():
    events = [
        {"seq": 0, "type": "match.created", "payload": {"game": "chess", "players": PLAYERS}},
        {"seq": 1, "type": "state.changed", "payload": {"publicState": {"turnIndex": 2, "winner": "A"}}},
        {"seq": 2, "type": "match.ended", "payload": {"winner": "B"}},
    ]
    summary = summarize_imported("m1", events)
    assert summary["winner"] == "B" and summary["finished"] is True
    assert summarize_imported("m1", events[:2])["winner"] == "A"
    with pytest.raises(ValueError, match="match.created"):
        summarize_imported("m1", [])


def test_parse_rejects_non_object_and_build_reads_step_at_text():
    from services.match.episode import build_export, parse_episode

    with pytest.raises(ValueError, match="JSON object"):
        parse_episode([])
    episode = Episode(
        source="fixture",
        game="chess",
        players=PLAYERS,
        seed=0,
        steps=[EpisodeStep(player="A", action="e4", at="2026-01-01T00:00:00", text="opening")],
        winner="A",
    )
    document = build_export(episode)
    assert any(event["type"] == "state.changed" and event["payload"]["publicState"].get("gameState") == {"text": "opening"} for event in document["events"])
    assert any(event["type"] == "turn.started" and event["at"] == "2026-01-01T00:00:00" for event in document["events"])


def test_kaggle_action_and_thought_variants():
    from services.match.episode import _kaggle_action, _kaggle_thought

    assert _kaggle_action(None) == ""
    assert _kaggle_action({"action": {"actionString": "e4"}}) == "e4"
    assert _kaggle_action({"info": {"actionAppliedToString": "Nf3"}}) == "Nf3"
    assert _kaggle_action({"info": {"actionApplied": 42}}) == "42"
    assert _kaggle_action({"action": " e4 "}) == "e4"
    assert _kaggle_action({"action": 7}) == "7"
    assert _kaggle_action({"action": -1}) == ""

    assert _kaggle_thought({"info": {"reasoning": "think"}}) == "think"
    assert _kaggle_thought({"action": {"generate_returns": [None, "not json", '{"main_response":"answer"}']}}) == "answer"
    assert _kaggle_thought({}) == ""


def test_action_and_board_helpers():
    from services.match.episode import _board_from_value, _has_action, _looks_like_fen

    assert _has_action({"action": "e4"}) is True
    assert _has_action({"info": {"action": "e4"}}) is True
    assert _has_action({"action": []}) is False
    assert _has_action("no") is False
    assert _board_from_value({"gameState": {"board": [["A"]]}}) == [["A"]]
    assert _board_from_value({"position": "8/8/8/8/8/8/8/8"}) is not None
    assert _board_from_value({"fen": 1}) is None
    assert _board_from_value(["A", ["B"]]) is None
    assert _looks_like_fen("8/8") is False


def test_build_export_cue_branches_and_live():
    from services.match.episode import build_export

    steps = [EpisodeStep(player=PLAYERS[index % 2], action=move) for index, move in enumerate(("e4", "e5", "Nf3"))]
    episode = Episode(source="fixture", game="chess", players=PLAYERS, seed=0, steps=steps)
    document = build_export(episode)
    cues = [event for event in document["events"] if event["type"] == "director.cue"]
    assert [cue["payload"]["shot"] for cue in cues] == ["wide", "close", "slowmo"]

    live = build_export(episode, live=True)
    assert live["config"]["live"] is True
    assert not [event for event in live["events"] if event["type"] == "director.cue"]
    unfinished = build_export(episode, finished=False)
    assert unfinished["live"] is True and unfinished["endedAt"] is None


def test_pgn_minimal_headers_and_missing_san():
    from services.match.chess_export import _wrap, pgn_from_export

    assert _wrap([]) == []
    minimal = {
        "game": "chess",
        "players": ["A", "B"],
        "config": {},
        "events": [
            {"type": "match.created", "payload": {}},
            {"type": "action.applied", "payload": {"action": {"value": "e4"}}},
            {"type": "match.ended", "payload": {}},
        ],
    }
    text = pgn_from_export(minimal)
    assert "Termination" not in text and "{ " not in text
    broken = {**minimal, "events": [{"type": "action.applied", "payload": {"action": {"value": ""}}}]}
    try:
        pgn_from_export(broken)
    except ValueError as exc:
        assert "missing SAN" in str(exc)
    else:
        raise AssertionError("missing SAN should fail")


def test_misc_branch_guards(tmp_path):
    import sqlite3

    from services.match.db import ensure_schema_for
    from services.match.fairness import resolve_seed
    from services.match.textfit import wrap_text
    from services.match.webhook_log import record

    with pytest.raises(ValueError, match="unknown seed mode"):
        resolve_seed(None, "bogus")
    assert wrap_text("", 4) == [""]

    conn = sqlite3.connect(":memory:")
    try:
        assert ensure_schema_for(conn, "CREATE TABLE IF NOT EXISTS x (a TEXT)", "x") is True
    finally:
        conn.close()
    record(tmp_path / "webhooks.db", "m1", b"{}", [])
