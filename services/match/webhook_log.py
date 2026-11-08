"""Webhook 投递记录：把"发过什么、成没成、为什么失败"落到库里。

问题：旧实现只把结果写进服务器日志。运维要看"昨天的 webhook 为什么失败了"
得去翻日志文件，而且进程重启就没了；管理后台也无法展示，更没法手动重发。

设计取舍：
- **不存请求体全文**：webhook 载荷含比赛结果，属于可重新生成的数据；
  存摘要（sha256 前 16 位）足够对照，又不会把内容复制一份到第二个地方。
- **每次尝试一条记录**：重试 3 次就有 3 条，能看出是"一直 500"还是"抖了一下"。
- **有上限**：只保留最近 N 条（默认 500），后台按时间倒序展示；
  删除在写入时顺带做，不需要定时任务。
- 状态只有四种：ok / http_error / network_error / rejected（目标被 SSRF 拦截）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import ensure_schema_for

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  match_id   TEXT NOT NULL,
  url        TEXT NOT NULL,
  status     TEXT NOT NULL,
  http_code  INTEGER,
  error      TEXT,
  attempts   INTEGER NOT NULL DEFAULT 1,
  payload_sha TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_created ON webhook_deliveries (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_match ON webhook_deliveries (match_id, created_at DESC);
"""

MAX_ROWS = 500
KEEP_DAYS = 30


@dataclass
class Delivery:
    url: str
    status: str
    http_code: int | None = None
    error: str = ""
    attempts: int = 1

    def as_record(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "status": self.status,
            "httpCode": self.http_code,
            "error": self.error,
            "attempts": self.attempts,
        }


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    ensure_schema_for(conn, SCHEMA, "webhook")
    return conn


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:16]


def record(db_path: str | Path, match_id: str, body: bytes, deliveries: list[Delivery]) -> None:
    """写入一批投递结果；顺带做保留期与条数清理。"""
    if not deliveries:
        return
    payload_sha = digest(body)
    now = time.time()
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO webhook_deliveries "
            "(match_id, url, status, http_code, error, attempts, payload_sha, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [
                (match_id, item.url, item.status, item.http_code, item.error[:400],
                 int(item.attempts), payload_sha, now)
                for item in deliveries
            ],
        )
        conn.execute("DELETE FROM webhook_deliveries WHERE created_at < ?", (now - KEEP_DAYS * 86400,))
        # 条数上限：只留最近 MAX_ROWS 条
        conn.execute(
            "DELETE FROM webhook_deliveries WHERE id NOT IN "
            "(SELECT id FROM webhook_deliveries ORDER BY id DESC LIMIT ?)",
            (MAX_ROWS,),
        )
        conn.commit()
    finally:
        conn.close()


def recent(db_path: str | Path, *, limit: int = 50, match_id: str | None = None) -> list[dict[str, Any]]:
    """最近投递记录（倒序）。后台与 CLI 共用。"""
    limit = max(1, min(int(limit), MAX_ROWS))
    conn = _connect(db_path)
    try:
        if match_id:
            rows = conn.execute(
                "SELECT * FROM webhook_deliveries WHERE match_id = ? ORDER BY id DESC LIMIT ?",
                (match_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhook_deliveries ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "matchId": row["match_id"],
            "url": row["url"],
            "status": row["status"],
            "httpCode": row["http_code"],
            "error": row["error"],
            "attempts": row["attempts"],
            "payloadSha": row["payload_sha"],
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["created_at"])),
        }
        for row in rows
    ]


def summary(db_path: str | Path) -> dict[str, Any]:
    """后台概览：总数、失败数、最近一次失败时间。"""
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM webhook_deliveries").fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM webhook_deliveries WHERE status != 'ok'"
        ).fetchone()[0]
        last_failure = conn.execute(
            "SELECT created_at FROM webhook_deliveries WHERE status != 'ok' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return {
        "total": total,
        "failed": failed,
        "lastFailureAt": (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last_failure[0])) if last_failure else None
        ),
    }


def as_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False)
