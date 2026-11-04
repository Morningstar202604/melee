

from .fake_openai import FakeOpenAI
from .support import create, make_client, wait_finished

SECRET = "sk-live-must-never-appear"

def test_llm_player_plays_via_api(local_client):
    with FakeOpenAI() as server:
        server.replies = [
            {"content": '{"kind":"ask","value":"category=建筑","thought":"先砍掉一半"}', "reasoning": "建筑类通常更大"},
        ]
        response = local_client.post(
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

    events = local_client.get(f"/matches/{match_id}/events").json()["events"]
    kinds = [e["type"] for e in events]
    assert "thought.delta" in kinds
    assert "match.ended" in kinds

    thoughts = [e["payload"]["text"] for e in events if e["type"] == "thought.delta"]
    assert "建筑类通常更大" in thoughts

    proposed = [e for e in events if e["type"] == "action.proposed" and e["player"] == "A"]
    assert "category=建筑" in proposed[0]["payload"]["raw"]

def test_key_never_appears_in_events_or_view(local_client):
    with FakeOpenAI() as server:
        server.replies = [{"content": '{"kind":"guess","value":"灯塔"}'}]
        response = local_client.post(
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

    events = local_client.get(f"/matches/{match_id}/events").json()["events"]
    views = local_client.get(f"/matches/{match_id}/view").json()
    streamed = ""
    with local_client.stream("GET", f"/matches/{match_id}/stream") as stream:
        streamed = "".join(stream.iter_text())

    for blob in (str(events), str(views), streamed):
        assert SECRET not in blob

def test_upstream_error_body_is_redacted_in_events(local_client):
    with FakeOpenAI() as server:
        server.status = 401
        response = local_client.post(
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

    events = local_client.get(f"/matches/{match_id}/events").json()["events"]
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
