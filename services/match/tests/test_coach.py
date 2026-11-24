"""教练席：指令校验、队列语义、路由与 runner 投递。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from services.match.coach import CoachBook, CoachError, enqueue, validate_instruction
from services.match.episode import build_export, parse_episode
from services.match.eventlog import read_match
from services.match.routes.matches import CoachCommandIn

from .support import make_client, wait_finished


def test_instruction_validation():
    assert validate_instruction("  先换马  ") == "先换马"
    try:
        validate_instruction("")
    except CoachError as exc:
        assert "不能为空" in str(exc)
    else:
        raise AssertionError("empty instruction should fail")
    assert len(validate_instruction("x" * 280)) == 280
    try:
        validate_instruction("x" * 281)
    except CoachError as exc:
        assert "280" in str(exc)
    else:
        raise AssertionError("long instruction should fail")


def test_queue_is_one_shot_and_bounded(tmp_path: Path):
    db_path = Path(tmp_path) / "coach.db"
    book = CoachBook(db_path, "m_test")
    for index in range(4):
        enqueue(db_path, "m_test", "A", f"指令 {index}")
    first = book.take("A")
    assert [command.instruction for command in first] == ["指令 0", "指令 1", "指令 2"]
    remaining = book.take("A")
    assert len(remaining) == 1
    assert remaining[0].command_id == 4
    assert remaining[0].instruction == "指令 3"
    assert book.take("B") == []


def test_coach_endpoint_requires_live_and_known_target(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    # 只登记、没有 match.created：还没开赛。
    pending = client.post(
        "/matches",
        json={"game": "tic-tac-toe", "players": [{"id": "A"}, {"id": "B"}], "inlineWorker": False},
    )
    assert pending.status_code == 201
    response = client.post(
        f"/matches/{pending.json()['matchId']}/coach",
        json={"target": "A", "instruction": "先占中心"},
    )
    assert response.status_code == 409
    assert client.post("/matches/missing/coach", json={"target": "A", "instruction": "x"}).status_code == 404


def test_coach_endpoint_rejects_unknown_target(client):
    data = client.post(
        "/matches",
        json={"game": "tic-tac-toe", "players": [{"id": "A", "delayMs": 1100}, {"id": "B", "delayMs": 1100}]},
    )
    assert data.status_code == 201
    # 等 worker 写下 match.created，证明比赛已经开赛。
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        events = client.get(f"/matches/{data.json()['matchId']}/events").json()["events"]
        if any(event["type"] == "match.created" for event in events):
            break
        time.sleep(0.02)
    response = client.post(
        f"/matches/{data.json()['matchId']}/coach",
        json={"target": "Z", "instruction": "换马"},
    )
    assert response.status_code == 400


def test_runner_delivers_coach_instruction_once(tmp_path, monkeypatch):
    """真跑一场：queued 事件、delivered 事件、随后动作的顺序都要成立。"""
    client = make_client(tmp_path, monkeypatch, worker=True)
    response = client.post(
        "/matches",
        json={
            "game": "tic-tac-toe",
            "seed": 7,
            "players": [{"id": "A", "delayMs": 1100}, {"id": "B", "delayMs": 1100}],
        },
    )
    assert response.status_code == 201, response.text
    match_id = response.json()["matchId"]
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        events = client.get(f"/matches/{match_id}/events").json()["events"]
        if any(event["type"] == "match.started" for event in events):
            break
        time.sleep(0.03)
    queued = client.post(
        f"/matches/{match_id}/coach",
        json={"target": "A", "instruction": "优先占中心"},
    )
    assert queued.status_code == 201, queued.text
    command_id = queued.json()["command"]["id"]
    wait_finished(match_id, timeout=35)
    events = read_match(Path(tmp_path) / "match.db", match_id)
    coach_events = [event for event in events if event["type"] == "coach.message"]
    assert any(event["payload"]["commandId"] == command_id and event["payload"]["status"] == "queued" for event in coach_events)
    delivered = [event for event in coach_events if event["payload"]["commandId"] == command_id and event["payload"]["status"] == "delivered"]
    assert delivered
    delivered_seq = delivered[0]["seq"]
    assert any(event["type"] == "turn.started" and event["seq"] > delivered_seq for event in events)
    # 指令只会投递一次；runner 每手 take 不会重复生成同一条 delivered。
    assert len(delivered) == 1


def test_coach_endpoint_rejects_imported_live_snapshot(tmp_path, monkeypatch):
    """导入的实时快照是 live，但事实只读，API 不能绕过前端写教练指令。"""
    client = make_client(tmp_path, monkeypatch, worker=False)
    sample = Path(__file__).resolve().parents[3] / "examples" / "episodes" / "kaggle-chess.json"
    episode = parse_episode(json.loads(sample.read_text(encoding="utf-8")), "kaggle")
    document = build_export(episode, finished=False, live=True)
    response = client.post("/matches/import", json=document)
    assert response.status_code == 200, response.text
    match_id = response.json()["matchId"]
    scene = client.get(f"/matches/{match_id}/scene").json()
    assert scene["status"] == "live" and scene["source"] == "import"
    coach = client.post(
        f"/matches/{match_id}/coach",
        json={"target": episode.players[0], "instruction": "只读局不能改"},
    )
    assert coach.status_code == 409, coach.text
    assert "只读" in coach.json()["detail"]


def test_coach_route_shape_is_declared():
    # 防止只加实现、忘记把路由写进架构冻结清单。
    schema = CoachCommandIn.model_json_schema()["properties"]
    assert schema["instruction"]["maxLength"] == 280
    assert schema["target"]["maxLength"] == 80
