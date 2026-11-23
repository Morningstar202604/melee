"""赛季端点：建赛季、列表、详情、推进。"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..db import open_db
from ..player_specs import PlayerSpec
from ..schedule import SeasonBusyError, run_season
from ..season import Season, load, persist
from .common import (
    db_path_of,
    guard,
    load_season_or_404,
    parse_start_at,
    quota,
    remaining_fixtures,
    safe_season_id,
    validate_players,
)

router = APIRouter()


class SeasonCreate(BaseModel):
    seasonId: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    players: list[PlayerSpec] = Field(min_length=2, max_length=32)
    # 赛季统一赛制：只接受 2 人赛制（循环赛按两人对局记分）。
    game: str = Field(default="twenty-questions", max_length=40)
    # 排期：给 startAt（ISO 时间或 unix 秒）就按轮次排开，每轮相隔 intervalMinutes。
    startAt: str | float | None = None
    intervalMinutes: float = Field(default=30.0, ge=1.0, le=1440.0)
    # 双循环：每对选手主客各一场。单循环下"宿敌"（≥2 场）永远为空，
    # 被实测发现。API 层保持单循环默认（不改变既有调用方的行为），
    # 前端弹窗默认勾选它，让新建赛季的宿敌面板有数据。
    doubleRoundRobin: bool = False


@router.get("/seasons")
async def list_seasons(request: Request) -> dict[str, Any]:
    """列出全部赛季，供赛季页做入口。"""
    guard(request, "read")
    with open_db(db_path_of(request), rows=True) as conn:
        try:
            rows = conn.execute(
                "SELECT s.season_id, s.name, s.game, s.created_at, "
                "(SELECT COUNT(*) FROM fixtures f WHERE f.season_id = s.season_id) AS fixtures, "
                "(SELECT COUNT(*) FROM results r WHERE r.season_id = s.season_id) AS played "
                "FROM seasons s ORDER BY s.created_at DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        return {
            "count": len(rows),
            "seasons": [
                {
                    "seasonId": row["season_id"],
                    "name": row["name"],
                    "game": row["game"] or "twenty-questions",
                    "createdAt": row["created_at"],
                    "fixtures": row["fixtures"],
                    "played": row["played"],
                }
                for row in rows
            ],
        }


@router.post("/seasons", status_code=201)
async def create_season(body: SeasonCreate, request: Request) -> dict[str, Any]:
    guard(request, "write_season")
    safe_season_id(body.seasonId)
    validate_players(
        body.game,
        [p.id for p in body.players],
        supports=2,
        supports_error=f"{body.game} 不是双人赛制，无法排赛季",
        enforce_range=False,
    )

    season = Season(season_id=body.seasonId, name=body.name, game=body.game)
    for spec in body.players:
        season.add_player(spec.id, spec.id, model=spec.model.model if spec.model else "")

    start_at = parse_start_at(body.startAt)
    season.build_round_robin(
        start_at=start_at,
        interval_minutes=body.intervalMinutes if start_at is not None else None,
        double=bool(body.doubleRoundRobin),
    )

    with open_db(db_path_of(request)) as conn:
        # 同一 seasonId 重建：先清旧 roster/fixtures/results，避免两批选手混榜。
        persist(conn, season, replace=True)

    return {
        "seasonId": season.season_id,
        "name": season.name,
        "game": season.game,
        "fixtures": [
            {"round": r, "a": a, "b": b, "scheduledAt": scheduled} for r, a, b, scheduled in season.fixtures
        ],
    }


@router.get("/seasons/{season_id}")
async def get_season(season_id: str, request: Request) -> dict[str, Any]:
    guard(request, "read")
    season = load_season_or_404(db_path_of(request), season_id)

    played_pairs = {(row["playerA"], row["playerB"]) for row in season.results}
    return {
        "seasonId": season.season_id,
        "name": season.name,
        "game": season.game,
        "standings": [s.as_dict() for s in season.standings()],
        "rivalries": season.rivalries(),
        "results": season.results,
        "fixtures": [
            {
                "round": r,
                "a": a,
                "b": b,
                "scheduledAt": scheduled,
                "played": (a, b) in played_pairs or (b, a) in played_pairs,
            }
            for r, a, b, scheduled in season.fixtures
        ],
    }


@router.post("/seasons/{season_id}/run")
async def run_season_endpoint(
    season_id: str,
    request: Request,
    maxFixtures: int | None = Query(default=None, ge=1, le=200),
) -> dict[str, Any]:
    db_path = db_path_of(request)
    principal, _remaining = guard(request, "write_season")
    safe_season_id(season_id)
    season = load_season_or_404(db_path, season_id)
    # 不传 maxFixtures 会跑完整个赛季：必须按"剩余场次"扣配额，
    # 而不是固定扣 1 格。否则一次请求就能绕过建场上限跑掉整个赛季。
    remaining = remaining_fixtures(season)
    planned = remaining if maxFixtures is None else min(maxFixtures, remaining)
    if planned > 0:
        quota(db_path, principal, "matches", amount=planned)
    try:
        run = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_season(season_id, db_path=db_path, max_fixtures=maxFixtures),
        )
    except SeasonBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    season = load_season_or_404(db_path, season_id)
    return {
        "seasonId": season_id,
        "played": run.played,
        "skipped": run.skipped,
        "matches": [o.as_dict() for o in run.outcomes],
        "standings": [s.as_dict() for s in season.standings()],
    }


__all__ = ["router", "SeasonCreate", "load", "load_season_or_404"]
