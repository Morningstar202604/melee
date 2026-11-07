"""崩溃恢复与队列一致性：终局事件补齐、租约归属、孤儿任务回收。"""

from __future__ import annotations

import sqlite3

import pytest

from services.match import api as api_module
from services.match import workqueue
from services.match.eventlog import EventLog, read_match
from services.match.runner import Runner, ensure_terminal

from .support import create, wait_finished

# ---- 崩溃恢复 ----

def _append_actions_to_finished(tmp_path, match_id: str = "m_resume") -> EventLog:
    """造一场"状态已终局但缺 match.ended"的日志。"""
    from services.match.engine.twenty_questions import CATALOG, TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 6})
    log = EventLog.open(tmp_path / "resume.db", match_id)
    log.create_match(game=game.game_id, seed=3, players=["A", "B"])

    # 直接构造一个已结束的状态，模拟"最后一步写完但终局事件没落"。
    finished = {**state, "finished": True, "winner": "A", "endReason": "测试终局"}
    assert CATALOG  # 保持导入被使用，避免误删
    return log, game, finished

def test_ensure_terminal_appends_exactly_one_end_event(tmp_path):
    log, game, finished = _append_actions_to_finished(tmp_path)
    try:
        assert ensure_terminal(log, game, finished) is True
        assert ensure_terminal(log, game, finished) is False
        ended = [event for event in log.read() if event["type"] == "match.ended"]
        assert len(ended) == 1
        assert ended[0]["payload"]["winner"] == "A"
    finally:
        log.close()

def test_runner_finish_is_idempotent_for_same_log(tmp_path):
    log, game, finished = _append_actions_to_finished(tmp_path)
    try:
        log.append("match.ended", {"winner": "A", "reason": "第一次", "finalState": {}})
        runner = Runner(game, log, {"A": None, "B": None})
        result = runner._finish(finished, "第二次", "A", 1)
        assert result.winner == "A"
        ended = [event for event in log.read() if event["type"] == "match.ended"]
        assert len(ended) == 1
    finally:
        log.close()

def test_worker_repairs_missing_terminal_event(tmp_path, monkeypatch):
    from services.match import worker as worker_module

    db = tmp_path / "resume.db"
    log, game, finished = _append_actions_to_finished(tmp_path)
    log.append("match.started", {"firstPlayer": "A"})
    log.close()

    payload = {
        "matchId": "m_resume",
        "game": "twenty-questions",
        "seed": 3,
        "players": ["A", "B"],
        "maxQuestions": 6,
        "specs": {"data": [], "encrypted": False},
    }
    # 让 rehydrate 看到终局：补一条能重放出 finished 的动作不现实，
    # 这里直接替换 _resume_state 返回预置状态，只验证补齐逻辑。
    from services.match.worker import _Snapshot

    monkeypatch.setattr(
        worker_module, "_resume_state", lambda db_path, match_id: _Snapshot(state=finished, game=game, events=True)
    )
    worker_module.run_match_job(db, payload)

    events = read_match(db, "m_resume")
    assert sum(1 for event in events if event["type"] == "match.ended") == 1
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT ended_at FROM matches WHERE match_id = 'm_resume'").fetchone()
    finally:
        conn.close()
    assert row[0] is not None


def test_archive_status_reflects_terminal_marking(client):
    """补写终局事件时必须同时补 ended_at，否则这场永远算"直播中"。"""
    from services.match.engine.twenty_questions import TwentyQuestions
    from services.match.runner import ensure_terminal

    game = TwentyQuestions()
    state = game.init(seed=1, players=["A", "B"], config={})
    log = EventLog.open(api_module.DB_PATH, "m_terminal")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        finished = {**state, "finished": True, "winner": "A", "endReason": "测试"}
        assert ensure_terminal(log, game, finished) is True
    finally:
        log.close()
    data = client.get("/archive").json()
    item = next(row for row in data["items"] if row["matchId"] == "m_terminal")
    assert item["status"] == "ended"
    assert item["endedAt"]


def test_mark_ended_is_idempotent(tmp_path):
    log = EventLog.open(tmp_path / "idem.db", "m1")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        log.mark_ended()
        first = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()[0]
        log.mark_ended()
        second = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()[0]
        assert first == second
    finally:
        log.close()


def test_create_match_does_not_wipe_ended_at(tmp_path):
    """重跑同一场时 INSERT OR IGNORE 不能把已结束的行重置。"""
    log = EventLog.open(tmp_path / "keep.db", "m1")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        log.mark_ended()
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        row = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()
        assert row[0] is not None
    finally:
        log.close()


def test_workqueue_complete_requires_owner(tmp_path):
    from services.match import workqueue

    db = tmp_path / "q2.db"
    job_id = workqueue.enqueue(db, "run-match", {"matchId": "m"})
    workqueue.claim(db, "w1", ["run-match"], lease_s=60)
    assert workqueue.complete(db, job_id, owner="intruder") is False
    assert workqueue.get(db, job_id).status == "running"
    assert workqueue.complete(db, job_id, owner="w1") is True
    assert workqueue.get(db, job_id).status == "done"


def test_workqueue_reap_marks_orphan_running_jobs(tmp_path):
    from services.match import workqueue

    db = tmp_path / "q.db"
    job_id = workqueue.enqueue(db, "run-match", {"matchId": "m"}, max_attempts=1)
    workqueue.claim(db, "w1", ["run-match"], lease_s=0.01)
    import time as _time

    _time.sleep(0.05)
    assert workqueue.reap(db) == 1
    assert workqueue.get(db, job_id).status == "failed"
