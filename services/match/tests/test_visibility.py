"""私有事件：隐藏信息赛制的选手思考不能播给观众。

`visibility` 字段在契约里存在很久但没有实际用例。这里锁住三件事：
1. 队长思考写库时带 `visibility: {players:[...]}`，队员思考是公开的；
2. `/events` 与 `/recap` 对观众过滤私有事件，事件序号仍有空洞（不能重排）；
3. 旧库（没有 visibility 列）升级后能直接写，不会 no such column。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from services.match import api as api_module
from services.match.engine.codenames import Codenames
from services.match.eventlog import EventLog, read_match
from services.match.routes.common import visible
from services.match.runner import Runner

from .support import make_client, wait_finished


def _codenames_players() -> list[str]:
    return ["红队队长", "蓝队队长", "红队队员", "蓝队队员"]


def test_captain_thoughts_are_private_and_teammates_are_public(tmp_path):
    players = _codenames_players()
    game = Codenames()
    state = game.init(seed=5, players=players, config={})
    log = EventLog.open(tmp_path / "m.db", "m_private")
    try:
        log.create_match(game="codenames", seed=5, players=players)
        from services.match.agents.scripted import CodenamesAgent
        from services.match.director import Director
        from services.match.narrator import RuleNarrator

        Runner(
            game,
            log,
            {p: CodenamesAgent(p) for p in players},
            narrator=RuleNarrator(),
            director=Director(),
        ).play(state)
        events = log.read()
    finally:
        log.close()

    thoughts = [e for e in events if e["type"] == "thought.delta"]
    assert thoughts, "代号应该产出思考片段"
    for event in thoughts:
        is_captain = event["player"] in {players[0], players[1]}
        if is_captain:
            assert event.get("visibility") == {"players": [event["player"]]}, event
        else:
            assert "visibility" not in event, event

    # 观众视角：私有事件一条都看不到
    assert all(visible(event, None) for event in thoughts if "visibility" not in event)
    assert not any(visible(event, None) for event in thoughts if event.get("visibility"))

    # 事件序号仍然是连续的（过滤发生在读取之后，不是删除）
    assert [e["seq"] for e in events] == list(range(len(events)))


def test_events_endpoint_hides_private_thoughts(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        response = client.post(
            "/matches",
            json={"game": "codenames", "seed": 3, "players": [{"id": p} for p in _codenames_players()]},
        )
        assert response.status_code == 201, response.text
        match_id = response.json()["matchId"]
        wait_finished(match_id, timeout=120)

        stored = read_match(api_module.DB_PATH, match_id)
        private_seqs = {e["seq"] for e in stored if e.get("visibility")}
        assert private_seqs, "这场代号应该有私有思考"

        payload = client.get(f"/matches/{match_id}/events").json()["events"]
        returned = {e["seq"] for e in payload}
        assert not (private_seqs & returned), "私有事件不能出现在观众接口里"
        assert returned, "公开事件必须仍然返回"
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_recap_ignores_private_events(tmp_path, monkeypatch):
    """战报只能统计公开事件，否则观众能从计数反推出私有事件数量。"""
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 9, "players": [{"id": p} for p in _codenames_players()]},
        )
        match_id = created.json()["matchId"]
        wait_finished(match_id, timeout=120)
        recap = client.get(f"/matches/{match_id}/recap").json()
        assert recap["problems"] == [], recap["problems"]
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_old_database_without_visibility_column_upgrades(tmp_path):
    """旧库升级：events 表没有 visibility 列时自动补列，写入不报错。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE matches (
          match_id TEXT PRIMARY KEY, game TEXT NOT NULL, seed INTEGER NOT NULL,
          players TEXT NOT NULL, created_at TEXT NOT NULL, ended_at TEXT
        );
        CREATE TABLE events (
          match_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
          player TEXT, at TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (match_id, seq)
        );
        """
    )
    conn.commit()
    conn.close()

    log = EventLog.open(db, "m_legacy")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        event = log.append("thought.delta", {"text": "hi"}, player="A", visibility={"players": ["A"]})
        assert event["visibility"] == {"players": ["A"]}
    finally:
        log.close()

    reopened = EventLog.open(db, "m_legacy")
    try:
        assert reopened.read()[0]["visibility"] == {"players": ["A"]}
    finally:
        reopened.close()


# ---- 赛后揭秘：visibility 契约的第二个真实用例 ----
# 直播期隐藏队长视角的思考是硬约束；比赛结束后观众应当能看到"当时他在想什么"，
# 这是转播产品的天然需求（体育转播的赛后揭秘），也是竞品都没有的能力。


def test_reveal_is_forbidden_while_match_is_live(tmp_path, monkeypatch):
    """直播中不能揭秘：否则隐藏信息赛制的核心看点当场失效。"""
    from services.match.engine import registry

    # 支持多队编队后人数区间扩到 4–16，但仍必须包含经典 4 人。
    low, high = registry.player_range("codenames")
    assert low == 4 and high >= 4
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 11, "players": [{"id": p} for p in _codenames_players()]},
        )
        match_id = created.json()["matchId"]
        # 不等比赛结束，立刻请求揭秘
        response = client.get(f"/matches/{match_id}/events?reveal=1")
        assert response.status_code == 403, response.text
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_reveal_returns_private_events_after_match_ends(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 13, "players": [{"id": p} for p in _codenames_players()]},
        )
        match_id = created.json()["matchId"]
        wait_finished(match_id, timeout=120)

        public_events = client.get(f"/matches/{match_id}/events").json()["events"]
        revealed = client.get(f"/matches/{match_id}/events?reveal=1").json()["events"]
        assert len(revealed) > len(public_events), "揭秘必须多出私有事件"
        private_seqs = {e["seq"] for e in revealed if e.get("visibility")}
        assert private_seqs, "揭秘流里必须带上私有事件"
        assert not (private_seqs & {e["seq"] for e in public_events})
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_view_as_player_returns_only_their_events(tmp_path, monkeypatch):
    """选手视角：只含公共事件 + 他自己的私有事件。"""
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        players = _codenames_players()
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 17, "players": [{"id": p} for p in players]},
        )
        match_id = created.json()["matchId"]
        wait_finished(match_id, timeout=120)

        captain = players[0]
        data = client.get(f"/matches/{match_id}/view/as/{captain}").json()
        assert data["player"] == captain
        assert data["events"], "视角里必须有事件"
        assert data["view"]["turns"], "视角也能折叠出动作序列"
        seqs = {e["seq"] for e in data["events"]}
        stored = read_match(api_module.DB_PATH, match_id)
        # 别的选手的私有事件不在里面
        for event in stored:
            if event.get("visibility") and captain not in event["visibility"].get("players", []):
                assert event["seq"] not in seqs
        # 自己的私有事件在里面
        own = [e for e in stored if e.get("visibility") == {"players": [captain]}]
        assert own, "队长应该有私有思考"
        assert {e["seq"] for e in own} <= seqs
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_view_as_unknown_player_is_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 19, "players": [{"id": p} for p in _codenames_players()]},
        )
        match_id = created.json()["matchId"]
        wait_finished(match_id, timeout=120)
        response = client.get(f"/matches/{match_id}/view/as/不存在的人")
        assert response.status_code == 404
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_stream_never_emits_private_thoughts(tmp_path, monkeypatch):
    """直播 SSE 也不能把私有思考推给观众。

    /events 有过滤，SSE 是另一条独立输出路径——两条都堵上才算真的不泄露。
    """
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        players = _codenames_players()
        created = client.post(
            "/matches",
            json={"game": "codenames", "seed": 23, "players": [{"id": p} for p in players]},
        )
        match_id = created.json()["matchId"]
        collected: list[dict] = []
        with client.stream("GET", f"/matches/{match_id}/stream") as stream:
            assert stream.status_code == 200
            for line in stream.iter_lines():
                if not line.startswith("data: "):
                    continue
                import json as _json

                collected.append(_json.loads(line[6:]))
                if collected[-1].get("type") == "match.ended":
                    break
        assert collected, "至少要收到事件"
        private = [e for e in collected if e.get("visibility")]
        assert private == [], f"SSE 泄露了 {len(private)} 条私有事件"
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()


def test_stream_handles_already_finished_match(tmp_path, monkeypatch):
    """已结束的比赛：SSE 回放完整事件后立即关闭，不能挂住或无限重连。"""
    client = make_client(tmp_path, monkeypatch, worker=True)
    try:
        created = client.post(
            "/matches",
            json={"game": "tic-tac-toe", "seed": 3, "players": [{"id": "A"}, {"id": "B"}]},
        )
        match_id = created.json()["matchId"]
        wait_finished(match_id, timeout=120)
        seen: list[str] = []
        with client.stream("GET", f"/matches/{match_id}/stream") as stream:
            for line in stream.iter_lines():
                if line.startswith("data: "):
                    import json as _json

                    seen.append(_json.loads(line[6:]).get("type"))
                if seen and seen[-1] == "match.ended":
                    break
        assert "match.ended" in seen
    finally:
        from services.match.worker import stop_inline_worker

        stop_inline_worker()
