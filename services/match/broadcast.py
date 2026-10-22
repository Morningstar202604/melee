"""事件广播：单实例用内存，多实例用 Redis。

为什么需要这一层：MatchHandle 是进程内的，SSE 订阅者也在进程内。
一旦比赛服务多副本，连到 A 副本的观众收不到 B 副本的比赛事件。
把广播抽出来后，跨副本只共享"事件"，不共享状态——事件日志仍是唯一真相。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable, Protocol


class Broadcaster(Protocol):
    def publish(self, match_id: str, event: dict[str, Any]) -> None: ...

    def subscribe(self, match_id: str) -> "Subscription": ...


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
        self._prefix = channel_prefix
        self._pending: dict[str, dict[str, Any]] = {}
        self._watchers: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._stop = threading.Event()
        self._thread.start()

    def _channel(self, match_id: str) -> str:
        return f"{self._prefix}{match_id}"

    def publish(self, match_id: str, event: dict[str, Any]) -> None:
        self._client.publish(self._channel(match_id), json.dumps(event, ensure_ascii=False))

    def _listen(self) -> None:
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        pubsub.psubscribe(f"{self._prefix}*")
        while not self._stop.is_set():
            message = pubsub.get_message(timeout=1.0)
            if not message:
                continue
            match_id = message["channel"].split(self._prefix, 1)[-1]
            try:
                event = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            self._pending.setdefault(match_id, {})[event["seq"]] = event
            for callback in list(self._watchers.get(match_id, ())):
                callback(event)

    def subscribe(self, match_id: str) -> "RedisSubscription":
        return RedisSubscription(self, match_id)

    def on_event(self, match_id: str, callback: Callable[[dict[str, Any]], None]) -> None:
        self._watchers.setdefault(match_id, []).append(callback)

    def close(self) -> None:
        self._stop.set()


class RedisSubscription:
    def __init__(self, owner: RedisBroadcaster, match_id: str) -> None:
        self._owner = owner
        self._match_id = match_id
        self._buffer: list[dict[str, Any]] = []
        self._event = threading.Event()

        def _collect(event: dict[str, Any]) -> None:
            self._buffer.append(event)
            self._event.set()

        owner.on_event(match_id, _collect)
        self._collect = _collect

    async def get(self, timeout_s: float = 15.0) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            if self._buffer:
                return self._buffer.pop(0)
            await asyncio.sleep(0.05)
        return None

    def close(self) -> None:
        watchers = self._owner._watchers.get(self._match_id, [])
        if self._collect in watchers:
            watchers.remove(self._collect)


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
            except Exception:  # noqa: BLE001 - 广播是增强，不是启动前提
                _GLOBAL = MemoryBroadcaster()
    return _GLOBAL


def reset_global_broadcaster(backend: Broadcaster | None = None) -> None:
    """测试用：换掉进程级单例。"""
    global _GLOBAL
    with _GLOBAL_LOCK:
        _GLOBAL = backend


def publish_event(event: dict[str, Any]) -> None:
    """给 EventLog.on_event 用的回调。"""
    match_id = event.get("matchId")
    if match_id:
        global_broadcaster().publish(match_id, event)
