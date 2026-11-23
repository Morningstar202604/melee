"""选手 HTTP 失败的统一文案：状态码、脱敏后的响应片段、可重试判定。

HTTPAgent 与 LLM 的三条重试路径（chat / 原生协议 / 流式）此前各自
切响应体、各自拼 `[http-...]` 与 `[transport-error]`，长度上限还不同。
"""

from __future__ import annotations

import httpx

from .base import redact_sensitive_text


def http_failure(exc: httpx.HTTPStatusError, api_key: str, max_chars: int = 300) -> tuple[str, bool]:
    """返回（可落事件的错误文案, 是否值得重试）。"""
    status = exc.response.status_code
    detail = redact_sensitive_text(exc.response.text[:max_chars], [api_key])
    retryable = status == 429 or status >= 500
    return f"[http-{status}] {detail}", retryable


def transport_failure(exc: Exception, api_key: str) -> str:
    """连接失败/超时/解码错误：同样先脱敏再落事件。"""
    return f"[transport-error] {redact_sensitive_text(str(exc), [api_key])}"


def stream_failure(exc: Exception, api_key: str) -> str:
    """流式读取中途断开：保留 stream 前缀，便于和普通请求失败区分。"""
    return f"[stream-error] {redact_sensitive_text(str(exc), [api_key])}"
