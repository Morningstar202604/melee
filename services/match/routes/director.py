"""导播端点：列出直播中的比赛、镜头状态、人工切镜。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..broadcast import global_broadcaster
from ..control import CueError, auto_cue_summary, live_matches, push_cue, recent_cues
from .common import db_path_of, events_of, guard

router = APIRouter()


@router.get("/director/matches")
async def director_matches(request: Request) -> dict[str, Any]:
    guard(request, "read")
    return {"matches": live_matches(db_path_of(request))}


@router.get("/director/{match_id}")
async def director_state(match_id: str, request: Request) -> dict[str, Any]:
    guard(request, "read")
    db_path = db_path_of(request)
    events = events_of(db_path, match_id)
    return {
        "matchId": match_id,
        "eventCount": len(events),
        "cues": auto_cue_summary(db_path, match_id),
        "recent": recent_cues(db_path, match_id),
        "lastEvents": events[-12:],
    }


@router.post("/director/{match_id}/cue")
async def director_cue(match_id: str, request: Request) -> dict[str, Any]:
    guard(request, "write_season")
    try:
        body = await request.json()
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 非法 JSON 是客户端错误，不该 500
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须是对象")
    try:
        event = push_cue(
            db_path_of(request),
            match_id,
            shot=str(body.get("shot", "")),
            target=body.get("target") or None,
            duration_ms=int(body.get("durationMs", 1200)),
            note=str(body.get("note", ""))[:100],
        )
    except CueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"参数不合法：{exc}") from None
    global_broadcaster().publish(match_id, event)
    return {"ok": True, "event": event}
