"""分享卡：把一场比赛画成 1200×630 的 PNG，给社交平台做 og:image。

为什么服务端画而不是前端截图：分享链接的预览图必须由静态 HTML 直接引用，
爬虫不会执行 JS；服务端画一次、缓存到磁盘，重复请求直接回文件。

颜色与网页/视频同一套色板，分享卡看起来才像同一个产品。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .archive import find_match
from .render import _font

WIDTH = 1200
HEIGHT = 630

# 亮色是产品默认（与网页同一套令牌），深色卡片留给深色社交场景。
# 分享卡由爬虫抓取、无法跟随用户主题，所以在 URL 上用 ?theme=dark 显式选择。
BG = (244, 246, 251)
PANEL = (255, 255, 255)
LINE = (221, 228, 239)
TEXT = (15, 23, 41)
DIM = (74, 85, 104)
FAINT = (107, 118, 144)
ACCENT = (14, 116, 144)
ACCENT_B = (161, 98, 7)
OK = (4, 120, 87)

DARK = {
    "bg": (7, 9, 13),
    "panel": (17, 20, 27),
    "line": (35, 40, 56),
    "text": (232, 234, 242),
    "dim": (154, 163, 181),
    "faint": (92, 100, 120),
    "accent": (34, 211, 238),
    "accent_b": (245, 165, 36),
    "ok": (52, 211, 153),
}


def palette(dark: bool = False) -> dict[str, tuple[int, int, int]]:
    if not dark:
        return {"bg": BG, "panel": PANEL, "line": LINE, "text": TEXT, "dim": DIM,
                "faint": FAINT, "accent": ACCENT, "accent_b": ACCENT_B, "ok": OK}
    return dict(DARK)

from .engine.codenames import team_label as _team_label

_TEAM = {"red": "红队", "blue": "蓝队"}


def _find(db_path: str | Path, match_id: str) -> dict[str, Any] | None:
    """按 id 直查单场。

    之前遍历 `list_matches(limit=500)` 再线性找：一次分享卡请求会把
    最近 500 场的事件计数全部算一遍，比赛一多就是全库扫描。
    """
    return find_match(db_path, match_id)


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


def render_card(db_path: str | Path, match_id: str, out_path: str | Path, *, dark: bool = False) -> Path:
    """画一张分享卡；已存在且比数据库旧就复用。"""
    from PIL import Image, ImageDraw

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    item = _find(db_path, match_id)
    if item is None:
        raise KeyError(match_id)

    tone = palette(dark)
    image = Image.new("RGB", (WIDTH, HEIGHT), tone["bg"])
    draw = ImageDraw.Draw(image)

    draw.rectangle([0, 0, WIDTH, 6], fill=tone["accent"])
    draw.rectangle([60, 74, 66, 556], fill=tone["panel"])

    draw.text((96, 84), "MELEE / Broadcast", font=_font(26), fill=tone["accent"])
    draw.text((96, 128), item["gameLabel"], font=_font(58), fill=tone["text"])
    draw.text((96, 208), " · ".join(item["players"]), font=_font(30), fill=tone["dim"])

    winner = item.get("winner")
    label = _TEAM.get(winner) or (_team_label(winner) if isinstance(winner, str) and winner.startswith("team") else winner) if winner else ""
    if item["status"] == "ended":
        headline = f"{label} 胜" if label else "无人获胜"
        color = tone["ok"] if winner else tone["faint"]
    else:
        headline = "直播中"
        color = tone["accent_b"]
    draw.text((96, 300), headline, font=_font(86), fill=color)

    reason = (item.get("reason") or "").strip()
    if reason:
        for index, line in enumerate(_wrap(reason, 22)[:2]):
            draw.text((96, 410 + index * 44), line, font=_font(30), fill=tone["dim"])

    stats = [
        ("事件", str(item["events"])),
        ("赛制", item["gameLabel"]),
        ("状态", "已结束" if item["status"] == "ended" else "直播中"),
    ]
    x = 96
    for key, value in stats:
        draw.text((x, 520), value, font=_font(34), fill=tone["text"])
        draw.text((x, 562), key, font=_font(18), fill=tone["faint"])
        x += 180

    draw.text((WIDTH - 96, 520), "melee · AI matches, worth watching", font=_font(22), fill=tone["faint"], anchor="ra")
    draw.text((WIDTH - 96, 556), match_id, font=_font(20), fill=tone["faint"], anchor="ra")

    image.save(out_path, format="PNG", optimize=True)
    return out_path


def ensure_card(db_path: str | Path, match_id: str, cache_dir: str | Path, *, dark: bool = False) -> Path:
    """按需生成并缓存。

    已结束的比赛内容不再变化，直接复用磁盘上的 PNG；
    直播中的卡片会随胜负变化，每次重画（一张 1200×630 只要几十毫秒）。
    """
    cache = Path(cache_dir) / (f"{match_id}.dark.png" if dark else f"{match_id}.png")
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
    return render_card(db_path, match_id, cache, dark=dark)
