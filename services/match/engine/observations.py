"""上游观察文本的共用判定：只认上游自己写的"无子可下"句子。

TextArena 适配器与内置脚本选手各自维护过一份同样的正则；选手层不能
反向 import 适配器，所以把这条最小规则放到两者都能依赖的模块里。
"""

from __future__ import annotations

import re

_NO_MOVES = re.compile(
    r"(no valid moves|no legal moves|may have to skip|had to skip|must skip)",
    re.I,
)


def says_no_moves(observations: list[str]) -> bool:
    """观测是否明确说"你没有可下的子、需要跳过"。

    只认上游原话，不做语义猜测——猜错会让选手白白弃权。
    """
    return any(_NO_MOVES.search(str(text)) for text in observations[-2:])
