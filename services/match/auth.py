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

import hmac
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

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
        from .settings import settings

        cfg = settings()
        return cls(
            mode=cfg.auth,
            tokens=cfg.tokens,
            matches_per_day=cfg.quota_matches,
            renders_per_day=cfg.quota_renders,
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
    """标准库的定长比较，不再手写 XOR 循环。"""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def check_and_consume(db_path: str | Path, principal: Principal, kind: str, config: QuotaConfig) -> None:
    """原子地"检查 + 记一次用量"。kind 是 matches / renders。

    为什么不用"先 SELECT 再 UPDATE"：两个副本同时读到 used=limit-1 时会各放行一次，
    实际超发。这里改成单条 UPSERT + 条件 WHERE：数据库替我们做比较与自增，
    只有 rowcount=1 才算真正占到额度。
    """
    limit = config.matches_per_day if kind == "matches" else config.renders_per_day
    if limit <= 0:
        raise AuthError("该操作已被禁用", status=403)

    # 列名只可能来自这两个常量，不用 f-string 拼 SQL。
    if kind == "matches":
        statement = (
            "INSERT INTO quotas (token_name, day, matches) VALUES (?,?,1) "
            "ON CONFLICT(token_name, day) DO UPDATE SET matches = matches + 1 "
            "WHERE quotas.matches < ?"
        )
    else:
        statement = (
            "INSERT INTO quotas (token_name, day, renders) VALUES (?,?,1) "
            "ON CONFLICT(token_name, day) DO UPDATE SET renders = renders + 1 "
            "WHERE quotas.renders < ?"
        )

    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.executescript(SCHEMA)
        cursor = conn.execute(statement, (principal.name, _today(), limit))
        conn.commit()
        if cursor.rowcount == 0:
            used = _used(conn, principal, kind)
            raise AuthError(f"今日 {kind} 配额已用尽（{used}/{limit}）", status=429)
    finally:
        conn.close()


def _used(conn: sqlite3.Connection, principal: Principal, kind: str) -> int:
    row = conn.execute(
        "SELECT matches, renders FROM quotas WHERE token_name = ? AND day = ?",
        (principal.name, _today()),
    ).fetchone()
    if row is None:
        return 0
    return int(row[0] if kind == "matches" else row[1])


# ---------- 票据（SSE / 媒体） ----------
# EventSource 与 <video> 都不能带自定义请求头；把长期 token 拼进 URL 会进访问日志。
# 用 itsdangerous 做**签名票据**，不再维护进程内字典：
# - 无状态：多副本部署时任何实例都能验签，不需要共享内存或 Redis；
# - 过期由签名本身携带，不依赖本机时钟之外的存储；
# - 篡改即失效，不泄漏 token 本身。
#
# 密钥优先级：MELEE_TICKET_SECRET → MELEE_SECRET_KEY → 进程随机（仅限单进程）。


def _ticket_serializer() -> URLSafeTimedSerializer:
    from .settings import settings

    cfg = settings()
    secret = cfg.ticket_secret.strip() or cfg.secret_key.strip()
    if not secret:
        # 没有配置时退化成进程内随机密钥：单进程可用，重启即失效。
        global _EPHEMERAL_SECRET
        if _EPHEMERAL_SECRET is None:
            _EPHEMERAL_SECRET = __import__("secrets").token_urlsafe(32)
        secret = _EPHEMERAL_SECRET
    return URLSafeTimedSerializer(secret, salt="melee-ticket")


_EPHEMERAL_SECRET: str | None = None


def issue_ticket(principal: Principal, *, ttl_s: float = 60.0) -> str:
    """SSE 票据：签名 + 内嵌过期时间（不同实例用同一密钥即可验证）。"""
    return _ticket_serializer().dumps(
        {"n": principal.name, "k": "sse", "exp": time.time() + ttl_s}
    )


def redeem_ticket(ticket: str, *, max_age: float = 60.0) -> Principal | None:
    try:
        data = _ticket_serializer().loads(ticket, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("k") != "sse":
        return None
    # 到期时刻本身即视为过期（< 会让 exp==now 仍然有效）
    if float(data.get("exp", 0)) <= time.time():
        return None
    return Principal(name=str(data.get("n", "")), authenticated=True)


def issue_media_ticket(principal: Principal, match_id: str, *, ttl_s: float = 600.0) -> str:
    """媒体票据：额外绑定 matchId，可重复使用（<video> 的 Range 请求会多次打同一 URL）。"""
    return _ticket_serializer().dumps(
        {"n": principal.name, "k": "media", "m": match_id, "exp": time.time() + ttl_s}
    )


def redeem_media_ticket(ticket: str, match_id: str, *, max_age: float = 600.0) -> Principal | None:
    """媒体票据可重复使用，但只能读签发时绑定的那一场。"""
    try:
        data = _ticket_serializer().loads(ticket, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict) or data.get("k") != "media" or data.get("m") != match_id:
        return None
    # 到期时刻本身即视为过期（< 会让 exp==now 仍然有效）
    if float(data.get("exp", 0)) <= time.time():
        return None
    return Principal(name=str(data.get("n", "")), authenticated=True)


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
