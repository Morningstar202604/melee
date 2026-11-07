"""分享落地页：给社交平台爬虫的 OG 标签 + 给人看的跳转。

社交平台爬虫不会带 Authorization，所以默认公开；页面只含
赛制、胜者、一句话战报与分享卡，不含选手凭据或私有事件。
`MELEE_PUBLIC_SHARE=off` 可改回鉴权。
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..engine import registry as game_registry
from ..messages import format_message, localize_recap
from ..recap import build_recap
from ..settings import settings
from .common import db_path_of, events_of, guard, request_lang, safe_match_id

router = APIRouter()

_PAGE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · melee</title>
<meta name="description" content="{description}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="melee">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:image" content="{image}">
<meta property="og:url" content="{share_url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{image}">
<style>
/* 分享落地页与产品同一套配色：亮色默认，meta 里声明深色偏好时跟随。
   这个页面由爬虫抓取，不能依赖 JS 切换主题，只能用 prefers-color-scheme。 */
:root{{--bg:#F4F6FB;--panel:#FFFFFF;--txt:#0F1729;--dim:#4A5568;--line:#DDE4EF;--a:#0E7490;--on-a:#FFFFFF}}
@media (prefers-color-scheme:dark){{
:root{{--bg:#07090D;--panel:#11141B;--txt:#E8EAF2;--dim:#9AA3B5;--line:#232838;--a:#22D3EE;--on-a:#05070A}}
}}
body{{margin:0;background:var(--bg);color:var(--txt);font-family:"Segoe UI","PingFang SC",system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{max-width:760px;padding:32px;text-align:center}}
h1{{font-size:32px;margin:0 0 12px}}
p{{color:var(--dim);line-height:1.7}}
a{{display:inline-block;margin-top:20px;padding:10px 20px;border-radius:8px;background:var(--a);color:var(--on-a);
font-weight:700;text-decoration:none}}
img{{width:100%;border-radius:12px;border:1px solid var(--line);margin-top:20px}}
</style>
</head>
<body>
<div class="card">
<h1>{title}</h1>
<p>{description}</p>
<img src="{image}" alt="{title}">
<a href="{watch}">{cta}</a>
</div>
</body>
</html>"""


@router.get("/share/{match_id}", include_in_schema=False)
async def share_page(match_id: str, request: Request) -> HTMLResponse:
    if not settings().public_share:
        guard(request, "read")
    safe_match_id(match_id)
    lang = request_lang(request)
    events = events_of(db_path_of(request), match_id)
    recap = localize_recap(build_recap(events), lang)
    game = recap.get("game") or "twenty-questions"
    # 赛制名按语言取；字典里没有的新赛制退回注册表中文名。
    registry_label = next((item["label"] for item in game_registry.catalog() if item["gameId"] == game), game)
    label = format_message(f"game.{game}", lang=lang)
    if label == f"game.{game}":
        label = registry_label
    winner = recap.get("winner") or ""
    # 队伍名与赛制名都要落成当前语言；否则英文页会混进中文。
    team_names = {"red": ("Red", "红队"), "blue": ("Blue", "蓝队")}
    if winner in team_names:
        winner_label = team_names[winner][0 if lang == "en" else 1]
    else:
        winner_label = winner
    title = (
        format_message("share.title.win", {"game": label, "winner": winner_label}, lang=lang)
        if winner_label
        else format_message("share.title.live", {"game": label}, lang=lang)
    )
    description = recap.get("oneLine") or format_message("share.description.fallback", lang=lang)
    origin = str(request.base_url).rstrip("/")
    image = f"{origin}/matches/{match_id}/card.png"
    watch = f"{origin}/watch.html?match={match_id}"
    return HTMLResponse(
        _PAGE.format(
            lang="en" if lang == "en" else "zh-CN",
            title=html.escape(title),
            description=html.escape(description),
            image=html.escape(image),
            share_url=html.escape(f"{origin}/share/{match_id}"),
            watch=html.escape(watch),
            cta=html.escape(format_message("share.cta", lang=lang)),
        )
    )
