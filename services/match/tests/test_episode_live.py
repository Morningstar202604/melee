"""实时外部对局桥：首帧建场、增量追加、终局收尾、前缀约束。"""

from __future__ import annotations

from services.match.episode import EPISODE_FORMAT

BASE = {
    "format": EPISODE_FORMAT,
    "source": "textarena",
    "episodeId": "live-fixture-1",
    "game": "tic-tac-toe",
    "players": ["A", "B"],
    "winner": "A",
    "steps": [{"player": "A", "action": "0,0"}],
}


def _frame(steps, *, finished=False):
    document = {**BASE, "steps": steps}
    return {"format": "episode", "episode": document, "finished": finished}


def test_live_episode_grows_and_ends(client):
    first = client.post("/matches/import-episode/live", json=_frame(BASE["steps"]))
    assert first.status_code == 200, first.text
    match_id = first.json()["matchId"]
    assert first.json()["live"] is True
    assert first.json()["steps"] == 1

    second = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "1,1"}]),
    )
    assert second.status_code == 200, second.text
    assert second.json()["matchId"] == match_id
    assert second.json()["steps"] == 2

    # 同一帧重复发是幂等的，不追加事件。
    duplicate = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "1,1"}]),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["appended"] == 0

    final = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "1,1"}], finished=True),
    )
    assert final.status_code == 200, final.text
    assert final.json()["finished"] is True
    state = client.get(f"/matches/{match_id}/state").json()
    assert state["finished"] is True
    assert state["winner"] == "A"
    events = client.get(f"/matches/{match_id}/events").json()["events"]
    types = [event["type"] for event in events]
    assert types[-1] == "match.ended"
    assert types.count("match.ended") == 1


def test_live_episode_rejects_rewound_history(client):
    first = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "1,1"}]),
    )
    assert first.status_code == 200
    first.json()["matchId"]
    rewound = client.post(
        "/matches/import-episode/live",
        json=_frame(BASE["steps"]),
    )
    assert rewound.status_code == 409
    assert "rewinds" in rewound.json()["detail"]


def test_live_episode_rejects_rewritten_events(client):
    first = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "1,1"}]),
    )
    assert first.status_code == 200
    rewritten = client.post(
        "/matches/import-episode/live",
        json=_frame([*BASE["steps"], {"player": "B", "action": "2,2"}]),
    )
    assert rewritten.status_code == 409
    assert "rewrote history" in rewritten.json()["detail"]
