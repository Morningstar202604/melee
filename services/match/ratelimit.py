"""限流：交给 `limits` 库，不再自己写滑动窗口。

之前这里有两套手写实现（内存桶 + Redis ZSET），各自维护窗口清理、
键过期和 Redis 故障回退，约 150 行；而 `limits` 已经把这些做完了，
并且同时支持内存、Redis、Memcached 等后端。

现在只保留：
- 分档表（每个接口的额度与窗口）
- 后端选择（内存 / Redis，Redis 不可用自动退回内存）
- 来源 IP 提取（`X-Forwarded-For` 默认不信任）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from limits import RateLimitItem, parse
from limits.storage import MemoryStorage, RedisStorage
from limits.strategies import MovingWindowRateLimiter

logger = logging.getLogger("melee.ratelimit")


# 分档：写操作更严，读操作更宽。金额与窗口用 limits 的表达式，一眼能读懂。
PROFILES: dict[str, str] = {
    "write_match": "10/minute",
    "write_season": "5/minute",
    "render": "5/minute",
    "read": "120/minute",
    "stream": "30/minute",
}

_DEFAULT = "60/minute"


@dataclass
class Decision:
    allowed: bool
    remaining: int
    reset_in: float


class Limiter:
    """`limits` 的薄封装：把"命中一次"翻成 (allowed, remaining)。"""

    def __init__(self, storage: Any | None = None) -> None:
        self._storage = storage or MemoryStorage()
        self._strategy = MovingWindowRateLimiter(self._storage)
        self._items: dict[str, RateLimitItem] = {}

    def _item(self, profile: str) -> RateLimitItem:
        expression = PROFILES.get(profile, _DEFAULT)
        item = self._items.get(expression)
        if item is None:
            item = parse(expression)
            self._items[expression] = item
        return item

    def hit(self, key: str, profile: str) -> Decision:
        item = self._item(profile)
        allowed = bool(self._strategy.hit(item, key))
        stats = self._strategy.get_window_stats(item, key)
        return Decision(allowed=allowed, remaining=int(stats.remaining), reset_in=float(stats.reset_time))

    def reset(self) -> None:
        self._storage.reset()
        self._items.clear()


_LIMITER: Limiter | None = None


def _build_storage() -> Any:
    from .settings import settings

    cfg = settings()
    if cfg.ratelimit != "redis":
        return MemoryStorage()
    url = cfg.redis_url.strip()
    if not url:
        logger.warning("MELEE_RATELIMIT=redis 但未配置 MELEE_REDIS_URL，退回内存限流")
        return MemoryStorage()
    try:
        return RedisStorage(url)
    except Exception as exc:  # noqa: BLE001 - 限流后端不能阻塞启动
        logger.warning("Redis 限流初始化失败，退回内存：%s", exc)
        return MemoryStorage()


def limiter() -> Limiter:
    global _LIMITER
    if _LIMITER is None:
        _LIMITER = Limiter(_build_storage())
    return _LIMITER


def reset_limiter() -> None:
    """测试与配置切换用：丢掉当前实例，下次重新按环境变量构建。"""
    global _LIMITER
    if _LIMITER is not None:
        try:
            _LIMITER.reset()
        except Exception:  # noqa: BLE001
            pass
    _LIMITER = None


def client_ip(request: Any) -> str:
    """从请求里取可信来源 IP。

    `MELEE_TRUSTED_PROXY_HOPS` 表示"可信反代追加了几段"，**默认 0**：
    直连部署不信任任何 `X-Forwarded-For`。默认信任 1 段时，任何客户端
    只要每次换一个 XFF 就能绕过每 IP 限流（实测 12 次建场全部 201）。
    反向代理后面部署必须显式设成 1（或实际层数），并确保代理会覆写该头。
    """
    from .settings import settings

    hops = settings().trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
    if forwarded and hops > 0:
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def check(request: Any, profile: str) -> tuple[bool, int]:
    decision = limiter().hit(f"{profile}:{client_ip(request)}", profile)
    return decision.allowed, decision.remaining
