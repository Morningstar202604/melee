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
from .font import font
from .palette import DARK, LIGHT
from .teams import team_label_zh
from .textfit import wrap_text

WIDTH = 1200
HEIGHT = 630


def palette(dark: bool = False) -> dict[str, tuple[int, int, int]]:
    return DARK if dark else LIGHT


def _find(db_path: str | Path, match_id: str) -> dict[str, Any] | None:
    """按 id 直查单场。

    之前遍历 `list_matches(limit=500)` 再线性找：一次分享卡请求会把
    最近 500 场的事件计数全部算一遍，比赛一多就是全库扫描。
    """
    return find_match(db_path, match_id)


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

    draw.text((96, 84), "MELEE / Broadcast", font=font(26), fill=tone["accent"])
    draw.text((96, 128), item["gameLabel"], font=font(58), fill=tone["text"])
    draw.text((96, 208), " · ".join(item["players"]), font=font(30), fill=tone["dim"])

    winner = item.get("winner")
    label = team_label_zh(winner) if winner else ""
    if item["status"] == "ended":
        headline = f"{label} 胜" if label else "无人获胜"
        color = tone["ok"] if winner else tone["faint"]
    else:
        headline = "直播中"
        color = tone["accent_b"]
    draw.text((96, 300), headline, font=font(86), fill=color)

    reason = (item.get("reason") or "").strip()
    if reason:
        for index, line in enumerate(wrap_text(reason, 22)[:2]):
            draw.text((96, 410 + index * 44), line, font=font(30), fill=tone["dim"])

    stats = [
        ("事件", str(item["events"])),
        ("赛制", item["gameLabel"]),
        ("状态", "已结束" if item["status"] == "ended" else "直播中"),
    ]
    x = 96
    for key, value in stats:
        draw.text((x, 520), value, font=font(34), fill=tone["text"])
        draw.text((x, 562), key, font=font(18), fill=tone["faint"])
        x += 180

    draw.text((WIDTH - 96, 520), "melee · AI matches, worth watching", font=font(22), fill=tone["faint"], anchor="ra")
    draw.text((WIDTH - 96, 556), match_id, font=font(20), fill=tone["faint"], anchor="ra")

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
