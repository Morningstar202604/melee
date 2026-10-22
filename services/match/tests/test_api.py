from __future__ import annotations

import asyncio
import json
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


def create(client, **overrides):
    body = {
        "game": "twenty-questions",
        "seed": 7,
        "maxQuestions": 21,
        "players": [{"id": "A"}, {"id": "B"}],
    }
    body.update(overrides)
    response = client.post("/matches", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_returns_match_id_and_players(client):
    data = create(client)
    assert data["matchId"].startswith("m_")
    assert data["players"] == ["A", "B"]


def test_rejects_unsupported_game(client):
    response = client.post(
        "/matches",
        json={"game": "chess", "players": [{"id": "A"}, {"id": "B"}]},
    )
    assert response.status_code == 400


def test_rejects_duplicate_players(client):
    response = client.post(
        "/matches",
        json={"game": "twenty-questions", "players": [{"id": "A"}, {"id": "A"}]},
    )
    assert response.status_code == 400


def test_unknown_match_is_404(client):
    assert client.get("/matches/nope/events").status_code == 404
    assert client.get("/matches/nope/view").status_code == 404
    assert client.get("/matches/nope/stream").status_code == 404


def test_events_are_monotonic_and_replayable(client):
    data = create(client)
    wait_finished(data["matchId"])

    payload = client.get(f"/matches/{data['matchId']}/events").json()
    seqs = [e["seq"] for e in payload["events"]]
    assert seqs == list(range(len(seqs)))
    assert payload["nextAfter"] == seqs[-1]

    tail = client.get(f"/matches/{data['matchId']}/events", params={"after": seqs[-2]}).json()
    assert [e["seq"] for e in tail["events"]] == [seqs[-1]]

    view = client.get(f"/matches/{data['matchId']}/view").json()
    assert view["winner"] in {"A", "B"}
    assert view["turns"]


def _sse_body(response) -> str:
    """SSE 帧的换行由 sse-starlette 决定（CRLF），断言前先归一成 LF。"""
    return "".join(response.iter_text()).replace("\r\n", "\n")


def test_stream_replays_from_last_event_id(client):
    data = create(client)
    wait_finished(data["matchId"])
    match_id = data["matchId"]

    with client.stream("GET", f"/matches/{match_id}/stream", headers={"Last-Event-ID": "3"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = _sse_body(response)

    frames = [line for line in body.split("\n") if line.startswith("id: ")]
    assert frames[0] == "id: 4", frames[:3]


def test_stream_stops_at_match_end(client):
    data = create(
        client,
        players=[
            {"id": "A", "script": ["guess:不存在的答案"]},
            {"id": "B"},
        ],
    )
    wait_finished(data["matchId"])

    with client.stream("GET", f"/matches/{data['matchId']}/stream") as response:
        body = _sse_body(response)

    events = [json.loads(chunk.split("data: ", 1)[1]) for chunk in body.split("\n\n") if "data: " in chunk]
    assert events[-1]["type"] == "match.ended"
    assert events[-1]["payload"]["winner"] == "B"


def test_seed_is_reproducible(client):
    first = create(client, seed=99)
    wait_finished(first["matchId"])
    second = create(client, seed=99)
    wait_finished(second["matchId"])

    def outcomes(match_id: str) -> list[str]:
        events = client.get(f"/matches/{match_id}/events").json()["events"]
        return [
            json.dumps(e["payload"]["action"], ensure_ascii=False)
            for e in events
            if e["type"] == "action.applied"
        ]

    assert outcomes(first["matchId"]) == outcomes(second["matchId"])


def test_events_available_immediately_after_create(client):
    """建场返回后立刻拉事件不能 404：worker 可能还没领取任务。"""
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "seed": 7,
            "inlineWorker": False,
            "players": [{"id": "A"}, {"id": "B"}],
        },
    )
    assert response.status_code == 201, response.text
    match_id = response.json()["matchId"]

    payload = client.get(f"/matches/{match_id}/events")
    assert payload.status_code == 200, payload.text
    assert payload.json()["events"] == []

    with client.stream("GET", f"/matches/{match_id}/stream") as stream:
        assert stream.status_code == 200


# ---------- 安全响应头 / 请求体上限 / 探针 ----------

def test_security_headers_are_present(client):
    response = client.get("/games")
    for header in ("x-content-type-options", "x-frame-options", "referrer-policy", "cross-origin-resource-policy"):
        assert response.headers.get(header), f"缺少安全响应头 {header}"


def test_oversized_body_is_rejected(client):
    """体积门禁按 Content-Length 提前拒绝，不把 2MB 以上的 body 交给解析器。"""
    payload = {
        "game": "twenty-questions",
        "players": [{"id": "A"}, {"id": "B"}],
        "inlineWorker": False,
        "padding": "x" * (3 * 1024 * 1024),
    }
    response = client.post("/matches", json=payload)
    assert response.status_code == 413, response.text[:200]


def test_script_list_has_an_item_cap(client):
    """体积上限之外还要有结构化上限：不然 2MB 以内仍能塞十万条。"""
    payload = {
        "game": "twenty-questions",
        "players": [{"id": "A", "script": ["ask:x=y"] * 5000}, {"id": "B"}],
        "inlineWorker": False,
    }
    response = client.post("/matches", json=payload)
    assert response.status_code == 422, response.text[:200]


def test_health_reports_checks(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"db": True, "queue": True}


def test_metrics_is_prometheus_text(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "melee_events_total" in response.text
    assert response.headers["content-type"].startswith("text/plain")


def test_openapi_hidden_in_token_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "m.db")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "a:t")
    api_module.MATCHES.clear()
    client = TestClient(api_module.create_app())
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


def test_frontend_is_served_from_same_origin(client):
    """单端口部署：根路径直接出观战页，API 仍可用。"""
    root = client.get("/")
    assert root.status_code == 200
    assert "melee" in root.text.lower()
    assert client.get("/index.html").status_code == 200
    assert client.get("/season.html").status_code == 200
    assert client.get("/games").status_code == 200
    assert client.get("/health").status_code == 200
