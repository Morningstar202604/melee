"""文本折行：分享卡按字符数，视频/分享卡渲染按显示列宽。

两处都曾各写一份逐字符累加的循环；差别只在「中文算不算两列」和
「是否把换行压成空格」。这里把差异变成参数，排版结果保持不变。
"""

from __future__ import annotations


def wrap_text(text: str, width: int, *, wide: bool = False, flatten: bool = False) -> list[str]:
    """按宽度折行。

    wide=True 时按显示列宽（CJK 算 2 列），否则按字符数；
    flatten=True 时先把换行压成空格，适合标题/单段文案。
    """
    source = str(text).replace("\n", " ") if flatten else text
    lines: list[str] = []
    current = ""
    for char in source:
        current += char
        measured = (
            sum(2 if ord(item) > 0x2E80 else 1 for item in current)
            if wide
            else len(current)
        )
        threshold = width * 2 if wide else width
        if measured >= threshold:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines or [""]
