"""持久化任务队列：让"跑一场比赛"和"渲染一段高光"可以在任意副本上执行。

为什么需要它：
- 比赛的写路径原本绑在接收请求的那个进程里（`threading.Thread`），副本一挂就没了。
- 渲染是同步的，一段视频几秒，批量赛事会把请求拖死。

做法是最小可用的租约队列：
- 落表 `jobs`，状态 pending / running / done / failed。
- 领取用一条带条件的 UPDATE，靠数据库的行锁保证同一任务只被一个副本拿到。
- 租约到期后自动可被重新领取，所以副本崩溃不会让任务永久卡住。
- 重试用 attempts 计数，超过上限标记 failed，不再无限重试。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id      TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  lease_owner TEXT,
  lease_until REAL,
  last_error  TEXT,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs (kind, status, lease_until);
"""

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    job_id: str
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "attempts": self.attempts,
            "maxAttempts": self.max_attempts,
            "lastError": self.last_error,
            "payload": _redact(self.payload),
        }


def _redact(value: Any, key: str = "") -> Any:
    """HTTP 视角的载荷脱敏：Key 只对执行体可见，不对读者回显。"""
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if key in {"apiKey", "api_key", "authorization"} and value:
        return "***"
    return value


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def enqueue(
    db_path: str | Path,
    kind: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    job_id: str | None = None,
) -> str:
    job_id = job_id or f"job_{uuid.uuid4().hex[:12]}"
    now = time.time()
    conn = connect(db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO jobs (job_id, kind, payload, status, attempts, max_attempts, created_at, updated_at) "
            "VALUES (?,?,?,?,0,?,?,?)",
            (job_id, kind, json.dumps(payload, ensure_ascii=False), PENDING, max_attempts, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def claim(db_path: str | Path, owner: str, kinds: Iterable[str], *, lease_s: float = 120.0) -> Job | None:
    """原子领取一个可执行任务。

    条件里同时包含"从未执行"和"租约已过期"，这样副本崩溃后任务会被别人接手。
    """
    kinds = list(kinds)
    if not kinds:
        return None
    placeholders = ",".join("?" for _ in kinds)
    now = time.time()
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            # 只有占位符数量是拼出来的，机队名本身全部走参数绑定（nosec: B608）。
            f"""
            SELECT job_id FROM jobs
            WHERE kind IN ({placeholders})
              AND attempts < max_attempts
              AND (status = ? OR (status = ? AND lease_until IS NOT NULL AND lease_until < ?))
            ORDER BY created_at ASC
            LIMIT 1
            """,  # nosec B608
            (*kinds, PENDING, RUNNING, now),
        ).fetchone()
        if row is None:
            conn.commit()
            return None

        job_id = row["job_id"]
        conn.execute(
            "UPDATE jobs SET status = ?, lease_owner = ?, lease_until = ?, attempts = attempts + 1, updated_at = ? "
            "WHERE job_id = ?",
            (RUNNING, owner, now + lease_s, now, job_id),
        )
        conn.commit()
        record = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_job(record)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete(db_path: str | Path, job_id: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET status = ?, lease_owner = NULL, lease_until = NULL, updated_at = ? WHERE job_id = ?",
            (DONE, time.time(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def fail(db_path: str | Path, job_id: str, error: str) -> str:
    """失败后要么回到 pending 等重试，要么彻底 failed。返回新状态。"""
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT attempts, max_attempts FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return FAILED
        status = FAILED if row["attempts"] >= row["max_attempts"] else PENDING
        conn.execute(
            "UPDATE jobs SET status = ?, last_error = ?, lease_owner = NULL, lease_until = NULL, updated_at = ? "
            "WHERE job_id = ?",
            (status, error[:500], time.time(), job_id),
        )
        conn.commit()
        return status
    finally:
        conn.close()


def get(db_path: str | Path, job_id: str) -> Job | None:
    conn = connect(db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None
    finally:
        conn.close()


def list_jobs(db_path: str | Path, *, status: str | None = None, limit: int = 50) -> list[Job]:
    conn = connect(db_path)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_job(row) for row in rows]
    finally:
        conn.close()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        job_id=row["job_id"],
        kind=row["kind"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        last_error=row["last_error"],
    )


def renew(db_path: str | Path, job_id: str, owner: str, lease_s: float) -> bool:
    """续租：长任务（一局比赛可能跑好几分钟）必须定期延长租约。

    不续租的话，租约到期后别的副本会把仍在运行的任务再领一次，
    两个 worker 同时往同一场比赛写事件——实测会出现两条 match.ended。
    返回 False 表示租约已经不属于自己（被别的副本接手）或任务已结束。
    """
    conn = connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE jobs SET lease_until = ?, updated_at = ? "
            "WHERE job_id = ? AND lease_owner = ? AND status = ?",
            (time.time() + lease_s, time.time(), job_id, owner, RUNNING),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
