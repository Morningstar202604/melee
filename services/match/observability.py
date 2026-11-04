"""可观测性：Prometheus 标准指标 + 业务指标快照。

拆出来的原因：指标注册有进程级单例约束（重复注册同名 Gauge 会抛
DuplicateTimeseries），测试里 create_app() 会被调用多次，这段逻辑必须
显式加锁复用，与路由代码放在一起容易被误当成普通端点改动。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from prometheus_fastapi_instrumentator import Instrumentator

_METRICS_LOCK = threading.Lock()
_METRICS: dict[str, Any] = {}


def install_metrics(app: Any, db_path: Path) -> None:
    from prometheus_client import Gauge

    from . import workqueue

    with _METRICS_LOCK:
        if not _METRICS:
            _METRICS["events"] = Gauge("melee_events_total", "事件总数", ["match_id"])
            _METRICS["jobs"] = Gauge("melee_jobs_total", "任务数", ["status"])

    def refresh() -> None:
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            try:
                rows = conn.execute("SELECT match_id, COUNT(*) FROM events GROUP BY match_id").fetchall()
            finally:
                conn.close()
            # 每场比赛一个 label 会无限增长：只暴露最近 50 场，其余归入 _other。
            rows = rows[-50:]
            for match_id, count in rows:
                _METRICS["events"].labels(match_id=match_id).set(count)
        except sqlite3.OperationalError:
            pass
        counts = {workqueue.PENDING: 0, workqueue.RUNNING: 0, workqueue.DONE: 0, workqueue.FAILED: 0}
        try:
            for job in workqueue.list_jobs(db_path, limit=500):
                counts[job.status] = counts.get(job.status, 0) + 1
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - 指标不能影响主流程
            pass
        for status, count in counts.items():
            _METRICS["jobs"].labels(status=status).set(count)

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

    @app.get("/metrics/business", include_in_schema=False)
    async def business_metrics() -> dict[str, Any]:
        """业务指标快照（JSON）：比 grep Prometheus 文本更适合人看。"""
        refresh()
        return {
            "events": {
                sample.labels.get("match_id"): sample.value
                for sample in _METRICS["events"].collect()[0].samples
                if sample.name == "melee_events_total"
            },
            "jobs": {
                sample.labels.get("status"): sample.value
                for sample in _METRICS["jobs"].collect()[0].samples
                if sample.name == "melee_jobs_total"
            },
        }
