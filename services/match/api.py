"""比赛服务的 HTTP 层：建场、SSE 观战、事件回放。

设计约束（来自计划文档）：
- 事件日志是唯一真相，接口只做读与创建，不做状态推断。
- SSE 支持 Last-Event-ID，断线重连从下一条开始，不重不漏。
- 观众拿到的 payload 已按 visibility 过滤；服务端不做前端的安全判断。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import logging
import os

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sse_starlette.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from .engine import registry as game_registry
from .engine.base import Game
from .broadcast import Broadcaster, Subscription, global_broadcaster
from .clips import build_clips, export_manifest
from .control import CueError, auto_cue_summary, live_matches, push_cue, recent_cues
from .render import find_ffmpeg
from .eventlog import EventLog, has_match, read_match, replay
from .player_specs import PlayerSpec
from .recap import build_recap
from .rehydrate import RehydrateError, known_matches, rehydrate, summarize
from .auth import (
    AuthError,
    Principal,
    QuotaConfig,
    authenticate,
    check_and_consume,
    issue_media_ticket,
    issue_ticket,
    redeem_media_ticket,
    redeem_ticket,
    usage,
)
from .ratelimit import check as rate_check
from .schedule import run_season
from .scheduler import SeasonScheduler, ScheduleConfig, start_from_env
from .season import Season, load, persist

DB_PATH = Path(os.environ.get("MELEE_DB", "data/match.db"))
logger = logging.getLogger("melee.match")

class Bus:
    """广播门面。底层可换内存或 Redis，接口不变。

    事件仍由事件日志持有；广播只负责"通知有人有新事件了"。
    这样即使 Redis 丢消息，客户端重连也能靠 Last-Event-ID 补齐。
    """

    def __init__(self, backend: Broadcaster | None = None) -> None:
        self._backend = backend or global_broadcaster()

    def subscribe(self, match_id: str) -> Subscription:
        return self._backend.subscribe(match_id)

    def publish(self, match_id: str, event: dict[str, Any]) -> None:
        self._backend.publish(match_id, event)

    def publish_nowait(self, match_id: str, event: dict[str, Any]) -> None:
        """从非事件循环线程投递事件。"""
        self._backend.publish(match_id, event)


@dataclass
class MatchHandle:
    match_id: str
    game: Game
    state: dict[str, Any]
    log: EventLog
    season_id: str | None = None
    result: Any = None
    finished: asyncio.Event = field(default_factory=asyncio.Event)


BUS = Bus()
MATCHES: dict[str, MatchHandle] = {}


_AUTH_CACHE: dict[str, QuotaConfig] = {}


def auth_config() -> QuotaConfig:
    """每次读环境，避免模块导入时求值锁死配置。

    测试与运维都会在进程运行中改这些变量；缓存住会让配置看起来"不生效"。
    """
    return QuotaConfig.from_env()


def _guard(request: Request | None, profile: str) -> tuple[Principal, int]:
    """限流 + 鉴权，返回（身份，剩余额度）。"""
    if request is not None:
        allowed, remaining = rate_check(request, profile)
        if not allowed:
            raise HTTPException(status_code=429, detail="请求过于频繁", headers={"Retry-After": "60"})
    else:
        remaining = -1

    try:
        principal = authenticate(auth_config(), request.headers.get("authorization") if request else None)
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    return principal, remaining


def _quota(principal: Principal, kind: str) -> None:
    try:
        check_and_consume(DB_PATH, principal, kind, auth_config())
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


def visible(event: dict[str, Any], player: str | None) -> bool:
    """可见性过滤：public 对所有人可见，players 只对该选手可见。"""
    scope = event.get("visibility", "public")
    if scope == "public":
        return True
    if not isinstance(scope, dict):
        return True
    return player in scope.get("players", [])


def sse_frame(event: dict[str, Any]) -> ServerSentEvent:
    """一条事件 → 一个 SSE 帧。

    帧格式交给 sse-starlette：它会处理心跳、断连检测与正确的转义，
    手写 "id:/data:" 时这些都要自己维护，且容易在代理层被缓冲。
    """
    return ServerSentEvent(id=str(event["seq"]), data=json.dumps(event, ensure_ascii=False))


class CreateMatch(BaseModel):
    game: str = "twenty-questions"
    seed: int | None = None
    maxQuestions: int = Field(default=21, ge=4, le=60)
    players: list[PlayerSpec] = Field(min_length=2, max_length=8)
    seasonId: str | None = Field(default=None, max_length=64)
    inlineWorker: bool = Field(default=True, description="在本进程顺带启动 worker，单机演示用")


class SeasonCreate(BaseModel):
    seasonId: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    players: list[PlayerSpec] = Field(min_length=2, max_length=32)
    # 排期：给 startAt（ISO 时间或 unix 秒）就按轮次排开，每轮相隔 intervalMinutes。
    startAt: str | float | None = None
    intervalMinutes: float = Field(default=30.0, ge=1.0, le=1440.0)




_INLINE_WORKER: dict[str, Any] = {"worker": None}


def _ensure_inline_worker() -> None:
    """单机模式下，让 API 进程自己也当 worker。

    绑定当前的 DB_PATH：换库（测试或切环境）时必须换 worker，
    否则旧 worker 会继续消费旧库，新库的任务永远没人跑。
    """
    from .worker import Worker, WorkerConfig

    current = _INLINE_WORKER.get("worker")
    if current is not None and Path(current.db_path) == Path(DB_PATH):
        return
    if current is not None:
        current.stop()

    worker = Worker(DB_PATH, WorkerConfig.from_env())
    worker.start()
    _INLINE_WORKER["worker"] = worker


def _stop_inline_worker() -> None:
    worker = _INLINE_WORKER.get("worker")
    if worker is not None:
        worker.stop()
        _INLINE_WORKER["worker"] = None


def _events_of(match_id: str, *, after: int = -1) -> list[dict[str, Any]]:
    """先看进程内，再回落到日志文件。赛季批量跑出的比赛没有 handle。

    比赛已登记但 worker 还没写第一条事件时返回空列表（200），
    只有 matches 表里也没有才 404——否则刚建场的前几百毫秒会被误判为不存在。
    """
    handle = MATCHES.get(match_id)
    if handle is not None:
        return [e for e in handle.log.read(after) if visible(e, None)]

    try:
        events = read_match(DB_PATH, match_id, after)
    except Exception:  # noqa: BLE001 - 表不存在等一律视为没有这场
        events = []
    if not events and after < 0 and not has_match(DB_PATH, match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    return [e for e in events if visible(e, None)]


def _parse_start_at(value: str | float | None) -> float | None:
    """把 startAt 归一成 unix 秒。支持数字与 ISO 8601。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    from datetime import datetime, timezone

    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _load_or_404(season_id: str) -> Season:
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    try:
        return load(conn, season_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="赛季不存在") from None
    finally:
        conn.close()


def _assert_season_exists(season_id: str) -> None:
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT 1 FROM seasons WHERE season_id = ?", (season_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="赛季不存在，先创建赛季")


_SCHEDULER_REF: dict[str, Any] = {"instance": None}


def body_env_worker_enabled() -> bool:
    """MELEE_INLINE_WORKER=off 可关掉内嵌 worker（多副本各自跑独立 worker 时）。"""
    import os

    return os.environ.get("MELEE_INLINE_WORKER", "on").strip().lower() not in {"off", "0", "false", "no"}


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _SCHEDULER_REF["instance"] = start_from_env(DB_PATH)
        if body_env_worker_enabled():
            _ensure_inline_worker()
        try:
            yield
        finally:
            _stop_inline_worker()
            scheduler = _SCHEDULER_REF["instance"]
            if scheduler is not None:
                scheduler.stop()
                _SCHEDULER_REF["instance"] = None

    app = FastAPI(
        title="melee match service",
        version="0.1.0",
        lifespan=lifespan,
        # token 模式下不公开接口表：/docs 与 /openapi.json 之前无需鉴权即可读。
        docs_url=None if auth_config().mode != "off" else "/docs",
        redoc_url=None if auth_config().mode != "off" else "/redoc",
        openapi_url=None if auth_config().mode != "off" else "/openapi.json",
    )


    # 观众端与比赛服务分开部署（静态站点 + API），本地开发跨端口。
    # 上线时用 MELEE_CORS_ORIGINS 收窄；默认 * 只适合本机演示。
    origins = [item.strip() for item in os.environ.get("MELEE_CORS_ORIGINS", "*").split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        """安全响应头 + 请求体上限。

        没有这些头时，任何同源注入都能被浏览器当作脚本执行；
        没有体积上限时，一个 2.2MB 的 script 列表会被原样接收并落库。
        """
        limit = int(os.environ.get("MELEE_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse(status_code=413, content={"detail": f"请求体超过 {limit} 字节上限"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        if auth_config().mode != "off":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.get("/health")
    async def health() -> JSONResponse:
        """探针：数据库可读 + 队列可读。失败返回 503，而不是 200 假活。"""
        checks: dict[str, Any] = {"db": False, "queue": False}
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=2)
            try:
                conn.execute("SELECT 1")
                checks["db"] = True
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - 探针只报告，不抛
            pass
        try:
            from . import workqueue

            workqueue.list_jobs(DB_PATH, limit=1)
            checks["queue"] = True
        except Exception:  # noqa: BLE001
            pass
        ok = all(checks.values())
        payload = {"status": "ok" if ok else "degraded", "checks": checks}
        return JSONResponse(status_code=200 if ok else 503, content=payload)

    @app.get("/metrics")
    async def metrics(request: Request) -> Any:
        """最小可用指标：事件数、任务数、队列积压。"""
        _guard(request, "read")
        from . import workqueue

        conn = sqlite3.connect(str(DB_PATH), timeout=2)
        try:
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        except sqlite3.OperationalError:
            events = matches = 0
        finally:
            conn.close()
        jobs = workqueue.list_jobs(DB_PATH, limit=500)
        pending = sum(1 for job in jobs if job.status == workqueue.PENDING)
        running = sum(1 for job in jobs if job.status == workqueue.RUNNING)
        failed = sum(1 for job in jobs if job.status == workqueue.FAILED)
        lines = [
            f"melee_events_total {events}",
            f"melee_matches_total {matches}",
            f"melee_jobs_pending {pending}",
            f"melee_jobs_running {running}",
            f"melee_jobs_failed {failed}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    @app.post("/matches", status_code=201)
    async def create_match(body: CreateMatch, request: Request) -> dict[str, Any]:
        principal, remaining = _guard(request, "write_match")
        _quota(principal, "matches")

        if body.game not in game_registry.available():
            raise HTTPException(
                status_code=400,
                detail=f"暂不支持的赛制：{body.game}（可用：{', '.join(game_registry.available())}）",
            )

        match_id = f"m_{uuid.uuid4().hex[:12]}"
        seed = body.seed if body.seed is not None else int.from_bytes(uuid.uuid4().bytes[:4], "big")
        players = [p.id for p in body.players]
        if len(set(players)) != len(players):
            raise HTTPException(status_code=400, detail="选手 id 必须唯一")

        low, high = game_registry.player_range(body.game)
        if not (low <= len(players) <= high):
            raise HTTPException(
                status_code=400,
                detail=f"{body.game} 需要 {low}–{high} 名选手，收到 {len(players)}",
            )

        game = game_registry.get(body.game)
        log = EventLog.open(DB_PATH, match_id)
        log.create_match(game=game.game_id, seed=seed, players=players)

        if body.seasonId is not None:
            _assert_season_exists(body.seasonId)

        # 写路径走队列：任何副本都能接手，副本崩了租约到期后会被别人重跑。
        # inlineWorker=true 时同时启一个本地 worker，单进程部署开箱即用。
        from .worker import enqueue_match

        from .secrets import cipher_from_env

        job_id = enqueue_match(
            DB_PATH,
            match_id=match_id,
            seed=seed,
            players=players,
            max_questions=body.maxQuestions,
            season_id=body.seasonId,
            specs=[spec.model_dump() for spec in body.players],
            cipher=cipher_from_env(),
            game=body.game,
        )
        if body.inlineWorker:
            _ensure_inline_worker()

        return {
            "matchId": match_id,
            "seed": seed,
            "players": players,
            "seasonId": body.seasonId,
            "jobId": job_id,
            "quotaRemaining": remaining,
        }

    @app.get("/matches/{match_id}/events")
    async def events(match_id: str, request: Request, after: int = Query(default=-1, ge=-1)) -> dict[str, Any]:
        _guard(request, "read")
        items = _events_of(match_id, after=after)
        return {"matchId": match_id, "events": items, "nextAfter": items[-1]["seq"] if items else after}

    @app.get("/matches/{match_id}/view")
    async def view(match_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        return replay(_events_of(match_id))

    @app.get("/matches/{match_id}/recap")
    async def recap(match_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        return build_recap(_events_of(match_id))

    @app.post("/seasons", status_code=201)
    async def create_season(body: SeasonCreate, request: Request) -> dict[str, Any]:
        _guard(request, "write_season")
        players = [p.id for p in body.players]
        if len(set(players)) != len(players):
            raise HTTPException(status_code=400, detail="选手 id 必须唯一")

        season = Season(season_id=body.seasonId, name=body.name)
        for spec in body.players:
            season.add_player(spec.id, spec.id, model=spec.model.model if spec.model else "")

        start_at = _parse_start_at(body.startAt)
        season.build_round_robin(
            start_at=start_at,
            interval_minutes=body.intervalMinutes if start_at is not None else None,
        )

        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        try:
            persist(conn, season)
        finally:
            conn.close()

        return {
            "seasonId": season.season_id,
            "name": season.name,
            "fixtures": [
                {"round": r, "a": a, "b": b, "scheduledAt": scheduled} for r, a, b, scheduled in season.fixtures
            ],
        }

    @app.get("/seasons/{season_id}")
    async def get_season(season_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        try:
            season = load(conn, season_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="赛季不存在") from None
        finally:
            conn.close()

        played_pairs = {(row["playerA"], row["playerB"]) for row in season.results}
        return {
            "seasonId": season.season_id,
            "name": season.name,
            "standings": [s.as_dict() for s in season.standings()],
            "rivalries": season.rivalries(),
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

    @app.get("/matches/{match_id}/state")
    async def match_state(match_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        """跨副本可读的状态快照。

        进程内有 handle 就用内存态（更新），否则从日志重放。
        """
        # 一律从日志重放：handle.state 是比赛开始时的快照，
        # runner 在其作用域内换成了新 state，外面的 handle 永远停在开局。
        # 重放既正确又能跨副本，没有理由再分两条路。
        try:
            return {**summarize(rehydrate(DB_PATH, match_id)), "source": "log"}
        except RehydrateError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    @app.post("/auth/ticket")
    async def auth_ticket(request: Request) -> dict[str, Any]:
        """用 Bearer 换一张 SSE 用的短时票据。"""
        principal, _remaining = _guard(request, "read")
        return {"ticket": issue_ticket(principal), "ttlSec": 60}

    @app.post("/matches/{match_id}/media-ticket")
    async def media_ticket(match_id: str, request: Request) -> dict[str, Any]:
        """给 <video> 用的媒体票据：可复用、绑定这一场比赛、10 分钟。"""
        principal, _remaining = _guard(request, "read")
        return {"ticket": issue_media_ticket(principal, match_id), "ttlSec": 600}

    @app.get("/quota")
    async def quota(request: Request) -> dict[str, Any]:
        principal, _remaining = _guard(request, "read")
        return usage(DB_PATH, principal)

    @app.get("/scheduler")
    async def scheduler_status(request: Request) -> dict[str, Any]:
        _guard(request, "read")
        scheduler = _SCHEDULER_REF["instance"]
        config = scheduler.config if scheduler else ScheduleConfig.from_env()
        return {
            "enabled": bool(scheduler),
            "seasonIds": config.season_ids,
            "intervalSec": config.interval_s,
            "perTick": config.per_tick,
            "ticks": scheduler.ticks if scheduler else 0,
            "lastError": scheduler.last_error if scheduler else None,
        }

    @app.post("/scheduler/tick")
    async def scheduler_tick(request: Request) -> dict[str, Any]:
        """手动推进一次（不启动常驻线程）。"""
        _guard(request, "write_season")
        config = ScheduleConfig.from_env()
        if not config.season_ids:
            raise HTTPException(status_code=400, detail="未配置 MELEE_SCHEDULE，先从 /seasons/{id}/run 手动跑")

        loop = asyncio.get_running_loop()
        scheduler = SeasonScheduler(config, DB_PATH)
        played = await loop.run_in_executor(None, scheduler.tick)
        return {"played": played, "count": len(played)}

    @app.get("/matches")
    async def list_matches(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
        _guard(request, "read")
        """列出日志里出现过的比赛，跨副本可见。"""
        ids = known_matches(DB_PATH)
        live = [match_id for match_id in MATCHES]
        merged = sorted(set(ids) | set(live))
        return {"count": len(merged), "matches": merged[-limit:]}

    @app.get("/jobs/{job_id}")
    async def job_status(job_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        from . import workqueue

        job = workqueue.get(DB_PATH, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job.as_dict()

    @app.get("/jobs")
    async def job_list(
        request: Request,
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        _guard(request, "read")
        from . import workqueue

        jobs = workqueue.list_jobs(DB_PATH, status=status, limit=limit)
        return {"count": len(jobs), "jobs": [job.as_dict() for job in jobs]}

    @app.get("/games")
    async def games(request: Request) -> dict[str, Any]:
        _guard(request, "read")
        return {"games": game_registry.catalog(), "count": len(game_registry.available())}

    @app.get("/director/matches")
    async def director_matches(request: Request) -> dict[str, Any]:
        _guard(request, "read")
        return {"matches": live_matches(DB_PATH)}

    @app.get("/director/{match_id}")
    async def director_state(match_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "read")
        events = _events_of(match_id)
        return {
            "matchId": match_id,
            "cues": auto_cue_summary(DB_PATH, match_id),
            "recent": recent_cues(DB_PATH, match_id),
            "lastEvents": events[-12:],
        }

    @app.post("/director/{match_id}/cue")
    async def director_cue(match_id: str, request: Request) -> dict[str, Any]:
        _guard(request, "write_season")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="请求体必须是对象")
        try:
            event = push_cue(
                DB_PATH,
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
        BUS.publish(match_id, event)
        return {"ok": True, "event": event}

    @app.get("/matches/{match_id}/clips")
    async def clips(match_id: str, request: Request, limit: int = Query(default=3, ge=1, le=10)) -> dict[str, Any]:
        _guard(request, "read")
        events = _events_of(match_id)
        return export_manifest(match_id, events, limit=limit)

    @app.post("/matches/{match_id}/clips/render")
    async def render_clips_endpoint(
        match_id: str,
        request: Request,
        limit: int = Query(default=1, ge=1, le=5),
        perEventMs: int = Query(default=900, ge=200, le=5000),
        asyncMode: bool = Query(default=False, alias="async"),
    ) -> dict[str, Any]:
        principal, _remaining = _guard(request, "render")
        _quota(principal, "renders")
        asyncio_mode = asyncMode
        if not find_ffmpeg():
            raise HTTPException(status_code=503, detail="服务器上没有 ffmpeg，无法渲染视频")

        events = _events_of(match_id)
        clips = build_clips(events, limit=limit, per_event_ms=perEventMs)
        if not clips:
            raise HTTPException(status_code=404, detail="这场比赛没有可剪的高光")

        out_dir = DB_PATH.parent / "clips" / match_id
        loop = asyncio.get_running_loop()

        def _run() -> list[dict[str, Any]]:
            from .render import render_clip as _render

            results = []
            for index, clip in enumerate(clips, start=1):
                result = _render(
                    clip,
                    out_dir / f"{index:02d}-{clip.kind}.mp4",
                    per_event_ms=perEventMs,
                    all_events=events,
                )
                results.append(
                    {
                        "kind": clip.kind,
                        "title": clip.title,
                        "file": result.path.name,
                        "frames": result.frames,
                        "durationMs": result.duration_ms,
                        "bytes": result.bytes,
                    }
                )
            return results

        if asyncio_mode:
            from .worker import enqueue_render

            job_id = enqueue_render(DB_PATH, match_id=match_id, limit=limit, per_event_ms=perEventMs)
            return {"matchId": match_id, "jobId": job_id, "status": "queued"}

        rendered = await loop.run_in_executor(None, _run)
        for item in rendered:
            item["url"] = f"/matches/{match_id}/clips/{item['file']}"
        return {"matchId": match_id, "clips": rendered}

    @app.get("/matches/{match_id}/clips/{filename}")
    async def download_clip(
        match_id: str,
        filename: str,
        request: Request,
        ticket: str | None = Query(default=None),
    ) -> FileResponse:
        # 视频靠 <video> 拉，带不了 Authorization；token 模式下走媒体票据。
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
        path = DB_PATH.parent / "clips" / match_id / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="剪辑不存在")
        return FileResponse(path, media_type="video/mp4", filename=filename)

    @app.post("/seasons/{season_id}/run")
    async def run_season_endpoint(
        season_id: str,
        request: Request,
        maxFixtures: int | None = Query(default=None, ge=1, le=200),
    ) -> dict[str, Any]:
        _guard(request, "write_season")
        _load_or_404(season_id)
        run = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_season(season_id, db_path=DB_PATH, max_fixtures=maxFixtures),
        )
        season = _load_or_404(season_id)
        return {
            "seasonId": season_id,
            "played": run.played,
            "skipped": run.skipped,
            "matches": [o.as_dict() for o in run.outcomes],
            "standings": [s.as_dict() for s in season.standings()],
        }

    @app.get("/matches/{match_id}/stream")
    async def stream(
        match_id: str,
        request: Request,
        ticket: str | None = Query(default=None),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        # token 模式下优先用票据，避免把凭据写进 URL 与访问日志。
        config = auth_config()
        if config.mode != "off":
            principal = redeem_ticket(ticket) if ticket else None
            if principal is None:
                try:
                    principal = authenticate(config, request.headers.get("authorization"))
                except AuthError as exc:
                    raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        if not rate_check(request, "stream")[0]:
            raise HTTPException(status_code=429, detail="请求过于频繁")
        # 允许只有日志的比赛（队列跑的、别的副本跑的）也能观战；
        # _events_of 负责区分"不存在"（404）与"已登记但还没开写"（空列表）。
        if MATCHES.get(match_id) is None:
            _events_of(match_id)

        try:
            cursor = int(last_event_id) if last_event_id is not None else -1
        except ValueError:
            cursor = -1

        async def gen() -> AsyncIterator[ServerSentEvent]:
            nonlocal cursor
            subscription = BUS.subscribe(match_id)
            try:
                seen: set[int] = set()
                backlog = [e for e in _events_of(match_id, after=cursor)]
                for event in backlog:
                    seen.add(event["seq"])
                    yield sse_frame(event)
                if backlog and backlog[-1]["type"] == "match.ended":
                    return

                while True:
                    event = await subscription.get(timeout_s=15)
                    if event is None:
                        # 没有新事件时回查日志：队列模式下比赛可能在别的副本里推进。
                        recent = [e for e in _events_of(match_id, after=cursor) if e["seq"] not in seen]
                        if recent:
                            for item in recent:
                                seen.add(item["seq"])
                                yield sse_frame(item)
                            cursor = recent[-1]["seq"]
                            if recent[-1]["type"] == "match.ended":
                                return
                            continue
                        # 心跳交给 EventSourceResponse 的 ping，这里不再手写注释帧。
                        continue
                    if event["seq"] in seen:
                        continue
                    seen.add(event["seq"])
                    cursor = event["seq"]
                    yield sse_frame(event)
                    if event["type"] == "match.ended":
                        return
            finally:
                subscription.close()

        return EventSourceResponse(
            gen(),
            ping=15,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


app = create_app()
