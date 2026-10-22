from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.match import api as api_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    api_module.MATCHES.clear()
    # TestClient 不跑 lifespan，内嵌 worker 不会自动起；测试里显式拉起。
    api_module._ensure_inline_worker()
    client = TestClient(api_module.create_app())
    yield client
    api_module._stop_inline_worker()


def wait_finished(match_id: str, timeout: float = 60.0) -> None:
    """等比赛结束。

    写路径走队列后，比赛不再由请求持有；只能看日志里有没有终态事件。
    """
    import time

    from services.match.eventlog import read_match

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = read_match(api_module.DB_PATH, match_id)
        except Exception:  # noqa: BLE001 - 表还没建好
            events = []
        if any(event["type"] == "match.ended" for event in events):
            return
        time.sleep(0.05)
    raise TimeoutError(f"{match_id} 在 {timeout}s 内没有结束")


def wait_job(job_id: str, timeout: float = 30.0) -> None:
    """等任务被 worker 标记完成。"""
    import time

    from services.match import workqueue

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = workqueue.get(api_module.DB_PATH, job_id)
        if record is not None and record.status == "done":
            return
        time.sleep(0.05)
    raise TimeoutError(f"{job_id} 未在 {timeout}s 内完成")


def test_create_season_builds_round_robin(client):
    response = client.post(
        "/seasons",
        json={
            "seasonId": "s1",
            "name": "2026 秋季",
            "players": [{"id": p} for p in ["A", "B", "C", "D"]],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["fixtures"]) == 6
    assert {f["round"] for f in body["fixtures"]} == {1, 2, 3}


def test_season_requires_unique_players(client):
    response = client.post(
        "/seasons",
        json={"seasonId": "s1", "name": "x", "players": [{"id": "A"}, {"id": "A"}]},
    )
    assert response.status_code == 400


def test_unknown_season_is_404(client):
    assert client.get("/seasons/nope").status_code == 404


def test_match_into_season_updates_standings(client):
    client.post(
        "/seasons",
        json={
            "seasonId": "s1",
            "name": "2026 秋季",
            "players": [{"id": "A"}, {"id": "B"}],
        },
    )

    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "seed": 7,
            "seasonId": "s1",
            "players": [{"id": "A"}, {"id": "B"}],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # 等 job 完成而不是只等事件：记分写在 complete 之前，
    # 只盯 match.ended 会在"已结束但还没记分"的窗口里读到 0 场。
    wait_job(body["jobId"])
    wait_finished(body["matchId"])

    season = client.get("/seasons/s1").json()
    standings = {row["playerId"]: row for row in season["standings"]}
    assert sum(row["played"] for row in standings.values()) == 2
    assert sum(row["wins"] for row in standings.values()) == 1


def test_match_with_unknown_season_is_404(client):
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "players": [{"id": "A"}, {"id": "B"}],
            "seasonId": "nope",
        },
    )
    assert response.status_code == 404


def test_recap_endpoint_is_consistent_with_events(client):
    response = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 7, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = response.json()["matchId"]
    wait_finished(match_id)

    recap = client.get(f"/matches/{match_id}/recap").json()
    events = client.get(f"/matches/{match_id}/events").json()["events"]

    applied = [e for e in events if e["type"] == "action.applied"]
    assert recap["asks"] == sum(1 for e in applied if e["payload"]["action"]["kind"] == "ask")
    assert recap["guesses"] == sum(1 for e in applied if e["payload"]["action"]["kind"] == "guess")
    assert recap["oneLine"]
    assert recap["highlights"]


def test_recap_404_for_unknown_match(client):
    assert client.get("/matches/nope/recap").status_code == 404
