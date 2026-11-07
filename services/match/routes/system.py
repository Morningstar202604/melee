"""系统端点：健康探针、赛制清单、任务查询、调度状态。"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .. import scheduler as scheduler_module
from .. import workqueue
from ..engine import registry as game_registry
from ..messages import game_label
from ..scheduler import ScheduleConfig
from .common import db_path_of, guard, request_lang

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """探针：数据库可读 + 队列可读。失败返回 503，而不是 200 假活。"""
    db_path = db_path_of(request)
    checks: dict[str, Any] = {"db": False, "queue": False}
    try:
        conn = sqlite3.connect(str(db_path), timeout=2)
        try:
            conn.execute("SELECT 1")
            checks["db"] = True
        finally:
            conn.close()
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 探针只报告，不抛
        pass
    try:
        workqueue.list_jobs(db_path, limit=1)
        checks["queue"] = True
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001
        pass
    ok = all(checks.values())
    payload = {"status": "ok" if ok else "degraded", "checks": checks}
    return JSONResponse(status_code=200 if ok else 503, content=payload)


@router.get("/ops/health")
async def ops_health(request: Request) -> JSONResponse:
    """运维健康：任务积压、失败率、卡住的比赛。

    与 /health 分工：/health 只回答依赖是否可用（探针每秒打，必须便宜）；
    这里扫 jobs 表回答"有没有开始劣化"，由 Prometheus 或人按需拉。
    有任一项超阈值返回 503，便于告警规则直接按状态码触发。
    """
    from ..ops import snapshot

    data = snapshot(db_path_of(request))
    return JSONResponse(status_code=200 if data["status"] == "ok" else 503, content=data)


@router.get("/ops/metrics", include_in_schema=False)
async def ops_metrics(request: Request) -> PlainTextResponse:
    """运维指标的 Prometheus 文本（与 /metrics 的 HTTP 指标分开抓取）。"""
    from ..ops import prometheus_text, snapshot

    return PlainTextResponse(prometheus_text(snapshot(db_path_of(request))))


@router.get("/games")
async def games(request: Request) -> dict[str, Any]:
    guard(request, "read")
    lang = request_lang(request)
    items = [
        {**item, "label": game_label(item["gameId"], lang)}
        for item in game_registry.catalog()
    ]
    return {"games": items, "count": len(game_registry.available())}


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, request: Request) -> dict[str, Any]:
    guard(request, "read")
    job = workqueue.get(db_path_of(request), job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job.as_dict()


@router.get("/jobs")
async def job_list(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    guard(request, "read")
    jobs = workqueue.list_jobs(db_path_of(request), status=status, limit=limit)
    return {"count": len(jobs), "jobs": [job.as_dict() for job in jobs]}


@router.get("/scheduler")
async def scheduler_status(request: Request) -> dict[str, Any]:
    guard(request, "read")
    scheduler = scheduler_module.current()
    config = scheduler.config if scheduler else ScheduleConfig.from_env()
    # enabled 反映配置意图（部署语义），而非"这个进程碰巧启动了线程"：
    # TestClient 不跑 lifespan、多副本部署里只有部分进程跑 tick，
    # 用进程状态回答"调度开没开"会在这些场景撒谎。
    return {
        "enabled": bool(config.enabled),
        "seasonIds": config.season_ids,
        "intervalSec": config.interval_s,
        "perTick": config.per_tick,
        "ticks": scheduler.ticks if scheduler else 0,
        "lastError": scheduler.last_error if scheduler else None,
    }


@router.post("/scheduler/tick")
async def scheduler_tick(request: Request) -> dict[str, Any]:
    """手动推进一次（不启动常驻线程）。"""
    guard(request, "write_season")
    # season_ids=None 是合法的自动发现模式；只有 MELEE_SCHEDULE=off 时才没有可推进的东西。
    config = ScheduleConfig.from_env()
    if not config.enabled:
        raise HTTPException(status_code=409, detail="调度器已关闭（MELEE_SCHEDULE=off），可改用 /seasons/{id}/run")

    loop = asyncio.get_running_loop()
    scheduler = scheduler_module.SeasonScheduler(config, db_path_of(request))
    played = await loop.run_in_executor(None, scheduler.tick)
    return {"played": played, "count": len(played)}
