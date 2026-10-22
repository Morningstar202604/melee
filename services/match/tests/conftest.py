from __future__ import annotations

import pytest

from services.match import api as api_module
from services.match.ratelimit import reset_limiter


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


@pytest.fixture(autouse=True)
def _fast_schedule():
    """把赛季调度的间隔压到极短，避免测试里空等。

    SeasonScheduler 的 tick 是同步的，这里只是防止别的测试遗留线程。
    """
    yield
