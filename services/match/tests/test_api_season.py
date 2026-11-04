

import sqlite3

from services.match import api as api_module
from services.match.eventlog import read_match

from .support import create, make_client, wait_finished


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


# ---- 赛季赛制一致性（原 test_archive.py） ----

# ---- 赛季赛制 ----

def test_season_rejects_non_two_player_game(client):
    response = client.post(
        "/seasons",
        json={
            "seasonId": "s-cn",
            "name": "代号联赛",
            "game": "codenames",
            "players": [{"id": p} for p in ("A", "B")],
        },
    )
    assert response.status_code == 400
    assert "双人" in response.json()["detail"]

def test_season_persists_game_and_runs_that_engine(client):
    response = client.post(
        "/seasons",
        json={
            "seasonId": "s-ttt",
            "name": "井字棋联赛",
            "game": "tic-tac-toe",
            "players": [{"id": "X"}, {"id": "O"}],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["game"] == "tic-tac-toe"

    run = client.post("/seasons/s-ttt/run")
    assert run.status_code == 200, run.text
    assert run.json()["played"] == 1

    season = client.get("/seasons/s-ttt").json()
    assert season["game"] == "tic-tac-toe"
    match_id = season["results"][0]["matchId"]
    created = next(
        event for event in read_match(api_module.DB_PATH, match_id) if event["type"] == "match.created"
    )
    assert created["payload"]["game"] == "tic-tac-toe"

def test_season_list_endpoint(client):
    client.post(
        "/seasons",
        json={"seasonId": "s1", "name": "秋季", "players": [{"id": "A"}, {"id": "B"}]},
    )
    data = client.get("/seasons").json()
    assert data["count"] == 1
    assert data["seasons"][0]["seasonId"] == "s1"
    assert data["seasons"][0]["fixtures"] == 1

def test_season_migrates_old_schema(tmp_path):
    from services.match.season import load

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE seasons (season_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);"
        "INSERT INTO seasons VALUES ('old', '旧赛季', '2026-01-01');"
    )
    conn.commit()
    season = load(conn, "old")
    conn.close()
    assert season.game == "twenty-questions"
    assert season.name == "旧赛季"
