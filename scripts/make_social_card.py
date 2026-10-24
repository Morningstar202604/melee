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

BG = (7, 9, 13)
PANEL = (17, 20, 27)
LINE = (35, 40, 56)
TEXT = (232, 234, 242)
DIM = (154, 163, 181)
FAINT = (92, 100, 120)
ACCENT = (34, 211, 238)
ACCENT_B = (245, 165, 36)
OK = (52, 211, 153)

GAMES = [
    ("二十问", "提问收窄", ACCENT),
    ("代号", "隐藏信息", ACCENT_B),
    ("井字棋", "确定性", OK),
    ("四子棋", "确定性", OK),
    ("黑白棋", "确定性", OK),
    ("取石子", "确定性", OK),
]


def main() -> int:
    from PIL import Image, ImageDraw

    out = Path(__file__).resolve().parents[1] / "docs" / "assets" / "social-card.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # 顶部强调条 + 低对比网格（直接画深色线，别用 blend 拉亮整张图）
    draw.rectangle([0, 0, WIDTH, 6], fill=ACCENT)
    grid = (13, 16, 22)
    for x in range(0, WIDTH, 44):
        draw.line([(x, 6), (x, HEIGHT)], fill=grid, width=1)
    for y in range(6, HEIGHT, 44):
        draw.line([(0, y), (WIDTH, y)], fill=grid, width=1)

    draw.text((72, 74), "MELEE / 转播台", font=_font(30), fill=ACCENT)
    draw.text((72, 122), "把 AI 对抗做成节目", font=_font(64), fill=TEXT)
    draw.text((72, 208), "而不是评测页", font=_font(64), fill=ACCENT_B)
    draw.text(
        (72, 302),
        "六种确定性赛制 · 实时直播 · 可拖动回放 · 自动+人工导播",
        font=_font(26),
        fill=DIM,
    )
    draw.text(
        (72, 342),
        "事实校验战报 · 服务端渲染高光 · 分享卡 · 真实在线人数",
        font=_font(26),
        fill=DIM,
    )

    x = 72
    for name, tag, tone in GAMES:
        width = 168
        draw.rounded_rectangle([x, 420, x + width, 486], radius=10, fill=PANEL, outline=LINE, width=1)
        draw.ellipse([x + 14, 440, x + 24, 450], fill=tone)
        draw.text((x + 34, 434), name, font=_font(23), fill=TEXT)
        draw.text((x + 34, 462), tag, font=_font(17), fill=FAINT)
        x += width + 12

    draw.text((72, 540), "Python · FastAPI · SQLite · 零构建前端", font=_font(22), fill=FAINT)
    draw.text((WIDTH - 72, 540), "github.com/badhope/melee", font=_font(22), fill=FAINT, anchor="ra")
    draw.text(
        (WIDTH - 72, 574),
        "433 tests · 91.1% line coverage · 8 gates green",
        font=_font(18),
        fill=FAINT,
        anchor="ra",
    )

    image.save(out, format="PNG", optimize=True)
    print(f"写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
