"""Idempotency-Key：让"重试建场"不会建出两场比赛。

背景：客户端超时后重发是常态（弱网、代理 5xx、用户连点）。建场是有副作用
的 POST，没有幂等键时一次重试就多一场比赛、多烧一次额度，赛季里还会多出
一条赛程。

实现按 draft-ietf-httpapi-idempotency-key-header 的语义：
- 首次请求：正常处理，把 (key, 请求体指纹, 响应体) 落库。
- 同 key 同请求体重放：直接返回首次的状态码与响应体，不重复执行。
- 同 key 不同请求体：422 —— 这是客户端 bug，不能假装成功。
- 并发同 key：409 —— 首次请求还在跑。

作用域是 (key, 端点) 而不是全局：同一个 key 用在建场和建赛季上是两件事。
记录会过期（默认 24 小时），由 `purge_expired` 在写入时顺带清理。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .db import ensure_schema_for

SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
  scope      TEXT NOT NULL,
  key        TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  status     INTEGER NOT NULL,
  body       TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (scope, key)
);
"""

TTL_SECONDS = 24 * 3600
MAX_KEY_LENGTH = 200


class IdempotencyConflict(Exception):
    """同 key 不同请求体。"""


class IdempotencyInFlight(Exception):
    """同 key 的首次请求尚未完成。"""


def fingerprint(payload: Any) -> str:
    """请求体指纹：稳定序列化后取 sha256。

    用 sort_keys 保证字段顺序不影响判定；中文不转义，避免同一语义
    因为编码差异被当成不同请求。
    """
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    ensure_schema_for(conn, SCHEMA, "idempotency")
    return conn


def validate_key(key: str) -> str:
    """键必须是可见 ASCII 且长度受控：它要进 SQL 与日志。"""
    if not key or len(key) > MAX_KEY_LENGTH:
        raise ValueError("Idempotency-Key 长度必须在 1–200 之间")
    if not all(33 <= ord(ch) <= 126 for ch in key):
        raise ValueError("Idempotency-Key 只能包含可见 ASCII 字符")
    return key


def begin(db_path: str | Path, scope: str, key: str, payload: Any) -> tuple[int, str] | None:
    """登记一次请求。

    返回 None 表示"这是首次请求，继续执行"；
    返回 (status, body) 表示"已有结果，直接重放"。
    """
    stamp = fingerprint(payload)
    conn = _connect(db_path)
    try:
        now = time.time()
        conn.execute("DELETE FROM idempotency WHERE created_at < ?", (now - TTL_SECONDS,))
        row = conn.execute(
            "SELECT fingerprint, status, body FROM idempotency WHERE scope = ? AND key = ?",
            (scope, key),
        ).fetchone()
        if row is not None:
            stored_fingerprint, status, body = row
            if stored_fingerprint != stamp:
                raise IdempotencyConflict(key)
            if status == 0:
                raise IdempotencyInFlight(key)
            return int(status), str(body)
        # status=0 占位：并发请求看到它就知道首次还在跑。
        conn.execute(
            "INSERT INTO idempotency (scope, key, fingerprint, status, body, created_at) VALUES (?,?,?,?,?,?)",
            (scope, key, stamp, 0, "", now),
        )
        conn.commit()
        return None
    finally:
        conn.close()


def complete(db_path: str | Path, scope: str, key: str, status: int, body: Any) -> None:
    """把首次响应落库，供后续重放。"""
    text = json.dumps(body, ensure_ascii=False, default=str)
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE idempotency SET status = ?, body = ?, created_at = ? WHERE scope = ? AND key = ?",
            (int(status), text, time.time(), scope, key),
        )
        conn.commit()
    finally:
        conn.close()


def abort(db_path: str | Path, scope: str, key: str) -> None:
    """首次请求失败时释放占位，让客户端能用同一个 key 重试。"""
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM idempotency WHERE scope = ? AND key = ? AND status = 0", (scope, key))
        conn.commit()
    finally:
        conn.close()
