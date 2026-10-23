from __future__ import annotations

from pathlib import Path

import pytest

from services.match import api as api_module
from services.match.ratelimit import reset_limiter
from services.match.settings import reset_settings


@pytest.fixture()
def allow_local_endpoints(monkeypatch):
    """显式选择"允许本机端点"的测试用。

    默认不全局打开：否则 SSRF 门禁的测试会被自己的 fixture 绕过。
    """
    monkeypatch.setenv("MELEE_ALLOW_PRIVATE_ENDPOINTS", "1")


@pytest.fixture(autouse=True)
def _reset_settings():
    """每个测试都从干净的环境变量重新构建配置。

    `settings()` 有进程级缓存，monkeypatch 改环境变量后必须清缓存，
    否则测试会读到上一个测试的配置。
    """
    reset_settings()
    yield
    reset_settings()


@pytest.fixture(autouse=True)
def _reset_inline_worker():
    """每个测试前后都停掉内嵌 worker。

    内嵌 worker 是全局单例，会跨测试消费彼此的队列；
    这里强制隔离，避免"单跑过、一起跑挂"。
    """
    api_module._stop_inline_worker()
    reset_limiter()
    yield
    api_module._stop_inline_worker()
    reset_limiter()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """API 客户端：独立临时库，不跑 lifespan（所以不会起内嵌 worker）。"""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())
    yield client
    api_module._stop_inline_worker()


@pytest.fixture(autouse=True)
def _fast_schedule():
    """把赛季调度的间隔压到极短，避免测试里空等。

    SeasonScheduler 的 tick 是同步的，这里只是防止别的测试遗留线程。
    """
    yield
