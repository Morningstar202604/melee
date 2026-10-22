"""限流：按来源 IP 与接口分档的滑动窗口。

与旧仓 melee 的教训一致：直接把 `x-forwarded-for` 第一段当 IP 会被伪造，
所以这里用"取链尾第 N 项"的方式（N 由 MELEE_TRUSTED_PROXY_HOPS 决定）。

单实例用内存计数器；多实例用 Redis（`MELEE_RATELIMIT=redis` + `MELEE_REDIS_URL`）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("melee.ratelimit")


@dataclass
class Bucket:
    hits: list[float] = field(default_factory=list)


class RateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, Bucket] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, *, limit: int, window_s: float) -> tuple[bool, int]:
        """记一次，返回（是否放行，剩余额度）。"""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(key, Bucket())
            bucket.hits = [t for t in bucket.hits if now - t < window_s]
            if len(bucket.hits) >= limit:
                return False, 0
            bucket.hits.append(now)
            return True, limit - len(bucket.hits)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def cleanup(self, older_than_s: float = 3600) -> int:
        """清掉长期不活跃的桶，避免键无限增长。"""
        now = time.monotonic()
        removed = 0
        with self._lock:
            for key in list(self._buckets):
                bucket = self._buckets[key]
                bucket.hits = [t for t in bucket.hits if now - t < older_than_s]
                if not bucket.hits:
                    del self._buckets[key]
                    removed += 1
        return removed


class RedisRateLimiter:
    """多副本限流：用 Redis 的有序集合做滑动窗口。

    Redis 不可用时放行（fail-open）：限流是保护，不是准入门槛；
    数据库与任务队列才是真相源，不能因为 Redis 抖动把整个服务打死。
    """

    def __init__(self, url: str, *, prefix: str = "melee:rl:", socket_timeout: float = 0.5) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=socket_timeout)
        self._prefix = prefix

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def hit(self, key: str, *, limit: int, window_s: float) -> tuple[bool, int]:
        import time

        now = time.time()
        redis_key = self._key(key)
        try:
            pipe = self._client.pipeline()
            pipe.zremrangebyscore(redis_key, 0, now - window_s)
            pipe.zcard(redis_key)
            pipe.zadd(redis_key, {f"{now}-{os.urandom(4).hex()}": now})
            pipe.expire(redis_key, int(window_s) + 1)
            _removed, used, _added, _ttl = pipe.execute()
        except Exception as exc:  # noqa: BLE001 - 限流后端故障不能变成全站故障
            logger.warning("限流 Redis 不可用，本次放行：%s", exc)
            return True, limit
        used = int(used)
        if used >= limit:
            try:
                self._client.zremrangebyscore(redis_key, now, now + 1)
            except Exception:  # noqa: BLE001
                pass
            return False, 0
        return True, limit - used - 1

    def reset(self) -> None:
        try:
            for key in self._client.scan_iter(f"{self._prefix}*"):
                self._client.delete(key)
        except Exception:  # noqa: BLE001
            pass

    def cleanup(self, older_than_s: float = 3600) -> int:
        return 0


# 分档：写操作更严，读操作更宽
PROFILES: dict[str, tuple[int, float]] = {
    "write_match": (10, 60.0),     # 建场
    "write_season": (5, 60.0),     # 建赛季 / 跑赛季
    "render": (5, 60.0),           # 渲染
    "read": (120, 60.0),           # 事件、状态、战报
    "stream": (30, 60.0),          # SSE 连接
}

LIMITER: Any = RateLimiter()
_LIMITER_LOCK = threading.Lock()


def limiter() -> Any:
    """按环境变量选后端：MELEE_RATELIMIT=redis 时用 Redis，否则内存。

    Redis 构造失败（没装包 / URL 错）退回内存，启动不受影响。
    """
    global LIMITER
    kind = os.environ.get("MELEE_RATELIMIT", "memory").strip().lower()
    if kind != "redis":
        return LIMITER
    if isinstance(LIMITER, RedisRateLimiter):
        return LIMITER
    with _LIMITER_LOCK:
        url = os.environ.get("MELEE_REDIS_URL", "").strip()
        if not url:
            logger.warning("MELEE_RATELIMIT=redis 但未配置 MELEE_REDIS_URL，退回内存限流")
            return LIMITER
        try:
            LIMITER = RedisRateLimiter(url)
        except Exception as exc:  # noqa: BLE001 - 装饰性能力不能阻塞启动
            logger.warning("Redis 限流初始化失败，退回内存：%s", exc)
        return LIMITER


def reset_limiter() -> None:
    """测试用：恢复默认内存实现。"""
    global LIMITER
    with _LIMITER_LOCK:
        LIMITER = RateLimiter()


def client_ip(request: Any) -> str:
    """从请求里取可信来源 IP。

    `MELEE_TRUSTED_PROXY_HOPS` 表示"可信反代追加了几段"，**默认 0**：
    直连部署不信任任何 `X-Forwarded-For`。默认信任 1 段时，任何客户端
    只要每次换一个 XFF 就能绕过每 IP 限流（实测 12 次建场全部 201）。
    反向代理后面部署必须显式设成 1（或实际层数），并确保代理会覆写该头。
    """
    hops = int(os.environ.get("MELEE_TRUSTED_PROXY_HOPS", "0"))
    forwarded = request.headers.get("x-forwarded-for") if hasattr(request, "headers") else None
    if forwarded and hops > 0:
        entries = [entry.strip() for entry in forwarded.split(",") if entry.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def check(request: Any, profile: str) -> tuple[bool, int]:
    limit, window = PROFILES.get(profile, (60, 60.0))
    key = f"{profile}:{client_ip(request)}"
    return limiter().hit(key, limit=limit, window_s=window)
