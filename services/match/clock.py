"""统一的时间戳格式：事件日志、便携导出、回放都只认这一种。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """带时区的 ISO-8601 当前时刻；排序与跨时区比较都依赖这个格式。"""
    return datetime.now(timezone.utc).isoformat()
