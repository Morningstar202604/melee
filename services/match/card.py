"""分享卡：把一场比赛画成 1200×630 的 PNG，给社交平台做 og:image。

为什么服务端画而不是前端截图：分享链接的预览图必须由静态 HTML 直接引用，
爬虫不会执行 JS；服务端画一次、缓存到磁盘，重复请求直接回文件。

颜色与网页/视频同一套色板，分享卡看起来才像同一个产品。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .archive import list_matches
from .render import _font

WIDTH = 1200
HEIGHT = 630

BG = (7, 9, 13)
PANEL = (17, 20, 27)
LINE = (35, 40, 56)
TEXT = (232, 234, 242)
DIM = (154, 163, 181)
FAINT = (92, 100, 120)
ACCENT = (34, 211, 238)
ACCENT_B = (245, 165, 36)
OK = (52, 211, 153)

_TEAM = {"red": "红队", "blue": "蓝队"}


def _find(db_path: str | Path, match_id: str) -> dict[str, Any] | None:
    for item in list_matches(db_path, limit=500)["items"]:
        if item["matchId"] == match_id:
            return item
    return None


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        current += char
        if len(current) >= width:
            lines.append(current)
            current = ""
    if current:
        lines.append(current)
    return lines or [""]


def render_card(db_path: str | Path, match_id: str, out_path: str | Path) -> Path:
    """画一张分享卡；已存在且比数据库旧就复用。"""
    from PIL import Image, ImageDraw

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    item = _find(db_path, match_id)
    if item is None:
        raise KeyError(match_id)

    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, WIDTH, 6], fill=ACCENT)
    draw.rectangle([60, 74, 66, 556], fill=PANEL)

    draw.text((96, 84), "MELEE / 转播台", font=_font(26), fill=ACCENT)
    draw.text((96, 128), item["gameLabel"], font=_font(58), fill=TEXT)
    draw.text((96, 208), " · ".join(item["players"]), font=_font(30), fill=DIM)

    winner = item.get("winner")
    label = _TEAM.get(winner, winner) if winner else ""
    if item["status"] == "ended":
        headline = f"{label} 胜" if label else "无人获胜"
        color = OK if winner else FAINT
    else:
        headline = "直播中"
        color = ACCENT_B
    draw.text((96, 300), headline, font=_font(86), fill=color)

    reason = (item.get("reason") or "").strip()
    if reason:
        for index, line in enumerate(_wrap(reason, 22)[:2]):
            draw.text((96, 410 + index * 44), line, font=_font(30), fill=DIM)

    stats = [
        ("事件", str(item["events"])),
        ("赛制", item["gameLabel"]),
        ("状态", "已结束" if item["status"] == "ended" else "直播中"),
    ]
    x = 96
    for key, value in stats:
        draw.text((x, 520), value, font=_font(34), fill=TEXT)
        draw.text((x, 562), key, font=_font(18), fill=FAINT)
        x += 180

    draw.text((WIDTH - 96, 520), "melee · 把 AI 对抗做成节目", font=_font(22), fill=FAINT, anchor="ra")
    draw.text((WIDTH - 96, 556), match_id, font=_font(20), fill=FAINT, anchor="ra")

    image.save(out_path, format="PNG", optimize=True)
    return out_path


def ensure_card(db_path: str | Path, match_id: str, cache_dir: str | Path) -> Path:
    """按需生成并缓存。

    已结束的比赛内容不再变化，直接复用磁盘上的 PNG；
    直播中的卡片会随胜负变化，每次重画（一张 1200×630 只要几十毫秒）。
    """
    cache = Path(cache_dir) / f"{match_id}.png"
    item = _find(db_path, match_id)
    if item is None:
        raise KeyError(match_id)
    if item["status"] == "ended" and cache.exists() and cache.stat().st_size > 0:
        # 只有"卡片生成时间晚于最后一条事件"才复用：直播中生成的卡
        # 在比赛结束后仍是"直播中"，必须重画。
        try:
            last = datetime.fromisoformat(str(item.get("lastAt") or "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            last = 0.0
        if cache.stat().st_mtime >= last:
            return cache
    return render_card(db_path, match_id, cache)
