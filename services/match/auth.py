"""鉴权与配额：把"谁在用"和"能用多少"分开管。

三种模式（按部署形态选）：
- `off`（默认）：只监听本机时不需要，任何请求都放行。
- `token`：请求头 `Authorization: Bearer <token>`，token 来自 `MELEE_API_TOKENS`
  （逗号分隔，格式 `name:token`）。适合小规模私有部署。
- 配额：每个 token 每天能建多少场、渲染多少次，存在 `quotas` 表里按 UTC 日期滚动。

为什么不做完整账号体系：这不是多租户 SaaS，缺失的是"防止别人白用你的模型额度"，
一个静态 token + 日配额足够；再往上就该交给网关或 IdP，而不是自己造。
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotas (
  token_name TEXT NOT NULL,
  day        TEXT NOT NULL,
  matches    INTEGER NOT NULL DEFAULT 0,
  renders    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (token_name, day)
);
"""


@dataclass
class Principal:
    name: str
    authenticated: bool

    @property
    def is_anonymous(self) -> bool:
        return not self.authenticated


@dataclass
class QuotaConfig:
    mode: str = "off"
    tokens: dict[str, str] | None = None
    matches_per_day: int = 20
    renders_per_day: int = 10

    @classmethod
    def from_env(cls) -> "QuotaConfig":
        mode = os.environ.get("MELEE_AUTH", "off").strip().lower()
        raw = os.environ.get("MELEE_API_TOKENS", "").strip()
        tokens: dict[str, str] = {}
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, token = item.partition(":")
            if name and token:
                tokens[name.strip()] = token.strip()
        return cls(
            mode=mode if mode in {"off", "token"} else "off",
            tokens=tokens,
            matches_per_day=int(os.environ.get("MELEE_QUOTA_MATCHES", "20")),
            renders_per_day=int(os.environ.get("MELEE_QUOTA_RENDERS", "10")),
        )


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def authenticate(config: QuotaConfig, authorization: str | None) -> Principal:
    if config.mode == "off":
        return Principal(name="local", authenticated=True)

    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("缺少 Bearer token")

    token = authorization.split(" ", 1)[1].strip()
    for name, expected in (config.tokens or {}).items():
        # 定长比较，避免通过响应时间猜 token
        if _constant_time_equal(token, expected):
            return Principal(name=name, authenticated=True)
    raise AuthError("token 无效")


def _constant_time_equal(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y
    return result == 0


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def check_and_consume(db_path: str | Path, principal: Principal, kind: str, config: QuotaConfig) -> None:
    """先检查再记一次用量。kind 是 matches / renders。"""
    limit = config.matches_per_day if kind == "matches" else config.renders_per_day
    if limit <= 0:
        raise AuthError("该操作已被禁用", status=403)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        day = _today()
        row = conn.execute(
            "SELECT matches, renders FROM quotas WHERE token_name = ? AND day = ?",
            (principal.name, day),
        ).fetchone()
        used = 0 if row is None else (row[0] if kind == "matches" else row[1])
        if used >= limit:
            raise AuthError(f"今日 {kind} 配额已用尽（{used}/{limit}）", status=429)

        # 列名只可能来自这两个常量；不用 f-string 拼 SQL，避免扫描器与
        # 后续改动引入注入面。
        if kind == "matches":
            conn.execute(
                "INSERT INTO quotas (token_name, day, matches) VALUES (?,?,1) "
                "ON CONFLICT(token_name, day) DO UPDATE SET matches = matches + 1",
                (principal.name, day),
            )
        else:
            conn.execute(
                "INSERT INTO quotas (token_name, day, renders) VALUES (?,?,1) "
                "ON CONFLICT(token_name, day) DO UPDATE SET renders = renders + 1",
                (principal.name, day),
            )
        conn.commit()
    finally:
        conn.close()


# ---------- SSE 用的短时票据 ----------
# EventSource 不能带自定义请求头，把 token 拼进 URL 会进访问日志。
# 改用"换票"：先用 Bearer 换一张 60 秒的一次性票据，再用票据连流。

_tickets: dict[str, tuple[str, float]] = {}
_ticket_lock = __import__("threading").Lock()


def issue_ticket(principal: Principal, *, ttl_s: float = 60.0) -> str:
    import secrets as _secrets
    import time as _time

    ticket = _secrets.token_urlsafe(24)
    with _ticket_lock:
        _tickets[ticket] = (principal.name, _time.monotonic() + ttl_s)
    return ticket


def redeem_ticket(ticket: str) -> Principal | None:
    import time as _time

    with _ticket_lock:
        entry = _tickets.pop(ticket, None)
    if entry is None:
        return None
    name, expires = entry
    if expires < _time.monotonic():
        return None
    return Principal(name=name, authenticated=True)


# ---------- 媒体票据 ----------
# <video> 标签不能带 Authorization，而 Range 请求会分多次打同一个 URL，
# 所以媒体票据是"可复用 + 绑定 matchId + 短时"，与 SSE 的一次性票据分开。

_media_tickets: dict[str, tuple[str, str, float]] = {}


def issue_media_ticket(principal: Principal, match_id: str, *, ttl_s: float = 600.0) -> str:
    import secrets as _secrets
    import time as _time

    ticket = _secrets.token_urlsafe(24)
    with _ticket_lock:
        now = _time.monotonic()
        for key, (_name, _match, expires) in list(_media_tickets.items()):
            if expires < now:
                del _media_tickets[key]
        _media_tickets[ticket] = (principal.name, match_id, now + ttl_s)
    return ticket


def redeem_media_ticket(ticket: str, match_id: str) -> Principal | None:
    """媒体票据可重复使用，但只能读签发时绑定的那一场。"""
    import time as _time

    with _ticket_lock:
        entry = _media_tickets.get(ticket)
    if entry is None:
        return None
    name, bound_match, expires = entry
    if expires < _time.monotonic() or bound_match != match_id:
        return None
    return Principal(name=name, authenticated=True)


def usage(db_path: str | Path, principal: Principal) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        row = conn.execute(
            "SELECT matches, renders FROM quotas WHERE token_name = ? AND day = ?",
            (principal.name, _today()),
        ).fetchone()
        return {
            "principal": principal.name,
            "day": _today(),
            "matches": 0 if row is None else row[0],
            "renders": 0 if row is None else row[1],
        }
    finally:
        conn.close()
