"""四子棋可落子列：结构化棋盘优先，观察文本兜底。

TextArena 适配器与内置脚本选手此前各写一份正则解析；文本里既有棋盘
又有历史落子行，`rows[0]` 不保证是顶行，两份实现迟早会分叉。这里只留一份。
"""

from __future__ import annotations

import re

_TOP_ROW = re.compile(r"[.XO]")


def columns_from_board(
    observations: list[str],
    columns: int = 7,
    board: list[list[str]] | None = None,
) -> list[str]:
    """返回 `[col i]` 形式的合法落子。

    优先读 `gameState.board` 的顶行；拿不到结构化棋盘时，才从观察文本里
    找第一行恰好 `columns` 个格子的文本——列号行不会被 `[.XO]` 匹配到。
    """
    if isinstance(board, list) and board and isinstance(board[0], list):
        top = [str(cell or ".") for cell in board[0]]
        if len(top) == columns:
            return [f"[col {index}]" for index, cell in enumerate(top) if cell == "."]
    rows: list[list[str]] = []
    for text in observations:
        for line in str(text).splitlines():
            cells = _TOP_ROW.findall(line)
            if len(cells) == columns:
                rows.append(cells)
    if not rows:
        return []
    top = rows[0]
    return [f"[col {i}]" for i, cell in enumerate(top) if cell == "."]
