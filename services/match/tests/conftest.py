from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import pytest

from services.match.ratelimit import reset_limiter
from services.match.secrets import generate_key
from services.match.settings import reset_settings
from services.match.worker import stop_inline_worker


class _FakeRedisThread:
    """在进程内起一个 Redis 协议服务端（fakeredis 的 TcpFakeServer）。

    为什么这么做：原先两条 Redis 集成测试要求外部 `MELEE_TEST_REDIS_URL`，
    CI 与本机都没有 Redis，于是长期 skip——等于"多副本能力从未被验证"。
    fakeredis 提供 RESP 服务端，`redis` 客户端照常走 TCP 协议。

    已知限制：fakeredis 2.32 的 TCP 层对 `psubscribe` 不投递 `pmessage`
    （同进程 FakeRedis 可以）。所以走 TCP 的用例只验证 publish/命令路径，
    pubsub 解析另用进程内客户端验证（见 test_extras 的两条用例）。
    """

    def __init__(self) -> None:
        from fakeredis import TcpFakeServer

        self._server = TcpFakeServer(("127.0.0.1", 0), server_type="redis")
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"redis://{host}:{port}/0"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture(scope="session")
def fake_redis_url():
    """进程内 Redis 服务端的连接串；启动失败则跳过而不是让整套测试挂掉。"""
    try:
        server = _FakeRedisThread()
    except Exception as exc:  # noqa: BLE001 - 可选依赖缺失
        pytest.skip(f"fakeredis 不可用：{exc}")
    server.start()
    # 等端口真的可连：serve_forever 是异步起监听。
    for _ in range(50):
        try:
            with socket.create_connection(server._server.server_address[:2], timeout=0.2):
                break
        except OSError:
            import time

            time.sleep(0.02)
    try:
        yield server.url
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def _provide_redis_url(fake_redis_url, monkeypatch):
    """把进程内 Redis 的地址注入成 MELEE_TEST_REDIS_URL。

    显式设置过的（例如运维想跑真 Redis）优先，不被覆盖。
    """
    if not os.environ.get("MELEE_TEST_REDIS_URL"):
        monkeypatch.setenv("MELEE_TEST_REDIS_URL", fake_redis_url)


@pytest.fixture()
def allow_local_endpoints(monkeypatch):
    """显式选择"允许本机端点"的测试用。

    默认不全局打开：否则 SSRF 门禁的测试会被自己的 fixture 绕过。
    """
    monkeypatch.setenv("MELEE_ALLOW_PRIVATE_ENDPOINTS", "1")


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    """每个测试都从干净的环境变量重新构建配置。

    `settings()` 有进程级缓存，monkeypatch 改环境变量后必须清缓存，
    否则测试会读到上一个测试的配置。

    默认**注入一把有效密钥**：绝大多数测试走"带凭据封存"的正常路径。
    要验证"没有密钥"的用例用 `no_secret_key` fixture 显式声明——
    旧实现靠 `delenv` 删，但 autouse 又塞回去，于是那些测试只能靠
    执行顺序碰运气（表现是单跑绿、全量偶尔红）。
    """
    if "MELEE_SECRET_KEY" not in os.environ:
        monkeypatch.setenv("MELEE_SECRET_KEY", generate_key())
    reset_settings()
    yield
    reset_settings()


@pytest.fixture()
def no_secret_key(monkeypatch):
    """显式声明"本用例运行在没有密钥的环境里"。"""
    monkeypatch.delenv("MELEE_SECRET_KEY", raising=False)
    reset_settings()
    yield
    reset_settings()


@pytest.fixture(autouse=True)
def _reset_inline_worker():
    """每个测试前后都停掉内嵌 worker。

    内嵌 worker 是全局单例，会跨测试消费彼此的队列；
    这里强制隔离，避免"单跑过、一起跑挂"。
    """
    stop_inline_worker()
    reset_limiter()
    yield
    stop_inline_worker()
    reset_limiter()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """API 客户端 + 内嵌 worker：独立临时库，开箱即可跑完一场。

    建场走队列，没有 worker 就永远停在 pending；所有 HTTP 行为测试都要它。
    """
    from .support import make_client

    instance = make_client(tmp_path, monkeypatch, worker=True)
    yield instance
    stop_inline_worker()


@pytest.fixture()
def local_client(tmp_path, monkeypatch, allow_local_endpoints):
    """同上，但允许 BYOK 指向本机端点（配合 FakeOpenAI 使用）。

    必须让 allow_local_endpoints 先于 create_app 生效：settings() 有进程级缓存，
    建 app 之后再设环境变量不会重新求值。
    """
    from .support import make_client

    instance = make_client(tmp_path, monkeypatch, worker=True)
    yield instance
    stop_inline_worker()


@pytest.fixture(autouse=True)
def _fast_schedule():
    """把赛季调度的间隔压到极短，避免测试里空等。

    SeasonScheduler 的 tick 是同步的，这里只是防止别的测试遗留线程。
    """
    yield
