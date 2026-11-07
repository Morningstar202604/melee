#!/usr/bin/env python
"""生成仓库级社交预览图（1280×640），用于 GitHub 社交预览与 README 头图。

为什么单独一张：单场比赛的分享卡（`/matches/{id}/card.png`）是运行时产物，
仓库门面需要一张不含具体比赛数据的静态图。

用法：
    python scripts/make_social_card.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.match.render import _font  # noqa: E402

WIDTH = 1280
HEIGHT = 640

# 亮色是产品默认（与网页同一套令牌），仓库门面跟它保持一致。
BG = (244, 246, 251)
PANEL = (255, 255, 255)
LINE = (221, 228, 239)
TEXT = (15, 23, 41)
DIM = (74, 85, 104)
FAINT = (95, 106, 126)
ACCENT = (14, 116, 144)
ACCENT_B = (161, 98, 7)
OK = (4, 120, 87)

GAMES = [
    ("Twenty Questions", "narrowing", ACCENT),
    ("Codenames", "hidden info", ACCENT_B),
    ("Tic-tac-toe", "deterministic", OK),
    ("Connect Four", "deterministic", OK),
    ("Othello", "deterministic", OK),
    ("Nim", "deterministic", OK),
]


def main() -> int:
    from PIL import Image, ImageDraw

    out = Path(__file__).resolve().parents[1] / "docs" / "assets" / "social-card.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # 顶部强调条 + 低对比网格（直接画浅色线，别用 blend 拉暗整张图）
    draw.rectangle([0, 0, WIDTH, 6], fill=ACCENT)
    grid = (226, 232, 241)
    for x in range(0, WIDTH, 44):
        draw.line([(x, 6), (x, HEIGHT)], fill=grid, width=1)
    for y in range(6, HEIGHT, 44):
        draw.line([(0, y), (WIDTH, y)], fill=grid, width=1)

    draw.text((72, 74), "MELEE / Broadcast", font=_font(30), fill=ACCENT)
    draw.text((72, 122), "Every move,", font=_font(64), fill=TEXT)
    draw.text((72, 208), "worth rewatching.", font=_font(64), fill=ACCENT_B)
    draw.text(
        (72, 302),
        "Self-hosted broadcast studio for AI matches",
        font=_font(26),
        fill=DIM,
    )
    draw.text(
        (72, 342),
        "Live · Replay · Directing · Recaps · Highlights · Sharing",
        font=_font(26),
        fill=DIM,
    )

    x = 72
    name_font = _font(23)
    tag_font = _font(17)
    for name, tag, tone in GAMES:
        # 胶囊宽度按文本实测（名称与标签取大者）：英文比中文长，写死宽度必截断。
        width = 34 + max(int(name_font.getlength(name)), int(tag_font.getlength(tag))) + 18
        draw.rounded_rectangle([x, 420, x + width, 486], radius=10, fill=PANEL, outline=LINE, width=1)
        draw.ellipse([x + 14, 440, x + 24, 450], fill=tone)
        draw.text((x + 34, 434), name, font=name_font, fill=TEXT)
        draw.text((x + 34, 462), tag, font=tag_font, fill=FAINT)
        x += width + 12

    draw.text((72, 540), "Python · FastAPI · SQLite · zero-build frontend", font=_font(22), fill=FAINT)
    draw.text((WIDTH - 72, 540), "github.com/badhope/melee", font=_font(22), fill=FAINT, anchor="ra")
    draw.text(
        (WIDTH - 72, 574),
        "The event log is the only source of truth - live and replay share one reducer",
        font=_font(18),
        fill=FAINT,
        anchor="ra",
    )

    image.save(out, format="PNG", optimize=True)
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
