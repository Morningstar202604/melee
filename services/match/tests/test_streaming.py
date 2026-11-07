from __future__ import annotations

from services.match.agents.llm import LLMAgent, LLMConfig
from services.match.engine.twenty_questions import TwentyQuestions

from .fake_stream import FakeStream


def observation(seed: int = 7):
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    return game.observe(state, "A")


def make_agent(server: FakeStream, *, chunk: int = 8) -> LLMAgent:
    return LLMAgent(
        "A",
        LLMConfig(
            base_url=server.base_url,
            api_key="sk-test",
            model="fake",
            timeout_s=5.0,
            stream=True,
            thought_chunk_chars=chunk,
        ),
    )


def test_streaming_emits_thought_in_chunks():
    with FakeStream() as server:
        server.reasoning_pieces = ["先缩小", "范围，", "再报答案"]
        server.content_pieces = ['{"kind":', '"guess",', '"value":"灯塔"}']
        agent = make_agent(server, chunk=4)

        thoughts: list[str] = []
        output = agent.act(observation(), [], thoughts.append)

    assert output.move.kind == "guess"
    assert output.move.value == "灯塔"
    assert len(thoughts) >= 2, thoughts
    assert "".join(thoughts) == "先缩小范围，再报答案"


def test_streaming_requests_stream_flag():
    with FakeStream() as server:
        server.reasoning_pieces = ["想一想"]
        server.content_pieces = ['{"kind":"ask","value":"place=海边"}']
        agent = make_agent(server)
        agent.act(observation(), [], lambda _t: None)

    assert server.requests[0]["body"]["stream"] is True


def test_streaming_without_reasoning_still_parses():
    with FakeStream() as server:
        server.content_pieces = ['{"kind":"ask",', '"value":"scale=小型"}']
        agent = make_agent(server)
        thoughts: list[str] = []
        output = agent.act(observation(), [], thoughts.append)

    assert output.move.kind == "ask"
    assert output.move.value == "scale=小型"
    assert thoughts == []


def test_non_streaming_path_still_works():
    with FakeStream() as server:
        server.content = '{"kind":"guess","value":"烟囱"}'
        agent = LLMAgent(
            "A",
            LLMConfig(base_url=server.base_url, api_key="k", model="m", timeout_s=5, stream=False),
        )
        output = agent.act(observation(), [], lambda _t: None)

    assert output.move.value == "烟囱"
    assert server.requests[0]["body"].get("stream") is not True

def test_events_endpoint_honours_limit(client):
    """limit 必须真的少读，而不是查完再切。

    大对局（几千条事件）一次全量返回会把整场塞进内存和网络；
    分页的价值就在"少读"，所以 SQL 层必须带上 LIMIT。
    """
    created = client.post("/matches", json={"game": "tic-tac-toe", "seed": 3,
                                            "players": [{"id": "A"}, {"id": "B"}]}).json()
    match_id = created["matchId"]
    deadline = __import__("time").time() + 60
    while __import__("time").time() < deadline:
        events = client.get(f"/matches/{match_id}/events").json()["events"]
        if any(e["type"] == "match.ended" for e in events):
            break
        __import__("time").sleep(0.5)

    full = client.get(f"/matches/{match_id}/events").json()
    assert len(full["events"]) > 10

    part = client.get(f"/matches/{match_id}/events?limit=5").json()
    assert len(part["events"]) == 5
    assert part["events"] == full["events"][:5]

    # 游标 + 上限组合：从第 8 条起再取 3 条
    tail = client.get(f"/matches/{match_id}/events?after=7&limit=3").json()
    assert [e["seq"] for e in tail["events"]] == [8, 9, 10]


def test_events_limit_is_capped(client):
    """上限要有天花板：传 100000 不该真去读十万条。"""
    created = client.post("/matches", json={"game": "nim", "players": [{"id": "A"}, {"id": "B"}]}).json()
    response = client.get(f"/matches/{created['matchId']}/events?limit=100000")
    assert response.status_code == 422

