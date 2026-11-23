"""选手请求的公共重试骨架：预算检查、可重试判定、线性退避。

HTTPAgent 与 LLM 的三条请求路径此前各写一遍循环，连“最后一次失败直接
返回”的边界都各自维护。这里只统一控制流，业务差异（URL、记账、解析）
仍留在各自的 send() 里。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from .errors import http_failure, transport_failure

T = TypeVar("T")


def with_retry(
    *,
    attempts: int,
    backoff_s: float,
    send: Callable[[], tuple[T | None, str | None]],
    api_key: str,
    ensure_budget: Callable[[], None] | None = None,
    error_chars: int = 500,
) -> tuple[T | None, str | None]:
    """执行 send()，失败按 429/5xx 重试；send() 也可直接返回终态失败。"""
    last = "[transport-error] 未知错误"
    for attempt in range(max(1, attempts)):
        # 预算检查放在每次尝试之前：重试也要计入。
        if ensure_budget is not None:
            ensure_budget()
        try:
            result, failure = send()
        except httpx.HTTPStatusError as exc:
            last, retryable = http_failure(exc, api_key, error_chars)
            if not retryable or attempt == attempts - 1:
                return None, last
        except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一次可记录的犯规
            last = transport_failure(exc, api_key)
            if attempt == attempts - 1:
                return None, last
        else:
            if failure is not None:
                return None, failure
            return result, None
        time.sleep(backoff_s * (attempt + 1))
    return None, last


def retry_for(
    config: object,
    send: Callable[[], tuple[T | None, str | None]],
    *,
    ensure_budget: Callable[[], None] | None = None,
    error_chars: int = 500,
) -> tuple[T | None, str | None]:
    """按选手配置里的 max_retries / retry_backoff_s 执行 with_retry。"""
    return with_retry(
        attempts=max(0, config.max_retries) + 1,  # type: ignore[attr-defined]
        backoff_s=config.retry_backoff_s,  # type: ignore[attr-defined]
        send=send,
        api_key=config.api_key,  # type: ignore[attr-defined]
        ensure_budget=ensure_budget,
        error_chars=error_chars,
    )
