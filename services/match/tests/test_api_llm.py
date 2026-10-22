from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.match import api as api_module
from .fake_openai import FakeOpenAI

SECRET = "sk-live-must-never-appear"


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


def test_llm_player_plays_via_api(client):
    with FakeOpenAI() as server:
        server.replies = [
            {"content": '{"kind":"ask","value":"category=建筑","thought":"先砍掉一半"}', "reasoning": "建筑类通常更大"},
        ]
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "seed": 5,
                "players": [
                    {
                        "id": "A",
                        "model": {
                            "baseUrl": server.base_url,
                            "model": "fake-model",
                            "apiKey": SECRET,
                        },
                    },
                    {"id": "B"},
                ],
            },
        )
        assert response.status_code == 201, response.text
        match_id = response.json()["matchId"]
        wait_finished(match_id)

    events = client.get(f"/matches/{match_id}/events").json()["events"]
    kinds = [e["type"] for e in events]
    assert "thought.delta" in kinds
    assert "match.ended" in kinds

    thoughts = [e["payload"]["text"] for e in events if e["type"] == "thought.delta"]
    assert "建筑类通常更大" in thoughts

    proposed = [e for e in events if e["type"] == "action.proposed" and e["player"] == "A"]
    assert "category=建筑" in proposed[0]["payload"]["raw"]


def test_key_never_appears_in_events_or_view(client):
    with FakeOpenAI() as server:
        server.replies = [{"content": '{"kind":"guess","value":"灯塔"}'}]
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "seed": 5,
                "players": [
                    {
                        "id": "A",
                        "model": {"baseUrl": server.base_url, "model": "m", "apiKey": SECRET},
                    },
                    {"id": "B"},
                ],
            },
        )
        match_id = response.json()["matchId"]
        wait_finished(match_id)

    events = client.get(f"/matches/{match_id}/events").json()["events"]
    views = client.get(f"/matches/{match_id}/view").json()
    streamed = ""
    with client.stream("GET", f"/matches/{match_id}/stream") as stream:
        streamed = "".join(stream.iter_text())

    for blob in (str(events), str(views), streamed):
        assert SECRET not in blob


def test_upstream_error_body_is_redacted_in_events(client):
    with FakeOpenAI() as server:
        server.status = 401
        response = client.post(
            "/matches",
            json={
                "game": "twenty-questions",
                "seed": 5,
                "players": [
                    {
                        "id": "A",
                        "model": {"baseUrl": server.base_url, "model": "m", "apiKey": SECRET},
                    },
                    {"id": "B"},
                ],
            },
        )
        match_id = response.json()["matchId"]
        wait_finished(match_id)

    events = client.get(f"/matches/{match_id}/events").json()["events"]
    rejected = [e for e in events if e["type"] == "action.rejected"]
    assert rejected, "上游 401 应导致动作被拒"
    assert SECRET not in str(events)
    assert "***" in str(events)


def test_api_rejects_missing_model_fields(client):
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "players": [{"id": "A", "model": {"baseUrl": "http://x/v1"}}, {"id": "B"}],
        },
    )
    assert response.status_code == 422
