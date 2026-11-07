"""事件广播：单实例用内存，多实例用 Redis。

为什么需要这一层：SSE 订阅者都在各自进程内。
一旦比赛服务多副本，连到 A 副本的观众收不到 B 副本的比赛事件。
把广播抽出来后，跨副本只共享"事件"，不共享状态——事件日志仍是唯一真相。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Protocol


class Broadcaster(Protocol):
    def publish(self, match_id: str, event: dict[str, Any]) -> None: ...

    def subscribe(self, match_id: str) -> "Subscription": ...

    def viewer_count(self, match_id: str) -> int: ...

    def total_viewers(self) -> int: ...


class Subscription(Protocol):
    async def get(self, timeout_s: float = 15.0) -> dict[str, Any] | None: ...

    def close(self) -> None: ...


def _offer(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # 慢消费者不能拖垮比赛：丢掉它自己的队列，它靠重连补全量。
        pass


class MemoryBroadcaster:
    """进程内广播。单实例演示足够；也是测试默认实现。

    订阅可能创建于事件循环内，而发布可能来自 worker 线程，
    所以带 loop 的订阅一律走 call_soon_threadsafe，不能直接 put。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[tuple[asyncio.AbstractEventLoop | None, asyncio.Queue]]] = {}
        self._lock = threading.Lock()

    def publish(self, match_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscribers.get(match_id, ()))
        for loop, queue in targets:
            if loop is None:
                _offer(queue, event)
                continue
            try:
                loop.call_soon_threadsafe(_offer, queue, event)
            except RuntimeError:
                # 事件循环已关闭（客户端断开），忽略即可。
                pass

    def subscribe(self, match_id: str) -> "MemorySubscription":
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._lock:
            self._subscribers.setdefault(match_id, []).append((loop, queue))
        return MemorySubscription(self, match_id, queue)

    def _remove(self, match_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            entries = self._subscribers.get(match_id, [])
            self._subscribers[match_id] = [item for item in entries if item[1] is not queue]

    def viewer_count(self, match_id: str) -> int:
        """真实订阅数。观赛人数只能来自这里，不允许编造。"""
        with self._lock:
            return len(self._subscribers.get(match_id, ()))

    def total_viewers(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._subscribers.values())

    @property
    def cross_replica(self) -> bool:
        """在线人数是否跨副本。内存实现天然只能看到本进程。"""
        return False


class MemorySubscription:
    def __init__(self, owner: MemoryBroadcaster, match_id: str, queue: asyncio.Queue) -> None:
        self._owner = owner
        self._match_id = match_id
        self._queue = queue

    async def get(self, timeout_s: float = 15.0) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    def close(self) -> None:
        self._owner._remove(self._match_id, self._queue)


class RedisBroadcaster:
    """跨副本广播。

    Redis 只做"通知"，事件本体仍然从各自的事件日志读。
    这样即使 Redis 丢消息，客户端重连时也能靠 Last-Event-ID 补齐。
    需要 redis 包；未安装时不导入，保持零依赖启动。
    """

    def __init__(self, url: str, *, channel_prefix: str = "melee:events:") -> None:
        import redis  # 延迟导入：没装 redis 也不影响单实例模式

        self._client = redis.Redis.from_url(url, decode_responses=True)
        # 建连时探一次：配置了 Redis 但连不上，必须在这里失败，
        # 让 global_broadcaster 退回内存广播。旧实现只判断"包是否导入成功"，
        # Redis 挂掉时监听线程会静默死掉，跨副本事件永久不再到达。
        self._client.ping()
        self._prefix = channel_prefix
        self._watchers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        # presence 用 Redis 计数器：每个订阅 +1，关闭 -1。
        # 键带 TTL 兜底：进程被 kill 时不会永久留下幽灵观众。
        self._presence_prefix = f"{channel_prefix}presence:"
        self._presence_ttl = 3600
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._stop = threading.Event()
        self._thread.start()

    def _channel(self, match_id: str) -> str:
        return f"{self._prefix}{match_id}"

    def publish(self, match_id: str, event: dict[str, Any]) -> None:
        try:
            self._client.publish(self._channel(match_id), json.dumps(event, ensure_ascii=False))
        # aqg: top-level boundary
        except Exception as exc:  # noqa: BLE001 - Redis 抖动不能让事件写入失败  # aqg: top-level boundary
            logging.getLogger("melee.broadcast").warning("Redis 发布失败（已忽略）：%s", exc)

    def _listen(self) -> None:
        """订阅循环：断连后指数退避重连，线程本身不退出。

        旧实现一旦 pubsub 抛错线程就死了，进程还在、/health 还 200，
        但跨副本事件再也不会到达——这种"假活"比启动失败更难发现。
        """
        log = logging.getLogger("melee.broadcast")
        backoff = 1.0
        while not self._stop.is_set():
            pubsub = None
            try:
                pubsub = self._client.pubsub(ignore_subscribe_messages=True)
                pubsub.psubscribe(f"{self._prefix}*")
                backoff = 1.0
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=1.0)
                    if not message:
                        continue
                    match_id = message["channel"].split(self._prefix, 1)[-1]
                    try:
                        event = json.loads(message["data"])
                    except (TypeError, ValueError):
                        continue
                    for callback in list(self._watchers.get(match_id, ())):
                        callback(event)
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 断连后重连，不能死线程
                log.warning("Redis 订阅中断，%.1fs 后重连：%s", backoff, exc)
                self._stop.wait(backoff)
                backoff = min(30.0, backoff * 2)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.close()
                    # aqg: top-level boundary
                    except Exception:  # noqa: BLE001 - 关闭失败无意义
                        pass

    def subscribe(self, match_id: str) -> "RedisSubscription":
        return RedisSubscription(self, match_id)

    def on_event(self, match_id: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self._watchers.setdefault(match_id, []).append(callback)

    def viewer_count(self, match_id: str) -> int:
        """全局在线人数：读 Redis 里所有副本累加的计数器。

        读失败时退回本进程订阅数，并保证不小于它——宁少报真实值，
        也不能因为一次 Redis 抖动把人数显示成 0。
        """
        local = len(self._watchers.get(match_id, ()))
        try:
            value = int(self._client.get(f"{self._presence_prefix}{match_id}") or 0)
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - presence 是展示项
            return local
        return max(local, value)

    def total_viewers(self) -> int:
        try:
            keys = list(self._client.scan_iter(f"{self._presence_prefix}*", count=100))
            if not keys:
                return sum(len(items) for items in self._watchers.values())
            values = self._client.mget(keys)
            return sum(int(v or 0) for v in values)
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - 同上
            return sum(len(items) for items in self._watchers.values())

    def _presence(self, match_id: str, delta: int) -> None:
        key = f"{self._presence_prefix}{match_id}"
        try:
            value = int(self._client.incrby(key, delta))
            if value < 0:
                # 计数器被别人清过或重复 close：归零，避免负人数。
                self._client.set(key, 0)
            self._client.expire(key, self._presence_ttl)
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - presence 是展示项
            pass

    @property
    def cross_replica(self) -> bool:
        return True

    def close(self) -> None:
        self._stop.set()


class RedisSubscription:
    """Redis 广播的订阅者。

    订阅者缓冲由锁保护：发布来自 Redis 监听线程，读取来自事件循环。
    唤醒用 asyncio.Event（由 call_soon_threadsafe 触发），不再 50ms 空转。
    """

    def __init__(self, owner: RedisBroadcaster, match_id: str) -> None:
        self._owner = owner
        self._match_id = match_id
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        def _collect(event: dict[str, Any]) -> None:
            with self._lock:
                self._buffer.append(event)
            loop = self._loop
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._notify)
                except RuntimeError:
                    pass

        owner.on_event(match_id, _collect)
        self._collect = _collect
        owner._presence(match_id, +1)

    def _notify(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def get(self, timeout_s: float = 15.0) -> dict[str, Any] | None:
        if self._wake is None:
            self._loop = asyncio.get_running_loop()
            self._wake = asyncio.Event()
        with self._lock:
            if self._buffer:
                return self._buffer.pop(0)
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
        with self._lock:
            return self._buffer.pop(0) if self._buffer else None

    def close(self) -> None:
        watchers = self._owner._watchers.get(self._match_id, [])
        if self._collect in watchers:
            watchers.remove(self._collect)
            self._owner._presence(self._match_id, -1)



def make_broadcaster(kind: str = "memory", *, redis_url: str | None = None) -> Broadcaster:
    if kind == "redis":
        if not redis_url:
            raise ValueError("redis 模式需要提供 redis_url")
        return RedisBroadcaster(redis_url)
    return MemoryBroadcaster()


_GLOBAL: Broadcaster | None = None
_GLOBAL_LOCK = threading.Lock()


def global_broadcaster() -> Broadcaster:
    """进程级单例。API 的订阅与 worker 的发布必须落在同一个实例上。

    配置了 Redis 但连不上/没装包时退回内存：观战仍可用（SSE 每秒回查日志），
    只是跨副本实时性降级，服务不能因此起不来。
    """
    global _GLOBAL
    if _GLOBAL is not None:
        return _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            from .settings import settings

            cfg = settings()
            kind = cfg.broadcast or "memory"
            url = cfg.redis_url or None
            try:
                _GLOBAL = make_broadcaster(kind, redis_url=url)
            # aqg: top-level boundary
            except Exception:  # noqa: BLE001 - 广播是增强，不是启动前提
                _GLOBAL = MemoryBroadcaster()
    return _GLOBAL


def reset_global_broadcaster(backend: Broadcaster | None = None) -> None:
    """测试用：换掉进程级单例。"""
    global _GLOBAL
    with _GLOBAL_LOCK:
        _GLOBAL = backend


def presence_scope() -> str:
    """在线人数的统计口径：global（Redis 聚合）或 process（单进程）。"""
    return "global" if getattr(global_broadcaster(), "cross_replica", False) else "process"


def publish_event(event: dict[str, Any]) -> None:
    """给 EventLog.on_event 用的回调。"""
    match_id = event.get("matchId")
    if match_id:
        global_broadcaster().publish(match_id, event)
