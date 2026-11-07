"""运维健康检查：把"能跑"与"跑得好"分开报。

`/health` 只回答"依赖是否可用"；这里回答"有没有开始劣化"：
任务积压、失败率、最老 pending 任务年龄、比赛卡在直播的时长。

为什么不复用 /health：探针被容器与负载均衡每秒打，它必须便宜且只看连通性。
这里的查询要扫 jobs 表，放在独立端点、由 Prometheus 或人按需拉。
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 阈值不是拍脑袋：默认 worker 4 线程、单场几十秒，积压 20 条意味着
# 至少 2-3 分钟没人消费；失败率 10% 说明上游模型或渲染器有问题。
BACKLOG_WARN = 20
FAILURE_RATE_WARN = 0.10
STUCK_MATCH_SECONDS = 3600
OLDEST_PENDING_WARN = 120


@dataclass
class Checks:
    problems: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, ok: bool, detail: Any, problem: str = "") -> None:
        self.details[name] = detail
        if not ok:
            self.problems.append(problem or name)


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def snapshot(db_path: str | Path, *, now: float | None = None) -> dict[str, Any]:
    """一次读完所有运维指标。任何一张表不存在都按 0 处理。"""
    moment = now if now is not None else time.time()
    checks = Checks()
    try:
        conn = _connect(db_path)
    except sqlite3.Error as exc:
        return {"status": "degraded", "problems": [f"数据库不可读：{exc}"], "details": {}}
    try:
        _queue_checks(conn, checks, moment)
        _match_checks(conn, checks, moment)
    finally:
        conn.close()
    return {
        "status": "ok" if not checks.problems else "degraded",
        "problems": checks.problems,
        "details": checks.details,
    }


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.OperationalError:
        return 0
    return row[0] if row is not None else 0


def _queue_checks(conn: sqlite3.Connection, checks: Checks, now: float) -> None:
    pending = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'pending'")
    running = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'running'")
    failed = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'failed'")
    done = _scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'done'")
    oldest = _scalar(conn, "SELECT MIN(created_at) FROM jobs WHERE status = 'pending'")
    oldest_age = max(0.0, now - float(oldest)) if oldest else 0.0

    checks.details["jobs"] = {
        "pending": pending,
        "running": running,
        "failed": failed,
        "done": done,
        "oldestPendingSeconds": round(oldest_age, 1),
    }
    checks.add(
        "backlog",
        pending <= BACKLOG_WARN,
        {"pending": pending, "threshold": BACKLOG_WARN},
        f"任务积压 {pending} 条（阈值 {BACKLOG_WARN}）",
    )
    checks.add(
        "oldestPending",
        oldest_age <= OLDEST_PENDING_WARN,
        {"seconds": round(oldest_age, 1), "threshold": OLDEST_PENDING_WARN},
        f"最老 pending 任务已等待 {oldest_age:.0f}s（阈值 {OLDEST_PENDING_WARN}s）",
    )
    total_finished = failed + done
    rate = (failed / total_finished) if total_finished else 0.0
    checks.add(
        "failureRate",
        rate <= FAILURE_RATE_WARN,
        {"rate": round(rate, 3), "failed": failed, "done": done, "threshold": FAILURE_RATE_WARN},
        f"任务失败率 {rate:.1%}（阈值 {FAILURE_RATE_WARN:.0%}）",
    )


def _match_checks(conn: sqlite3.Connection, checks: Checks, now: float) -> None:
    total = _scalar(conn, "SELECT COUNT(*) FROM matches")
    live = _scalar(conn, "SELECT COUNT(*) FROM matches WHERE ended_at IS NULL")
    # 卡住的比赛：建了超过一小时还没结束。worker 崩过、租约没人接时会这样。
    stuck = _scalar(
        conn,
        "SELECT COUNT(*) FROM matches WHERE ended_at IS NULL AND created_at < ?",
        (_iso_before(now, STUCK_MATCH_SECONDS),),
    )
    checks.details["matches"] = {"total": total, "live": live, "stuck": stuck}
    checks.add(
        "stuckMatches",
        stuck == 0,
        {"stuck": stuck, "thresholdSeconds": STUCK_MATCH_SECONDS},
        f"{stuck} 场比赛超过 {STUCK_MATCH_SECONDS}s 仍显示直播中（worker 可能卡住）",
    )


def _iso_before(now: float, seconds: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(now - seconds, tz=timezone.utc).isoformat()


def prometheus_text(snapshot_data: dict[str, Any]) -> str:
    """把快照转成 Prometheus 文本，方便直接接到现有 /metrics 抓取。"""
    lines: list[str] = []
    for name, detail in (snapshot_data.get("details") or {}).items():
        if not isinstance(detail, dict):
            continue
        for key, value in detail.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                metric = f"melee_{_snake(name)}_{_snake(key)}"
                lines.append(f"{metric} {value}")
    lines.append(f"melee_ops_problems {len(snapshot_data.get('problems') or [])}")
    return "\n".join(lines) + "\n"


def _snake(name: str) -> str:
    out = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            out.append("_")
        out.append(char.lower())
    return "".join(out)
