"""Per-game strategies for built-in players.

The default player used to pick a move by hashing its own name and taking
that index into the legal-move list: an arbitrary move, not a decision.
Measured: 40 tic-tac-toe games all won by the first mover, when optimal
play is a draw, so both sides were playing randomly.

Strategies read only `observation` (already trimmed by visibility), so they
see exactly what an LLM player sees -- the equality fairness tests assert.
"""

from __future__ import annotations

import re
from typing import Any

_EMPTY_TOKENS = {"", ".", "-", " "}


def _cell_token(value: Any) -> str:
    """Normalise a cell to X/O/empty. Upstreams use empty, dot or None."""
    if value is None or value == []:
        return ""
    if isinstance(value, list):
        return _cell_token(value[0]) if value else ""
    text = str(value).strip().strip("[]'\" ")
    return "" if text in _EMPTY_TOKENS else text


def _grid(state: dict):
    board = (state.get("gameState") or {}).get("board")
    if isinstance(board, list) and board and isinstance(board[0], list):
        return board
    return None


def _win_lines(n: int) -> list:
    out = [[(r, c) for c in range(n)] for r in range(n)]
    out += [[(r, c) for r in range(n)] for c in range(n)]
    out.append([(i, i) for i in range(n)])
    out.append([(i, n - 1 - i) for i in range(n)])
    return out


def _winner_on(grid: list, mark: str) -> bool:
    n = len(grid)
    return any(all(_cell_token(grid[r][c]) == mark for r, c in line) for line in _win_lines(n))


def _pick_sorted(moves: list, preferred: list) -> str:
    pool = set(moves)
    for item in preferred:
        if item in pool:
            return item
    return sorted(moves)[0] if moves else ""


def _index_of_move(move: str):
    digits = [ch for ch in str(move) if ch.isdigit()]
    if not digits:
        return None
    value = int("".join(digits))
    return value if 0 <= value <= 8 else None


# 棋盘状态 -> 最佳着法缓存。井字棋最多 3^9 个局面，缓存后整局只算一次；
# 纯函数式策略（局面决定着法）让缓存永远安全。
_TTT_CACHE: dict = {}


def _cache_key(flat: list, mine: str) -> str:
    return "".join(flat) + "|" + mine


def _marks_mine(state: dict, flat: list, marks: list):
    # Infer the mark from piece counts only. X always moves first in this
    # engine, so: equal counts -> X to move; X one ahead -> O to move.
    if not marks:
        return "X"
    counts = {m: flat.count(m) for m in marks}
    if len(marks) == 1:
        return marks[0]
    x_count = counts.get("X", 0)
    o_count = counts.get("O", 0)
    if x_count == o_count:
        return "X"
    return "O" if x_count > o_count else "X"



def tic_tac_toe_move(state: dict, moves: list, *, reverse: bool = False) -> str:
    # Full search on 3x3. Reverse mode flips it: three in a row loses.
    grid = _grid(state)
    if grid is None or len(grid) != 3:
        return sorted(moves)[0] if moves else ""
    flat = [_cell_token(cell) for row in grid for cell in row]
    marks = sorted({m for m in flat if m})
    mine = _marks_mine(state, flat, marks)
    theirs = next((m for m in marks if m != mine), "O" if mine == "X" else "X")

    def winner_now(board: list):
        g = [board[0:3], board[3:6], board[6:9]]
        mine_win = _winner_on(g, mine)
        theirs_win = _winner_on(g, theirs)
        if reverse:
            if mine_win:
                return "theirs"
            if theirs_win:
                return "mine"
        else:
            if mine_win:
                return "mine"
            if theirs_win:
                return "theirs"
        return "draw" if all(board) else None

    def search(board: list, depth: int, maximizing: bool, alpha: int = -10000, beta: int = 10000) -> int:
        # Alpha-beta pruning is load-bearing: without it an empty board is a
        # 9! node search and one decision took over three minutes, turning a
        # 3-minute suite into 18 minutes. With pruning: milliseconds.
        result = winner_now(board)
        if result == "mine":
            return 100 - depth
        if result == "theirs":
            return -100 + depth
        if result == "draw":
            return 0
        empty = [i for i, v in enumerate(board) if not v]
        if maximizing:
            best = -10000
            for i in empty:
                board[i] = mine
                best = max(best, search(board, depth + 1, False, alpha, beta))
                board[i] = ""
                alpha = max(alpha, best)
                if beta <= alpha:
                    break
            return best
        best = 10000
        for i in empty:
            board[i] = theirs
            best = min(best, search(board, depth + 1, True, alpha, beta))
            board[i] = ""
            beta = min(beta, best)
            if beta <= alpha:
                break
        return best

    key = _cache_key(flat, mine) + ("|rev" if reverse else "")
    cached = _TTT_CACHE.get(key)
    if cached is not None:
        index = _index_of_move(cached)
        if index is not None and not flat[index]:
            return cached

    best_move, best_score = None, -10000
    for move in sorted(moves):
        index = _index_of_move(move)
        if index is None or flat[index]:
            continue
        flat[index] = mine
        score = search(flat, 0, False)
        flat[index] = ""
        if score > best_score:
            best_move, best_score = move, score
    result = best_move or (sorted(moves)[0] if moves else "")
    if result:
        _TTT_CACHE[key] = result
    return result


def nim_move(state: dict, moves: list) -> str:
    # Optimal Nim: clear the xor sum. At xor 0 we are lost, play any move.
    piles = (state.get("gameState") or {}).get("piles")
    if not isinstance(piles, list) or not piles:
        return sorted(moves)[0] if moves else ""
    counts = [int(p) for p in piles if isinstance(p, int) or str(p).isdigit()]
    xor_all = 0
    for c in counts:
        xor_all ^= c
    if xor_all == 0:
        return sorted(moves)[0] if moves else ""
    for index, count in enumerate(counts):
        target = count ^ xor_all
        if target < count:
            return _pick_sorted(moves, ["[{} {}]".format(index + 1, count - target)])
    return sorted(moves)[0] if moves else ""


def _connect4_candidates(grid: list, moves: list) -> list:
    cols = len(grid[0]) if grid else 0
    open_cols = [c for c in range(cols) if not _cell_token(grid[0][c])]
    if not moves:
        return ["[col {}]".format(c) for c in open_cols]
    out = []
    for c in open_cols:
        for move in moves:
            nums = re.findall(r"\d+", move)
            if nums and int(nums[-1]) == c:
                out.append(move)
                break
    return out or list(moves)


def _cf_col(move: str) -> int:
    nums = re.findall(r"\d+", str(move))
    return int(nums[-1]) if nums else -1


def connect_four_move(state: dict, moves: list) -> str:
    # Win now > block their four > prefer the centre column.
    grid = _grid(state)
    if grid is None:
        return sorted(moves)[0] if moves else ""
    rows, cols = len(grid), len(grid[0])
    marks = sorted({_cell_token(c) for row in grid for c in row if _cell_token(c)})
    cand = _connect4_candidates(grid, moves)
    if not cand:
        return ""
    if not marks:
        center = cols // 2
        return cand[center] if len(cand) > center else cand[0]
    mine = "X" if "X" in marks else marks[0]
    theirs = next((m for m in marks if m != mine), "O" if mine == "X" else "X")
    board = [[_cell_token(c) for c in row] for row in grid]

    def drop(col: int, mark: str):
        for r in range(rows - 1, -1, -1):
            if not board[r][col]:
                board[r][col] = mark
                return r
        return None

    def four(mark: str) -> bool:
        for r in range(rows):
            for c in range(cols):
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    cells = [(r + dr * i, c + dc * i) for i in range(4)]
                    if all(0 <= rr < rows and 0 <= cc < cols and board[rr][cc] == mark for rr, cc in cells):
                        return True
        return False

    for move in cand:
        col = _cf_col(move)
        if col < 0:
            continue
        r = drop(col, mine)
        if r is not None:
            win = four(mine)
            board[r][col] = ""
            if win:
                return move
    for move in cand:
        col = _cf_col(move)
        if col < 0:
            continue
        r = drop(col, theirs)
        if r is not None:
            threat = four(theirs)
            board[r][col] = ""
            if threat:
                return move
    center = cols // 2
    return sorted(cand, key=lambda m: abs(_cf_col(m) - center))[0]


def _parse_coords(move: str):
    nums = re.findall(r"\d+", str(move))
    if len(nums) < 2:
        return None
    return int(nums[0]), int(nums[1])


def othello_move(state: dict, moves: list) -> str:
    # Corners are gold; the squares next to a corner hand the opponent one.
    grid = _grid(state)
    if grid is None:
        return sorted(moves)[0] if moves else ""
    n = len(grid)
    corners = {(0, 0), (0, n - 1), (n - 1, 0), (n - 1, n - 1)}
    # 紧邻角（对角一步）最危险：直接送对手一个角；再外一圈只是次差。
    near = set()
    outer = set()
    for cr, cc in corners:
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc2 = cr + dr, cc + dc
                if not (0 <= rr < n and 0 <= cc2 < n) or (rr, cc2) in corners:
                    continue
                near.add((rr, cc2))
    for cr, cc in corners:
        for dr in (-2, -1, 0, 1, 2):
            for dc in (-2, -1, 0, 1, 2):
                rr, cc2 = cr + dr, cc + dc
                if not (0 <= rr < n and 0 <= cc2 < n):
                    continue
                if (rr, cc2) in corners or (rr, cc2) in near:
                    continue
                outer.add((rr, cc2))
    scored = []
    for move in moves:
        coords = _parse_coords(move)
        if coords is None:
            continue
        r, c = coords
        if (r, c) in corners:
            score = 100
        elif (r, c) in near:
            score = -50
        elif (r, c) in outer:
            score = -20
        elif r in (0, n - 1) or c in (0, n - 1):
            score = 20
        else:
            score = 0
        scored.append((-score, move))
    if not scored:
        return sorted(moves)[0] if moves else ""
    scored.sort()
    return scored[0][1]


def _card_of(state: dict) -> str:
    cards = (state.get("gameState") or {}).get("player_cards")
    raw = None
    if isinstance(cards, dict):
        raw = cards.get(state.get("you"))
        if raw is None:
            players = state.get("players") or []
            you = state.get("you")
            if you in players:
                idx = players.index(you)
                raw = cards.get(idx + 1) or cards.get(idx)
            if raw is None:
                raw = next(iter(cards.values()), "")
    elif isinstance(cards, list) and cards:
        raw = cards[0]
    return str(raw or "").strip("[]'\" ").upper()[:1]


def kuhn_move(state: dict, moves: list) -> str:
    # Approximate equilibrium: K raises, Q calls, J bluffs only on small pots.
    rank = _card_of(state)
    gs = state.get("gameState") or {}
    pot = int(gs.get("pot") or 0)
    round_no = int(gs.get("current_round") or 0)
    aggressive = [m for m in moves if "raise" in m.lower() or "bet" in m.lower()]
    passive = [m for m in moves if m not in aggressive]
    if rank == "K":
        return _pick_sorted(moves, aggressive) if aggressive else (sorted(moves)[0] if moves else "")
    if rank == "J":
        if aggressive and pot <= 2 and round_no % 2 == 0:
            return _pick_sorted(moves, aggressive)
        return _pick_sorted(moves, passive) if passive else (sorted(moves)[0] if moves else "")
    call = [m for m in moves if "call" in m.lower() or "check" in m.lower()]
    return _pick_sorted(moves, call or passive) if (call or passive) else (sorted(moves)[0] if moves else "")


def _hand_of(state: dict) -> list:
    hands = (state.get("gameState") or {}).get("player_hands")
    mine = []
    if isinstance(hands, dict):
        raw = hands.get(state.get("you"))
        if raw is None:
            players = state.get("players") or []
            you = state.get("you")
            if you in players:
                idx = players.index(you)
                raw = hands.get(idx + 1) or hands.get(idx)
            if raw is None:
                raw = next(iter(hands.values()), [])
        mine = [str(x) for x in raw] if isinstance(raw, list) else []
    elif isinstance(hands, list):
        mine = [str(x) for x in hands]
    return mine


def gops_move(state: dict, moves: list) -> str:
    # Spend a big card on a big prize, a small card on a small prize.
    gs = state.get("gameState") or {}
    mine = _hand_of(state)
    try:
        prize = int(str(gs.get("current_prize")).strip("[]'\" "))
    except (TypeError, ValueError):
        prize = 7

    def rank(card: str) -> int:
        text = str(card).strip("[]'\" ").upper()
        table = {"A": 14, "K": 13, "Q": 12, "J": 11}
        if text in table:
            return table[text]
        try:
            return int(text)
        except ValueError:
            return 0

    if not mine:
        return sorted(moves)[0] if moves else ""
    ordered = sorted(mine, key=rank)
    want = ordered[-1] if prize >= 8 else ordered[0]
    return _pick_sorted(moves, ["[{}]".format(want)])


STRATEGIES = {
    "tic-tac-toe": lambda s, m: tic_tac_toe_move(s, m),
    "reverse-tic-tac-toe": lambda s, m: tic_tac_toe_move(s, m, reverse=True),
    "wild-tic-tac-toe": lambda s, m: tic_tac_toe_move(s, m),
    "nim": nim_move,
    "connect-four": connect_four_move,
    "othello": othello_move,
    "kuhn-poker": kuhn_move,
    "game-of-pure-strategy": gops_move,
}


def move_for(game_id: str, state: dict, moves: list):
    """Return a strategy move, or None when this game has no strategy yet."""
    handler = STRATEGIES.get(game_id)
    if handler is None or not moves:
        return None
    try:
        choice = handler(state, moves)
    except Exception:  # noqa: BLE001 - a strategy bug must not hang a match
        return None
    return choice if choice in set(moves) else None
