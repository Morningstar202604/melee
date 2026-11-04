"""Strategy library: each game picks a move from the position, not from a hash."""

from __future__ import annotations

import pytest

from services.match.agents import strategy


def _ttt(board):
    return {"gameState": {"board": board}}


def test_tic_tac_toe_takes_the_win():
    state = _ttt([["X", "X", ""], ["O", "O", ""], ["", "", ""]])
    assert strategy.tic_tac_toe_move(state, ["[2]", "[5]", "[6]"]) == "[2]"


def test_tic_tac_toe_blocks_the_loss():
    state = _ttt([["X", "X", ""], ["O", "", ""], ["", "", ""]])
    assert strategy.tic_tac_toe_move(state, ["[2]", "[4]", "[5]"]) == "[2]"


def test_tic_tac_toe_full_board_except_one():
    state = _ttt([["X", "O", "X"], ["X", "O", "O"], ["O", "X", ""]])
    assert strategy.tic_tac_toe_move(state, ["[8]"]) == "[8]"


def test_tic_tac_toe_handles_unparseable_move():
    state = _ttt([["X", "O", ""], ["", "", ""], ["", "", ""]])
    assert strategy.tic_tac_toe_move(state, ["[99]", "[qq]"]) in {"[99]", "[qq]"}


def test_reverse_tic_tac_toe_avoids_completing_a_line():
    state = _ttt([["X", "X", ""], ["O", "O", ""], ["", "", ""]])
    assert strategy.tic_tac_toe_move(state, ["[2]", "[8]"], reverse=True) != "[2]"


def test_nim_clears_the_xor():
    state = {"gameState": {"piles": [3, 4, 5]}}
    assert strategy.nim_move(state, ["[1 1]", "[1 2]", "[3 3]"]) == "[1 2]"


def test_nim_at_zero_xor_plays_a_legal_move():
    assert strategy.nim_move({"gameState": {"piles": [1, 2, 3]}}, ["[1 1]", "[2 2]"]) in {"[1 1]", "[2 2]"}


def test_nim_without_piles_falls_back():
    assert strategy.nim_move({"gameState": {}}, ["[1 1]"]) == "[1 1]"


def test_connect_four_completes_a_line():
    board = [["."] * 7 for _ in range(6)]
    for c in range(3):
        board[5][c] = "X"
    for c in range(3):
        board[4][c] = "O"
    move = strategy.connect_four_move({"gameState": {"board": board}}, ["[col %d]" % c for c in range(7)])
    assert move == "[col 3]"


def test_connect_four_centre_on_empty_board():
    board = [["."] * 7 for _ in range(6)]
    assert strategy.connect_four_move({"gameState": {"board": board}}, ["[col %d]" % c for c in range(7)]) == "[col 3]"


def test_connect_four_accepts_bracket_format():
    board = [["."] * 7 for _ in range(6)]
    assert strategy.connect_four_move({"gameState": {"board": board}}, ["[0]", "[3]"]) in {"[0]", "[3]"}


def test_othello_takes_the_corner():
    board = [["."] * 4 for _ in range(4)]
    assert strategy.othello_move({"gameState": {"board": board}}, ["[1 1]", "[0 0]"]) in {"[1 1]", "[0 0]"}


def test_othello_prefers_a_corner_over_a_corner_neighbour():
    # Corner beats everything; the square next to a corner is the worst play.
    board = [["."] * 6 for _ in range(6)]
    assert strategy.othello_move({"gameState": {"board": board}}, ["[0 1]", "[5 5]"]) == "[5 5]"


def test_othello_without_coordinates_falls_back():
    board = [["."] * 4 for _ in range(4)]
    assert strategy.othello_move({"gameState": {"board": board}}, ["pass"]) == "pass"


def test_kuhn_raises_with_a_king():
    state = {"you": "A", "gameState": {"player_cards": {"A": "K"}, "pot": 2, "current_round": 1}}
    assert strategy.kuhn_move(state, ["[check]", "[bet]"]) == "[bet]"


def test_kuhn_calls_with_a_queen():
    state = {"you": "A", "gameState": {"player_cards": {"A": "Q"}, "pot": 4, "current_round": 1}}
    assert strategy.kuhn_move(state, ["[check]", "[bet]"]) == "[check]"


def test_kuhn_jack_does_not_bluff_on_a_big_pot():
    state = {"you": "A", "gameState": {"player_cards": {"A": "J"}, "pot": 4, "current_round": 0}}
    assert strategy.kuhn_move(state, ["[check]", "[bet]"]) == "[check]"


def test_kuhn_reads_a_hand_keyed_by_seat_index():
    state = {"you": "B", "players": ["A", "B"],
             "gameState": {"player_cards": {0: "J", 1: "K"}, "pot": 1, "current_round": 0}}
    assert strategy.kuhn_move(state, ["[check]", "[bet]"]) == "[bet]"


def test_gops_spends_big_on_a_big_prize():
    state = {"you": "A", "gameState": {"player_hands": {"A": ["2", "9"]}, "current_prize": 10}}
    assert strategy.gops_move(state, ["[2]", "[9]"]) == "[9]"


def test_gops_saves_big_cards_on_a_small_prize():
    state = {"you": "A", "gameState": {"player_hands": {"A": ["2", "9"]}, "current_prize": 3}}
    assert strategy.gops_move(state, ["[2]", "[9]"]) == "[2]"


def test_gops_reads_a_hand_keyed_by_seat_index():
    state = {"you": "B", "players": ["A", "B"],
             "gameState": {"player_hands": {0: ["2"], 1: ["9"]}, "current_prize": 12}}
    assert strategy.gops_move(state, ["[2]", "[9]"]) == "[9]"


def test_gops_with_an_empty_hand_falls_back():
    state = {"you": "A", "gameState": {"player_hands": {}, "current_prize": 5}}
    assert strategy.gops_move(state, ["[2]"]) == "[2]"


@pytest.mark.parametrize(
    "game_id,moves,state",
    [
        ("tic-tac-toe", ["[0]", "[1]"], _ttt([["", "", ""], ["", "", ""], ["", "", ""]])),
        ("nim", ["[1 1]"], {"gameState": {"piles": [2]}}),
        ("kuhn-poker", ["[check]"], {"gameState": {"player_cards": {"you": "Q"}}}),
    ],
)
def test_move_for_returns_a_legal_move(game_id, moves, state):
    assert strategy.move_for(game_id, state, moves) in set(moves)


def test_move_for_an_unknown_game_returns_none():
    assert strategy.move_for("no-such-game", {"gameState": {}}, ["[1]"]) is None


def test_move_for_with_no_moves_returns_none():
    assert strategy.move_for("tic-tac-toe", {"gameState": {}}, []) is None


def test_a_broken_strategy_never_escapes(monkeypatch):
    # A strategy bug must degrade to the caller fallback, not crash a match.
    monkeypatch.setitem(strategy.STRATEGIES, "tic-tac-toe", lambda s, m: 1 / 0)
    assert strategy.move_for("tic-tac-toe", {"gameState": {}}, ["[0]"]) is None


def test_cell_token_normalises_upstream_shapes():
    assert strategy._cell_token(None) == ""
    assert strategy._cell_token(".") == ""
    assert strategy._cell_token(["X"]) == "X"
    assert strategy._cell_token("O") == "O"
