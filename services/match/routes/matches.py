"""比赛端点：建场、事件、回放视图、战报、状态、列表、赛事库、统计、SSE。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ..archive import list_matches as archive_matches
from ..archive import overview as archive_overview
from ..auth import AuthError, authenticate, issue_ticket, redeem_ticket, usage
from ..broadcast import global_broadcaster, presence_scope
from ..engine import registry as game_registry
from ..eventlog import EventLog, has_match, read_match, replay
from ..messages import LANGS as MESSAGE_LANGS
from ..messages import localize_payload, localize_recap
from ..player_specs import PlayerSpec
from ..ratelimit import check as rate_check
from ..recap import build_recap
from ..secrets import cipher_from_env, seal_specs
from ..settings import settings
from ..worker import enqueue_match, ensure_inline_worker
from .common import (
    auth_config,
    db_path_of,
    events_of,
    guard,
    quota,
    request_lang,
    sse_frame,
    visible,
)

router = APIRouter()

# 载荷里带可本地化文案的事件类型；其余事件的载荷是纯数据，不做处理。
LOCALIZABLE_EVENT_TYPES = frozenset({"narration.line", "director.cue", "match.ended"})


class MatchConfig(BaseModel):
    """赛制的可选配置。多 AI 编队靠这里表达，而不是每个赛制写死人数。

    `teams` / `teamSize` 目前由代号使用：
    - 默认 2 队 × 2 人 = 4 人（与旧行为完全一致）；
    - 3 队 × 2 人 = 6 个 AI；2 队 × 4 人 = 8 个 AI。

    `turnTimeoutMs` 是每回合的墙钟上限（1s–600s）：设置后 runner 真实强制，
    超时选手按弃权判负；不设置时不强制（旧行为）。
    """

    teams: int | None = Field(default=None, ge=2, le=8)
    teamSize: int | None = Field(default=None, ge=2, le=8)
    maxQuestions: int = Field(default=21, ge=4, le=60)
    turnTimeoutMs: int | None = Field(default=None, ge=1000, le=600_000)


class CreateMatch(BaseModel):
    game: str = "twenty-questions"
    seed: int | None = None
    maxQuestions: int = Field(default=21, ge=4, le=60)
    players: list[PlayerSpec] = Field(min_length=2, max_length=16)
    seasonId: str | None = Field(default=None, max_length=64)
    inlineWorker: bool = Field(default=True, description="在本进程顺带启动 worker，单机演示用")
    # 高级自定义：多队 / 多队员编队。不给就按赛制默认。
    config: MatchConfig = Field(default_factory=MatchConfig)


def _season_game(db_path: Path, season_id: str) -> str:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT game FROM seasons WHERE season_id = ?", (season_id,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="赛季不存在，先创建赛季")
    return row[0] or "twenty-questions"


def _validate_players(body: CreateMatch) -> list[str]:
    """校验赛制、人数与 id 唯一性，返回选手 id 列表。"""
    if body.game not in game_registry.available():
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持的赛制：{body.game}（可用：{', '.join(game_registry.available())}）",
        )
    players = [p.id for p in body.players]
    if len(set(players)) != len(players):
        raise HTTPException(status_code=400, detail="选手 id 必须唯一")
    low, high = game_registry.player_range(body.game)
    if not (low <= len(players) <= high):
        raise HTTPException(
            status_code=400,
            detail=f"{body.game} 需要 {low}–{high} 名选手，收到 {len(players)}",
        )
    _validate_roster(body, len(players))
    return players


def _validate_roster(body: "CreateMatch", count: int) -> None:
    """编队参数必须在建场时就校验，不能等 worker 跑到一半才炸。

    实测：4 个选手配 3 队 × 2 人（需 6 人）曾返回 201，任务入队后
    在 worker 里抛异常，前端只看到"比赛一直直播中"。
    """
    cfg = body.config
    wants_teams = cfg.teams is not None or cfg.teamSize is not None
    if not wants_teams:
        return
    teams = cfg.teams or 2
    team_size = cfg.teamSize or 2
    if body.game != "codenames":
        raise HTTPException(
            status_code=400,
            detail=f"编队参数（teams/teamSize）只对 codenames 生效，{body.game} 是固定人数赛制",
        )
    needed = teams * team_size
    if count != needed:
        raise HTTPException(
            status_code=400,
            detail=f"编队 {teams} 队 × 每队 {team_size} 人需要 {needed} 名选手，收到 {count}",
        )


def _check_season_game(db_path: Path, season_id: str, game: str) -> None:
    """赛季内所有对局必须同赛制，否则积分榜会把两种规则的结果混在一起。"""
    season_game = _season_game(db_path, season_id)
    if season_game and season_game != game:
        raise HTTPException(
            status_code=400,
            detail=f"赛季 {season_id} 使用赛制 {season_game}，不能加入 {game} 的对局",
        )


@router.post("/matches", status_code=201)
async def create_match(body: CreateMatch, request: Request) -> dict[str, Any]:
    db_path = db_path_of(request)
    principal, remaining = guard(request, "write_match")
    players = _validate_players(body)
    if body.seasonId is not None:
        # 先校验赛季存在再写库：否则 404 请求会留下一条永远没人跑的 live 比赛。
        _check_season_game(db_path, body.seasonId, body.game)

    match_id = f"m_{uuid.uuid4().hex[:12]}"
    seed = body.seed if body.seed is not None else int.from_bytes(uuid.uuid4().bytes[:4], "big")
    specs = [spec.model_dump() for spec in body.players]
    try:
        cipher = cipher_from_env()
        sealed_specs = seal_specs(specs, cipher)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="当前服务未配置安全的凭据封存密钥，无法创建外部 AI 比赛") from exc

    # 参数全部合法后才扣配额：写错参数不该烧额度。
    quota(db_path, principal, "matches")

    game = game_registry.get(body.game)
    log = EventLog.open(db_path, match_id)
    try:
        log.create_match(game=game.game_id, seed=seed, players=players)
    finally:
        log.close()

    # 写路径走队列：任何副本都能接手，副本崩了租约到期后会被别人重跑。
    # 建场语言写进 config：棋盘词等内容数据要与观看语言一致，
    # 否则英文界面里会出现中文棋盘——这是"语言错乱"最刺眼的一种。
    match_config = body.config.model_dump(exclude_none=True)
    match_config.setdefault("lang", request_lang(request))
    try:
        job_id = enqueue_match(
            db_path,
            match_id=match_id,
            seed=seed,
            players=players,
            max_questions=body.maxQuestions,
            season_id=body.seasonId,
            specs=specs,
            cipher=cipher,
            sealed_specs=sealed_specs,
            game=body.game,
            config=match_config,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="当前服务未配置安全的凭据封存密钥，无法创建外部 AI 比赛") from exc
    if body.inlineWorker and settings().inline_worker:
        ensure_inline_worker(db_path)

    return {
        "matchId": match_id,
        "seed": seed,
        "players": players,
        "seasonId": body.seasonId,
        "jobId": job_id,
        # 这是本分钟限流窗口的余量，不是每日配额（字段名保留兼容旧客户端）。
        "rateRemaining": remaining,
    }


@router.get("/matches/{match_id}/events")
async def events(
    match_id: str,
    request: Request,
    after: int = Query(default=-1, ge=-1),
    reveal: bool = Query(default=False, description="赛后揭秘：比赛结束后才可读私有事件"),
) -> dict[str, Any]:
    guard(request, "read")
    items = events_of(db_path_of(request), match_id, after=after, reveal=reveal)
    lang = request_lang(request)
    if lang in MESSAGE_LANGS:
        # 解说事件存的是模板键 + 参数，这里按请求语言重新展开；
        # 老事件没有 msg 字段，原样返回。
        items = [
            {**event, "payload": localize_payload(event.get("payload") or {}, lang)}
            if event.get("type") in LOCALIZABLE_EVENT_TYPES
            else event
            for event in items
        ]
    return {"matchId": match_id, "events": items, "nextAfter": items[-1]["seq"] if items else after}


@router.get("/matches/{match_id}/view")
async def view(match_id: str, request: Request) -> dict[str, Any]:
    guard(request, "read")
    return replay(events_of(db_path_of(request), match_id))


@router.get("/matches/{match_id}/view/as/{player}")
async def view_as(match_id: str, player: str, request: Request) -> dict[str, Any]:
    """某位选手在当时的视角。

    直播期用它做"队长视角"回放，赛后用来看"他当时看到了什么"。
    返回的是该选手可见的事件子集，前端复用同一个 reduce 即可。
    """
    guard(request, "read")
    events = read_match(db_path_of(request), match_id)
    if not events and not has_match(db_path_of(request), match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    if not any(event.get("player") == player for event in events):
        raise HTTPException(status_code=404, detail=f"这场比赛没有选手 {player}")
    scoped = [e for e in events if visible(e, player)]
    return {"matchId": match_id, "player": player, "events": scoped, "view": replay(scoped)}


@router.get("/matches/{match_id}/recap")
async def recap(match_id: str, request: Request) -> dict[str, Any]:
    guard(request, "read")
    lang = request_lang(request)
    return localize_recap(build_recap(events_of(db_path_of(request), match_id)), lang)


@router.get("/matches/{match_id}/export")
async def export_match(
    match_id: str,
    request: Request,
    reveal: bool = Query(default=False, description="赛后揭秘：比赛结束后才可读私有事件"),
) -> dict[str, Any]:
    """单文件可移植导出（Lichess PGN 的类比物）：事件流自包含快照。

    可见性与 /events 同一套契约：默认只导出 public 事件；
    `reveal=1` 揭秘私有事件（必须赛后），防止导出绕过 visibility 裁剪。
    """
    guard(request, "read")
    from ..portable import export_match

    try:
        document = export_match(db_path_of(request), match_id, reveal=reveal)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None
    if document is None:
        raise HTTPException(status_code=404, detail="比赛不存在")
    return document


@router.post("/matches/import")
async def import_match(request: Request) -> dict[str, Any]:
    """导入可移植导出文件，成为只读回放场次（不重跑引擎）。

    配额按"建场"计：导入会占一个 matchId，与新建同等代价。
    """
    db_path = db_path_of(request)
    principal, _remaining = guard(request, "write_match")
    body = await request.json()
    from ..portable import import_match

    quota(db_path, principal, "matches")
    try:
        result = import_match(db_path, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return result


@router.get("/matches/{match_id}/state")
async def match_state(match_id: str, request: Request) -> dict[str, Any]:
    """跨副本可读的状态快照：一律从日志重放，不读进程内缓存。

    建场后 worker 还没写下第一条事件的窗口里，日志是空的但 matches 行已存在。
    这时要返回 200 + pending，而不是 404——同一时刻 /events 已经是 200 空列表，
    两个端点对"已登记但未开写"的语义必须一致，否则观战页会闪一下"比赛不存在"。
    """
    from ..rehydrate import RehydrateError, rehydrate, summarize

    db_path = db_path_of(request)
    guard(request, "read")
    if not has_match(db_path, match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    try:
        return {**summarize(rehydrate(db_path, match_id)), "source": "log"}
    except RehydrateError:
        # 已登记、尚无事件：这是正常的建场窗口，不是错误。
        return {
            "matchId": match_id,
            "finished": False,
            "winner": None,
            "currentPlayer": None,
            "events": 0,
            "lastSeq": -1,
            "pending": True,
            "source": "log",
        }


@router.get("/matches")
async def list_matches(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> dict[str, Any]:
    """列出日志里出现过的比赛，跨副本可见。"""
    from ..rehydrate import known_matches

    guard(request, "read")
    ids = sorted(known_matches(db_path_of(request)))
    return {"count": len(ids), "matches": ids[-limit:]}


@router.get("/archive")
async def archive(
    request: Request,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
    game: str | None = Query(default=None, max_length=40),
    status: str | None = Query(default=None, pattern="^(live|ended)$"),
    q: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    """赛事库：带赛制/选手/胜负/事件数的比赛列表。

    `q` 同时搜选手名与 matchId（选手名单以 JSON 存在 matches.players）。
    """
    guard(request, "read")
    return archive_matches(
        db_path_of(request), limit=limit, offset=offset, game=game, status=status, q=q,
        lang=request_lang(request),
    )


@router.get("/stats")
async def stats(request: Request) -> dict[str, Any]:
    """全站统计：比赛数、事件数、赛制分布、胜者榜、近两周活动。"""
    guard(request, "read")
    data = archive_overview(db_path_of(request), lang=request_lang(request))
    try:
        data["viewers"] = global_broadcaster().total_viewers()
        data["viewerScope"] = presence_scope()
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 统计不能因为广播后端异常而 500
        data["viewers"] = 0
        data["viewerScope"] = "process"
    return data


@router.get("/matches/{match_id}/presence")
async def presence(match_id: str, request: Request) -> dict[str, Any]:
    """真实在线观赛人数（当前进程内 SSE 订阅数）。"""
    guard(request, "read")
    if not has_match(db_path_of(request), match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    return {
        "matchId": match_id,
        "viewers": global_broadcaster().viewer_count(match_id),
        # global = Redis 聚合所有副本；process = 只有本进程订阅数
        "scope": presence_scope(),
    }


@router.post("/auth/ticket")
async def auth_ticket(request: Request) -> dict[str, Any]:
    """用 Bearer 换一张 SSE 用的短时票据。"""
    principal, _remaining = guard(request, "read")
    return {"ticket": issue_ticket(principal), "ttlSec": 60}


@router.get("/quota")
async def quota_usage(request: Request) -> dict[str, Any]:
    principal, _remaining = guard(request, "read")
    return usage(db_path_of(request), principal)


@router.get("/matches/{match_id}/stream")
async def stream(
    match_id: str,
    request: Request,
    ticket: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    lastEventId: str | None = Query(default=None),
) -> EventSourceResponse:
    db_path = db_path_of(request)
    config = auth_config()
    if config.mode != "off":
        principal = redeem_ticket(ticket) if ticket else None
        if principal is None:
            try:
                authenticate(config, request.headers.get("authorization"))
            except AuthError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    if not rate_check(request, "stream")[0]:
        raise HTTPException(status_code=429, detail="请求过于频繁")
    # 允许只有日志的比赛（队列跑的、别的副本跑的）也能观战；
    # events_of 负责区分"不存在"（404）与"已登记但还没开写"（空列表）。
    events_of(db_path, match_id)

    try:
        raw_cursor = last_event_id if last_event_id is not None else lastEventId
        cursor = int(raw_cursor) if raw_cursor is not None else -1
    except ValueError:
        cursor = -1

    async def gen():
        nonlocal cursor
        subscription = global_broadcaster().subscribe(match_id)
        try:
            seen: set[int] = set()
            backlog = events_of(db_path, match_id, after=cursor)
            for event in backlog:
                seen.add(event["seq"])
                yield sse_frame(event)
            if backlog:
                # 游标必须跟着 backlog 前移：否则每次 15 秒回查都会重读同一批。
                cursor = backlog[-1]["seq"]
            if backlog and backlog[-1]["type"] == "match.ended":
                return

            while True:
                event = await subscription.get(timeout_s=15)
                if event is None:
                    # 没有新事件时回查日志：队列模式下比赛可能在别的副本里推进。
                    recent = [e for e in events_of(db_path, match_id, after=cursor) if e["seq"] not in seen]
                    if recent:
                        for item in recent:
                            seen.add(item["seq"])
                            if not visible(item, None):
                                continue
                            yield sse_frame(item)
                        cursor = recent[-1]["seq"]
                        if recent[-1]["type"] == "match.ended":
                            return
                        continue
                    # 心跳交给 EventSourceResponse 的 ping。
                    continue
                if event["seq"] in seen:
                    continue
                seen.add(event["seq"])
                cursor = event["seq"]
                # 私有事件（隐藏信息赛制的队长思考）不能推给普通观众。
                # 日志里仍保留全部事件，重放与战报按身份过滤。
                if not visible(event, None):
                    continue
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
