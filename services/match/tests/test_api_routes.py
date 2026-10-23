"""HTTP 路由覆盖：把 api.py 里每个端点至少真实打一次。

前面的测试聚焦"行为"，这里聚焦"接线"：路径、状态码、鉴权、返回结构。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.match import api as api_module


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    api_module.MATCHES.clear()
    api_module._ensure_inline_worker()
    client = TestClient(api_module.create_app())
    yield client
    api_module._stop_inline_worker()


def create(client, **overrides):
    body = {"game": "twenty-questions", "seed": 7, "maxQuestions": 6,
            "players": [{"id": "A"}, {"id": "B"}]}
    body.update(overrides)
    response = client.post("/matches", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def wait_finished(match_id: str, timeout: float = 60.0) -> None:
    from services.match.eventlog import read_match

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = read_match(api_module.DB_PATH, match_id)
        except Exception:  # noqa: BLE001
            events = []
        if any(e["type"] == "match.ended" for e in events):
            return
        time.sleep(0.05)
    raise TimeoutError(match_id)


def test_games_catalog(client):
    body = client.get("/games").json()
    assert body["count"] >= 2
    assert any(g["gameId"] == "twenty-questions" for g in body["games"])


def test_list_matches_and_state(client):
    data = create(client)
    wait_finished(data["matchId"])

    listing = client.get("/matches").json()
    assert data["matchId"] in listing["matches"]

    state = client.get(f"/matches/{data['matchId']}/state").json()
    assert state["game"] == "twenty-questions"
    assert state["finished"] is True
    assert state["source"] == "log"


def test_recap_and_clips_manifest(client):
    data = create(client)
    wait_finished(data["matchId"])

    recap = client.get(f"/matches/{data['matchId']}/recap").json()
    assert recap["oneLine"]
    assert recap["problems"] == []

    manifest = client.get(f"/matches/{data['matchId']}/clips").json()
    assert manifest["clipCount"] >= 1
    assert manifest["clips"][0]["eventCount"] > 0


def test_director_routes_on_live_match(client):
    """导播台：列表 → 详情 → 人工切镜（写进同一条事件流）。"""
    data = create(client, players=[{"id": "A", "delayMs": 300}, {"id": "B", "delayMs": 300}])
    match_id = data["matchId"]

    # 等它至少开始写事件
    for _ in range(60):
        events = client.get(f"/matches/{match_id}/events").json()["events"]
        if events:
            break
        time.sleep(0.05)

    live = client.get("/director/matches").json()
    assert isinstance(live["matches"], list)

    detail = client.get(f"/director/{match_id}").json()
    assert detail["matchId"] == match_id
    assert "cues" in detail and "recent" in detail

    cue = client.post(f"/director/{match_id}/cue", json={"shot": "close", "durationMs": 900, "note": "看这里"})
    if cue.status_code == 200:
        assert cue.json()["event"]["type"] == "director.cue"
        assert cue.json()["event"]["payload"]["trigger"].startswith("人工")
    else:
        # 比赛可能已经结束（快棋），那也是正确的拒绝
        assert cue.status_code == 400
        assert "已结束" in cue.text


def test_director_rejects_unknown_shot(client):
    data = create(client)
    wait_finished(data["matchId"])
    response = client.post(f"/director/{data['matchId']}/cue", json={"shot": "zoom"})
    assert response.status_code == 400


def test_jobs_routes(client):
    data = create(client)
    wait_finished(data["matchId"])
    job = client.get(f"/jobs/{data['jobId']}").json()
    assert job["jobId"] == data["jobId"]
    assert job["status"] in {"pending", "running", "done", "failed"}
    listing = client.get("/jobs").json()
    assert any(item["jobId"] == data["jobId"] for item in listing["jobs"])


def test_quota_endpoint(client):
    body = client.get("/quota").json()
    assert body["principal"]
    assert "matches" in body and "renders" in body


def test_auth_ticket_endpoint(client):
    body = client.post("/auth/ticket").json()
    assert body["ticket"] and body["ttlSec"] == 60


def test_media_ticket_endpoint(client):
    data = create(client)
    body = client.post(f"/matches/{data['matchId']}/media-ticket").json()
    assert body["ticket"] and body["ttlSec"] == 600


def test_season_create_list_and_run(client):
    created = client.post("/seasons", json={
        "seasonId": "s1", "name": "测试赛季",
        "players": [{"id": p} for p in "ABCD"],
        "startAt": 1_700_000_000, "intervalMinutes": 30,
    })
    assert created.status_code == 201, created.text
    assert len(created.json()["fixtures"]) == 6

    detail = client.get("/seasons/s1").json()
    assert detail["name"] == "测试赛季"
    assert all("played" in f for f in detail["fixtures"])

    run = client.post("/seasons/s1/run").json()
    assert run["played"] == 6
    assert run["skipped"] == 0
    assert run["standings"]

    again = client.post("/seasons/s1/run").json()
    assert again["played"] == 0 and again["skipped"] == 6


def test_render_endpoint_without_ffmpeg_returns_503(client, monkeypatch):
    from services.match import api as api_module

    data = create(client)
    wait_finished(data["matchId"])
    monkeypatch.setattr(api_module, "find_ffmpeg", lambda: None)
    response = client.post(f"/matches/{data['matchId']}/clips/render")
    assert response.status_code == 503


def test_render_async_enqueues_job(client, monkeypatch):
    from services.match import api as api_module

    data = create(client)
    wait_finished(data["matchId"])
    monkeypatch.setattr(api_module, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    response = client.post(f"/matches/{data['matchId']}/clips/render?async=true")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["jobId"]


def test_unknown_match_endpoints_are_404(client):
    for path in ("/matches/nope/state", "/matches/nope/recap", "/matches/nope/clips",
                 "/matches/nope/events", "/matches/nope/view"):
        assert client.get(path).status_code == 404, path
    assert client.post("/director/nope/cue", json={"shot": "wide"}).status_code == 400


def test_download_clip_serves_file_and_blocks_traversal(client):
    match_id = "m_clip"
    clips_dir = api_module.DB_PATH.parent / "clips" / match_id
    clips_dir.mkdir(parents=True, exist_ok=True)
    (clips_dir / "01-guess.mp4").write_bytes(b"fake-mp4-bytes")

    ok = client.get(f"/matches/{match_id}/clips/01-guess.mp4")
    assert ok.status_code == 200
    assert ok.content == b"fake-mp4-bytes"
    assert ok.headers["content-type"].startswith("video/mp4")

    assert client.get("/matches/m_clip/clips/../secret.mp4").status_code in {400, 404}
    assert client.get("/matches/m_clip/clips/none.mp4").status_code == 404


def test_stream_with_last_event_id_replays_tail(client):
    data = create(client)
    wait_finished(data["matchId"])
    with client.stream("GET", f"/matches/{data['matchId']}/stream", headers={"Last-Event-ID": "2"}) as response:
        body = "".join(response.iter_text()).replace("\r\n", "\n")
    ids = [line for line in body.split("\n") if line.startswith("id: ")]
    assert ids and ids[0] == "id: 3"


def test_render_sync_returns_clips(client, monkeypatch, tmp_path):
    from services.match import api as api_module
    from services.match.render import RenderResult

    data = create(client)
    wait_finished(data["matchId"])
    monkeypatch.setattr(api_module, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")

    def fake_render(clip, out_path, **_kwargs):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"mp4")
        return RenderResult(path=out_path, frames=10, duration_ms=1000, bytes=3)

    monkeypatch.setattr("services.match.render.render_clip", fake_render)
    response = client.post(f"/matches/{data['matchId']}/clips/render?limit=1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clips"] and body["clips"][0]["file"].endswith(".mp4")
    assert body["clips"][0]["url"].startswith(f"/matches/{data['matchId']}/clips/")


def test_error_handlers_return_json_not_html(client):
    """未知路径必须返回 JSON 404（前端 fetch 直接解析），不能是 HTML。"""
    response = client.get("/no-such-route")
    assert response.status_code == 404
    assert "application/json" in response.headers["content-type"]


def test_rate_limit_returns_429_with_retry_after(client):
    """建场接口 10/分钟：第 11 次必须 429 且带 Retry-After。"""
    from services.match import ratelimit

    ratelimit.reset_limiter()
    for _ in range(10):
        client.post("/matches", json={"game": "twenty-questions",
                                     "players": [{"id": "A"}, {"id": "B"}],
                                     "inlineWorker": False})
    response = client.post("/matches", json={"game": "twenty-questions",
                                             "players": [{"id": "A"}, {"id": "B"}],
                                             "inlineWorker": False})
    assert response.status_code == 429
    assert response.headers.get("retry-after") == "60"


def test_quota_exhausted_returns_429(client, monkeypatch):
    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "1")
    from services.match.settings import reset_settings

    reset_settings()
    first = client.post("/matches", json={"game": "twenty-questions",
                                          "players": [{"id": "A"}, {"id": "B"}],
                                          "inlineWorker": False})
    assert first.status_code == 201
    second = client.post("/matches", json={"game": "twenty-questions",
                                           "players": [{"id": "A"}, {"id": "B"}],
                                           "inlineWorker": False})
    assert second.status_code == 429
    assert "配额" in second.text


def test_invalid_game_and_player_count(client):
    bad_game = client.post("/matches", json={"game": "chess", "players": [{"id": "A"}, {"id": "B"}]})
    assert bad_game.status_code == 400
    assert "暂不支持" in bad_game.text

    bad_count = client.post("/matches", json={"game": "codenames", "players": [{"id": "A"}, {"id": "B"}]})
    assert bad_count.status_code == 400
    assert "名选手" in bad_count.text


def test_duplicate_player_ids_rejected(client):
    response = client.post("/matches", json={"game": "twenty-questions",
                                             "players": [{"id": "A"}, {"id": "A"}]})
    assert response.status_code == 400
    assert "唯一" in response.text
