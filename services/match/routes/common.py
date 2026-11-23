"""路由层共用件：鉴权、配额、ID 校验、事件读取。

拆出来的目的：这些是 33 个端点共享的前置逻辑，之前全部内联在 api.py 里，
任何一条规则改动都要在 1100 行里找三遍。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from sse_starlette.sse import ServerSentEvent

from ..auth import (
    AuthError,
    Principal,
    QuotaConfig,
    authenticate,
    check_and_consume,
)
from ..db import open_db
from ..engine import registry as game_registry
from ..eventlog import has_match, read_match
from ..language import LANGS, normalize_lang, parse_accept_language
from ..messages import localize_payload
from ..ratelimit import _DEFAULT, PROFILES
from ..ratelimit import check as rate_check
from ..season import Season, load


def auth_config() -> QuotaConfig:
    """每次读环境，避免模块导入时求值锁死配置。

    测试与运维都会在进程运行中改这些变量；缓存住会让配置看起来"不生效"。
    """
    return QuotaConfig.from_env()


def request_lang(request: Request) -> str:
    """解析请求语言：?lang= 优先，其次 Accept-Language，最后英文。

    产品主语言是英文，所以默认值不是中文——这决定了分享页、OG 卡片
    在没有语言偏好时看到的是英文。
    """
    query = normalize_lang(request.query_params.get("lang"), default="")
    if query in LANGS:
        return query
    return parse_accept_language(request.headers.get("accept-language", ""))


def db_path_of(request: Request) -> Path:
    """当前应用的数据库路径。测试通过 patch api.DB_PATH 后重建 app 注入。"""
    return request.app.state.db_path


def validate_players(
    game: str,
    players: list[str],
    *,
    supports: int | None = None,
    supports_error: str | None = None,
    enforce_range: bool = True,
) -> list[str]:
    """赛制可用、id 唯一、人数在引擎声明的区间内。

    建场与排赛季过去各写一遍同样的三条判断；赛季只多一条「该赛制必须
    能按两人开打」，用 supports 传入，不再复制整段。
    """
    available = game_registry.available()
    if game not in available:
        raise HTTPException(
            status_code=400,
            detail=f"暂不支持的赛制：{game}（可用：{', '.join(available)}）",
        )
    if len(set(players)) != len(players):
        raise HTTPException(status_code=400, detail="选手 id 必须唯一")
    low, high = game_registry.player_range(game)
    if supports is not None and not (low <= supports <= high):
        raise HTTPException(
            status_code=400,
            detail=supports_error or f"{game} 不支持 {supports} 人对局",
        )
    if enforce_range and not (low <= len(players) <= high):
        raise HTTPException(
            status_code=400,
            detail=f"{game} 需要 {low}–{high} 名选手，收到 {len(players)}",
        )
    return players


def guard(request: Request, profile: str) -> tuple[Principal, int]:
    """限流 + 鉴权，返回（身份，剩余额度）。

    429 与成功响应都带 IETF RateLimit 头（draft-ietf-httpapi-ratelimit-headers）：
    `RateLimit: "profile";r=<剩余>;t=<窗口剩余秒>` 与 `RateLimit-Policy`。
    客户端据此可以主动退避，不用先撞一次 429 才知道被限流。
    这些头是附赠信息——没有它们功能照样能用，所以渲染失败也不能影响响应。
    """
    allowed, remaining = rate_check(request, profile)
    headers = rate_limit_headers(profile, remaining)
    if not allowed:
        headers["Retry-After"] = "60"
        raise HTTPException(status_code=429, detail="请求过于频繁", headers=headers)
    try:
        principal = authenticate(auth_config(), request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc), headers=headers) from None
    # 把本次限流信息挂到 request 上，中间件统一写进响应头：
    # FastAPI 的路由签名里没有 response 参数，逐端点加会漏。
    request.state.rate_limit = headers
    return principal, remaining


def ticket_principal(
    request: Request,
    config: QuotaConfig,
    ticket: str | None,
    redeem: Callable[[str], Principal | None],
) -> Principal | None:
    """票据优先、Bearer 兜底：SSE 与媒体下载共用的入口鉴权。

    ticket 模式关闭时直接放行（返回 None）；开着但没有有效票据，
    再回落到 Authorization，失败就转成 HTTPException。
    """
    if config.mode == "off":
        return None
    principal = redeem(ticket) if ticket else None
    if principal is not None:
        return principal
    try:
        return authenticate(config, request.headers.get("authorization"))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


def rate_limit_headers(profile: str, remaining: int) -> dict[str, str]:
    """按 IETF 草案渲染限流头。未知 profile 就退回默认档位的说明。"""
    expression = PROFILES.get(profile, _DEFAULT)
    count, _, window = expression.partition("/")
    seconds = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(window.strip(), 60)
    return {
        "RateLimit-Policy": f'"{profile}";q={count};w={seconds}',
        "RateLimit": f'"{profile}";r={max(0, int(remaining))};t={seconds}',
    }


def quota(db_path: Path, principal: Principal, kind: str, *, amount: int = 1) -> None:
    """扣额度。amount>1 用于「一次推进 N 场」这种批量操作。"""
    try:
        check_and_consume(db_path, principal, kind, auth_config(), amount=amount)
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


def sse_frame(event: dict[str, Any], lang: str = "") -> ServerSentEvent:
    """一条事件 → 一个 SSE 帧；lang 非空时按请求语言重渲染载荷。

    这件事必须在服务端做：前端拿到的是已定稿的 `text`/`trigger`，
    再翻一遍等于把双语词表复制到浏览器。REST 回放与 SSE 现在走同一
    `localize_payload()`，不会再出现「界面中文、解说英文」。
    """
    payload = event.get("payload")
    if lang and isinstance(payload, dict):
        event = {**event, "payload": localize_payload(payload, lang)}
    return ServerSentEvent(id=str(event["seq"]), data=json.dumps(event, ensure_ascii=False))


def events_of(
    db_path: Path,
    match_id: str,
    *,
    after: int = -1,
    viewer: str | None = None,
    reveal: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """从日志读一场事件；不存在时 404，已登记但未开写时返回空列表。

    比赛由 worker 异步执行，刚建场的前几百毫秒日志为空但 matches 行已存在，
    这时要返回 200 空列表，不能误判为 404。

    `viewer` 是观看者身份（选手 id）。None 表示普通观众，只能看 public 事件；
    私有事件（`visibility: {players:[...]}`）在服务端就被过滤掉，
    不依赖前端自觉——这是 `visibility` 契约的落地方式。

    `reveal=True` 是赛后揭秘：比赛必须先结束，否则拒绝。揭秘不改变事件本身，
    只是不再过滤——所以"直播期看不到、赛后能回看"这一条由服务端保证。

    `limit` 下推到 SQL：分页要真的少读，不能在内存里切。
    """
    try:
        events = read_match(db_path, match_id, after, limit)
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 表不存在等一律视为没有这场
        events = []
    if not events and after < 0 and not has_match(db_path, match_id):
        raise HTTPException(status_code=404, detail="比赛不存在")
    if reveal:
        if not any(event["type"] == "match.ended" for event in events):
            raise HTTPException(
                status_code=403,
                detail="比赛尚未结束，不能揭秘：隐藏信息赛制的看点就是直播期看不到",
            )
        return events
    return [e for e in events if visible(e, viewer)]


def parse_start_at(value: str | float | None) -> float | None:
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

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"startAt 不是合法时间：{text}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def safe_match_id(match_id: str) -> str:
    """match_id 会参与文件路径拼接，必须拒绝路径分隔符与盘符。

    路由参数可以是 URL 编码后的 `..` 或 `C:/Windows`；不校验就能读走
    SQLite 主库（含 quotas 与队列里的密文）甚至系统文件。
    """
    bad_chars = ("/", chr(92), ":", chr(0))
    if (
        not match_id
        or len(match_id) > 64
        or match_id in {".", ".."}
        or any(char in match_id for char in bad_chars)
        or ".." in match_id
    ):
        raise HTTPException(status_code=400, detail="非法比赛 ID")
    return match_id


_SEASON_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def safe_season_id(season_id: str) -> str:
    """season_id 会拼进对局 id 与文件路径，也是 SQL 主键，限制成安全字符集。"""
    if not _SEASON_ID_PATTERN.fullmatch(season_id) or ".." in season_id:
        raise HTTPException(
            status_code=400,
            detail="赛季 ID 只能包含字母、数字、下划线、点和连字符（1–64 字符）",
        )
    return season_id


def load_season_or_404(db_path: Path, season_id: str) -> Season:
    with open_db(db_path) as conn:
        try:
            return load(conn, season_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="赛季不存在") from None


def remaining_fixtures(season: Season) -> int:
    """还没结果的场次数，用于推进赛季时扣配额。"""
    done = {(r["playerA"], r["playerB"]) for r in season.results}
    done |= {(r["playerB"], r["playerA"]) for r in season.results}
    return sum(1 for _round, a, b, _at in season.fixtures if (a, b) not in done)
