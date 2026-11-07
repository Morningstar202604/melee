"""媒体端点：高光剪辑清单、渲染、下载、分享卡。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..auth import AuthError, authenticate, issue_media_ticket, redeem_media_ticket
from ..card import ensure_card
from ..clips import build_clips, export_manifest
from ..eventlog import has_match
from ..render import find_ffmpeg, normalize_template
from ..settings import settings
from ..worker import enqueue_render
from .common import auth_config, db_path_of, events_of, guard, quota, safe_match_id

router = APIRouter()


@router.post("/matches/{match_id}/media-ticket")
async def media_ticket(match_id: str, request: Request) -> dict[str, Any]:
    """给 <video> 用的媒体票据：可复用、绑定这一场比赛、10 分钟。"""
    safe_match_id(match_id)
    principal, _remaining = guard(request, "read")
    return {"ticket": issue_media_ticket(principal, match_id), "ttlSec": 600}


@router.get("/matches/{match_id}/card.png", include_in_schema=False)
async def match_card(match_id: str, request: Request, theme: str = Query(default="light")) -> FileResponse:
    """分享卡：1200×630 PNG，供 og:image 使用。

    爬虫带不了 Authorization：`MELEE_PUBLIC_SHARE=on`（默认）时公开，
    只暴露已在分享页展示的摘要；关掉则与其它读接口一样要鉴权。
    """
    db_path = db_path_of(request)
    if not settings().public_share:
        guard(request, "read")
    safe_match_id(match_id)
    if not has_match(db_path, match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    cache_dir = db_path.parent / "cards"
    try:
        path = ensure_card(db_path, match_id, cache_dir, dark=(theme == "dark"))
    except KeyError:
        raise HTTPException(status_code=404, detail="比赛不存在") from None
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=60"})


@router.get("/matches/{match_id}/clips")
async def clips(match_id: str, request: Request, limit: int = Query(default=3, ge=1, le=10)) -> dict[str, Any]:
    guard(request, "read")
    events = events_of(db_path_of(request), match_id)
    return export_manifest(match_id, events, limit=limit)


@router.post("/matches/{match_id}/clips/render")
async def render_clips_endpoint(
    match_id: str,
    request: Request,
    limit: int = Query(default=1, ge=1, le=5),
    perEventMs: int = Query(default=900, ge=200, le=5000),
    template: str = Query(default="broadcast", pattern="^(broadcast|spotlight|scorecard)$"),
    asyncMode: bool = Query(default=False, alias="async"),
) -> dict[str, Any]:
    db_path = db_path_of(request)
    principal, _remaining = guard(request, "render")
    if not find_ffmpeg():
        raise HTTPException(status_code=503, detail="服务器上没有 ffmpeg，无法渲染视频")

    events = events_of(db_path, match_id)
    clips_to_render = build_clips(events, limit=limit, per_event_ms=perEventMs)
    if not clips_to_render:
        raise HTTPException(status_code=404, detail="这场比赛没有可剪的高光")
    # 确认真的有可剪内容后再扣渲染配额。
    quota(db_path, principal, "renders")

    out_dir = db_path.parent / "clips" / match_id
    loop = asyncio.get_running_loop()

    def _run() -> list[dict[str, Any]]:
        from ..render import render_clip as _render

        results = []
        for index, clip in enumerate(clips_to_render, start=1):
            result = _render(
                clip,
                out_dir / f"{index:02d}-{clip.kind}.mp4",
                per_event_ms=perEventMs,
                all_events=events,
                template=template,
            )
            results.append(
                {
                    "kind": clip.kind,
                    "title": clip.title,
                    "file": result.path.name,
                    "frames": result.frames,
                    "durationMs": result.duration_ms,
                    "bytes": result.bytes,
                    "template": template,
                }
            )
        return results

    if asyncMode:
        job_id = enqueue_render(
            db_path, match_id=match_id, limit=limit, per_event_ms=perEventMs, template=template
        )
        return {"matchId": match_id, "jobId": job_id, "status": "queued", "template": template}

    rendered = await loop.run_in_executor(None, _run)
    for item in rendered:
        item["url"] = f"/matches/{match_id}/clips/{item['file']}"
    return {"matchId": match_id, "template": normalize_template(template), "clips": rendered}


@router.get("/matches/{match_id}/clips/{filename}")
async def download_clip(
    match_id: str,
    filename: str,
    request: Request,
    ticket: str | None = Query(default=None),
) -> FileResponse:
    # 视频靠 <video> 拉，带不了 Authorization；token 模式下走媒体票据。
    safe_match_id(match_id)
    config = auth_config()
    if config.mode != "off":
        principal = redeem_media_ticket(ticket, match_id) if ticket else None
        if principal is None:
            try:
                authenticate(config, request.headers.get("authorization"))
            except AuthError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    path = db_path_of(request).parent / "clips" / match_id / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="剪辑不存在")
    return FileResponse(path, media_type="video/mp4", filename=filename)
