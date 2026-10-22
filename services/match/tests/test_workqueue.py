from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from services.match import workqueue
from services.match.worker import Worker, WorkerConfig, enqueue_match, enqueue_render


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "match.db"


def test_enqueue_and_get(db):
    job_id = workqueue.enqueue(db, "demo", {"x": 1})
    job = workqueue.get(db, job_id)
    assert job is not None
    assert job.status == workqueue.PENDING
    assert job.payload == {"x": 1}


def test_claim_marks_running_and_increments_attempts(db):
    workqueue.enqueue(db, "demo", {})
    job = workqueue.claim(db, "worker-1", ["demo"], lease_s=30)
    assert job is not None
    assert job.status == workqueue.RUNNING
    assert job.attempts == 1


def test_second_claim_returns_none_while_leased(db):
    workqueue.enqueue(db, "demo", {})
    first = workqueue.claim(db, "w1", ["demo"], lease_s=30)
    assert first is not None
    assert workqueue.claim(db, "w2", ["demo"], lease_s=30) is None


def test_expired_lease_is_reclaimable(db):
    workqueue.enqueue(db, "demo", {})
    first = workqueue.claim(db, "w1", ["demo"], lease_s=-1)  # 立刻过期
    assert first is not None
    second = workqueue.claim(db, "w2", ["demo"], lease_s=30)
    assert second is not None
    assert second.attempts == 2


def test_complete_moves_to_done(db):
    job_id = workqueue.enqueue(db, "demo", {})
    workqueue.claim(db, "w1", ["demo"])
    workqueue.complete(db, job_id)
    assert workqueue.get(db, job_id).status == workqueue.DONE


def test_fail_retries_until_max_attempts(db):
    job_id = workqueue.enqueue(db, "demo", {}, max_attempts=2)

    workqueue.claim(db, "w1", ["demo"])
    assert workqueue.fail(db, job_id, "boom") == workqueue.PENDING

    workqueue.claim(db, "w1", ["demo"])
    assert workqueue.fail(db, job_id, "boom") == workqueue.FAILED


def test_cannot_claim_beyond_max_attempts(db):
    workqueue.enqueue(db, "demo", {}, max_attempts=1)
    workqueue.claim(db, "w1", ["demo"])
    workqueue.fail(db, workqueue.list_jobs(db)[0].job_id, "boom")
    assert workqueue.claim(db, "w1", ["demo"]) is None


def test_kinds_are_isolated(db):
    workqueue.enqueue(db, "a", {})
    assert workqueue.claim(db, "w1", ["b"]) is None
    assert workqueue.claim(db, "w1", ["a"]) is not None


def test_list_filters_by_status(db):
    workqueue.enqueue(db, "a", {})
    workqueue.enqueue(db, "b", {})
    claimed = workqueue.claim(db, "w1", ["a"])
    workqueue.complete(db, claimed.job_id)
    assert len(workqueue.list_jobs(db, status=workqueue.DONE)) == 1
    assert len(workqueue.list_jobs(db, status=workqueue.PENDING)) == 1


# ---------- worker ----------

def test_worker_runs_a_queued_match(db):
    enqueue_match(db, match_id="m1", seed=7, players=["A", "B"])
    worker = Worker(db, WorkerConfig(kinds=("run-match",)))
    job = worker.run_once()
    assert job is not None
    assert worker.processed == 1

    from services.match.eventlog import read_match

    events = read_match(db, "m1")
    assert any(e["type"] == "match.ended" for e in events)


def test_worker_returns_none_when_idle(db):
    worker = Worker(db, WorkerConfig(kinds=("run-match",)))
    assert worker.run_once() is None


def test_worker_marks_failure_and_retries(db, monkeypatch):
    from services.match import worker as worker_module

    calls = {"n": 0}

    def boom(_db, _payload):
        calls["n"] += 1
        raise RuntimeError("故意失败")

    monkeypatch.setitem(worker_module.HANDLERS, "demo", boom)
    job_id = workqueue.enqueue(db, "demo", {}, max_attempts=2)

    worker = Worker(db, WorkerConfig(kinds=("demo",)))
    worker.run_once()
    assert workqueue.get(db, job_id).status == workqueue.PENDING

    worker.run_once()
    assert workqueue.get(db, job_id).status == workqueue.FAILED
    assert calls["n"] == 2


def test_queued_match_can_resume_after_crash(db):
    """第一次只跑一半就"崩溃"，重跑应当接着跑完而不是重头。"""
    enqueue_match(db, match_id="m2", seed=11, players=["A", "B"])

    from services.match import worker as worker_module

    original = worker_module.HANDLERS["run-match"]

    class Crash(RuntimeError):
        pass

    def half(_db, payload):
        # 先正常跑一遍（产生完整日志），再抛错模拟"跑完没来得及标记完成"
        original(_db, payload)
        raise Crash("模拟崩溃")

    worker_module.HANDLERS["run-match"] = half
    try:
        worker = Worker(db, WorkerConfig(kinds=("run-match",)))
        worker.run_once()
    finally:
        worker_module.HANDLERS["run-match"] = original

    from services.match.eventlog import read_match

    first = read_match(db, "m2")
    assert first, "崩溃前应已有事件"

    worker2 = Worker(db, WorkerConfig(kinds=("run-match",)))
    worker2.run_once()

    second = read_match(db, "m2")
    seqs = [e["seq"] for e in second]
    assert seqs == list(range(len(seqs))), "重跑不能产生重复或断号"
    assert sum(1 for e in second if e["type"] == "match.started") == 1
