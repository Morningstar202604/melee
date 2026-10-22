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


def test_renew_extends_lease_and_rejects_other_owner(db):
    workqueue.enqueue(db, "demo", {})
    job = workqueue.claim(db, "w1", ["demo"], lease_s=1)
    assert job is not None
    time.sleep(1.2)
    # 租约已过期，但还没人接手，原 owner 仍可续租
    assert workqueue.renew(db, job.job_id, "w1", lease_s=30) is True
    # 别人不能续
    assert workqueue.renew(db, job.job_id, "w2", lease_s=30) is False


def test_renewed_lease_prevents_duplicate_claim(db):
    """长任务续租后，第二个 worker 不应再领到同一场。"""
    workqueue.enqueue(db, "demo", {})
    job = workqueue.claim(db, "w1", ["demo"], lease_s=1)
    assert job is not None
    time.sleep(1.2)
    assert workqueue.renew(db, job.job_id, "w1", lease_s=30) is True
    assert workqueue.claim(db, "w2", ["demo"], lease_s=30) is None


def test_long_job_is_not_claimed_twice_by_two_workers(db, monkeypatch):
    """回归：两个 worker 同时跑长任务时，只能执行一次。

    复现条件：租约短于任务时长。修复前第二个 worker 会在租约到期后
    把同一场再跑一遍，事件流里出现两条 match.ended（实测 seq 375 / 384）。
    """
    import threading
    import time as _time

    from services.match.worker import HANDLERS, Worker, WorkerConfig

    runs: list[str] = []
    gate = threading.Event()

    def slow_handler(_db, payload):
        runs.append(payload["id"])
        # 跑 2.5 秒，租约只有 1 秒
        gate.wait(2.5)

    monkeypatch.setitem(HANDLERS, "demo", slow_handler)
    workqueue.enqueue(db, "demo", {"id": "x"})

    cfg = WorkerConfig(kinds=("demo",), lease_s=1.0, interval_s=0.1)
    w1 = Worker(db, cfg)
    w2 = Worker(db, cfg)

    t1 = threading.Thread(target=w1.run_once)
    t1.start()
    _time.sleep(1.3)  # 让 w1 的原始租约过期
    w2.run_once()
    t1.join(timeout=5)

    assert runs == ["x"], f"同一任务被执行了 {len(runs)} 次"


def test_worker_concurrency_runs_jobs_in_parallel(db, monkeypatch):
    """慢任务占住一个线程时，另一个线程仍能领到下一场。"""
    import threading
    import time as _time

    from services.match.worker import HANDLERS, Worker, WorkerConfig

    started: list[str] = []
    release = threading.Event()

    def slow(_db, payload):
        started.append(payload["id"])
        release.wait(3)

    monkeypatch.setitem(HANDLERS, "demo", slow)
    workqueue.enqueue(db, "demo", {"id": "a"})
    workqueue.enqueue(db, "demo", {"id": "b"})

    config = WorkerConfig(kinds=("demo",), interval_s=0.05, lease_s=30, concurrency=2)
    worker = Worker(db, config)
    worker.start()
    for _ in range(40):
        if len(started) >= 2:
            break
        _time.sleep(0.05)
    release.set()
    worker.stop()
    assert sorted(started) == ["a", "b"], started
