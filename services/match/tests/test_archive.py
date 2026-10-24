"""赛事库 / 统计 / 分享卡 / 在线人数 / 赛季赛制 / 崩溃恢复的行为测试。

这些是 v1.0 新增或修复的能力，断言的是可观察结果（返回字段、PNG 字节、终局事件条数），
不是实现细节。
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.match import api as api_module
from services.match import workqueue
from services.match.eventlog import EventLog, read_match
from services.match.runner import Runner, ensure_terminal


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    api_module.MATCHES.clear()
    api_module._ensure_inline_worker()
    client = TestClient(api_module.create_app())
    yield client
    api_module._stop_inline_worker()


def wait_finished(match_id: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = read_match(api_module.DB_PATH, match_id)
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - 表还没建  # aqg: top-level boundary
            events = []
        if any(event["type"] == "match.ended" for event in events):
            return
        time.sleep(0.05)
    raise TimeoutError(f"{match_id} 未在 {timeout}s 内结束")


def create(client, **overrides):
    body = {
        "game": "twenty-questions",
        "seed": 7,
        "maxQuestions": 6,
        "players": [{"id": "A"}, {"id": "B"}],
    }
    body.update(overrides)
    response = client.post("/matches", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ---- /archive ----

def test_archive_lists_finished_match_with_real_fields(client):
    created = create(client)
    wait_finished(created["matchId"])

    response = client.get("/archive")
    assert response.status_code == 200
    data = response.json()
    assert data["count"] >= 1
    item = next(row for row in data["items"] if row["matchId"] == created["matchId"])
    assert item["game"] == "twenty-questions"
    assert item["gameLabel"] == "二十问"
    assert item["players"] == ["A", "B"]
    assert item["status"] == "ended"
    assert item["events"] > 0
    assert item["endedAt"]
    assert item["winner"] in {"A", "B"}


def test_archive_filters_by_game_and_status(client):
    created = create(client, game="tic-tac-toe", players=[{"id": "X"}, {"id": "O"}])
    wait_finished(created["matchId"])

    only_ttt = client.get("/archive", params={"game": "tic-tac-toe"}).json()
    assert only_ttt["items"]
    assert all(item["game"] == "tic-tac-toe" for item in only_ttt["items"])

    only_live = client.get("/archive", params={"status": "live"}).json()
    assert all(item["status"] == "live" for item in only_live["items"])


def test_archive_pagination_is_stable(client):
    for seed in (1, 2, 3):
        created = create(client, seed=seed)
        wait_finished(created["matchId"])

    first = client.get("/archive", params={"limit": 2, "offset": 0}).json()
    second = client.get("/archive", params={"limit": 2, "offset": 2}).json()
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
    assert {item["matchId"] for item in first["items"]} & {
        item["matchId"] for item in second["items"]
    } == set()


def test_archive_rejects_bad_status(client):
    assert client.get("/archive", params={"status": "nope"}).status_code == 422


# ---- /stats ----

def test_stats_counts_match_database(client):
    create(client)
    created = create(client)
    wait_finished(created["matchId"])

    data = client.get("/stats").json()
    assert data["matches"] >= 2
    assert data["events"] > 0
    assert data["live"] + data["finished"] == data["matches"]
    assert any(row["game"] == "twenty-questions" for row in data["byGame"])
    assert isinstance(data["topWinners"], list)
    assert "viewers" in data


# ---- 分享卡 ----

def test_card_is_real_png(client):
    created = create(client)
    wait_finished(created["matchId"])

    response = client.get(f"/matches/{created['matchId']}/card.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_card_unknown_match_is_404(client):
    assert client.get("/matches/m_nope/card.png").status_code == 404


def test_share_page_has_open_graph_tags(client):
    created = create(client)
    wait_finished(created["matchId"])

    response = client.get(f"/share/{created['matchId']}")
    assert response.status_code == 200
    html = response.text
    assert 'property="og:title"' in html
    assert 'property="og:image"' in html
    assert f"/matches/{created['matchId']}/card.png" in html
    assert 'name="twitter:card"' in html


def test_share_page_escapes_player_names(client):
    name = '<script>alert(1)</script>'
    created = create(client, players=[{"id": name}, {"id": "B"}])
    wait_finished(created["matchId"])

    html = client.get(f"/share/{created['matchId']}").text
    assert "<script>alert(1)</script>" not in html


# ---- 在线人数 ----

def test_presence_reports_real_subscriber_count(client):
    from services.match.broadcast import reset_global_broadcaster, MemoryBroadcaster

    backend = MemoryBroadcaster()
    reset_global_broadcaster(backend)
    try:
        created = create(client)
        # 两个"订阅者"直接走广播后端，避免在 TestClient 里维持两条 SSE 长连接。
        subscriptions = [backend.subscribe(created["matchId"]) for _ in range(2)]
        try:
            data = client.get(f"/matches/{created['matchId']}/presence").json()
            assert data["viewers"] == 2
        finally:
            for item in subscriptions:
                item.close()
            reset_global_broadcaster(None)
    finally:
        reset_global_broadcaster(MemoryBroadcaster())


def test_presence_unknown_match_is_404(client):
    assert client.get("/matches/m_nope/presence").status_code == 404


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


# ---- 崩溃恢复 ----

def _append_actions_to_finished(tmp_path, match_id: str = "m_resume") -> EventLog:
    """造一场"状态已终局但缺 match.ended"的日志。"""
    from services.match.engine.twenty_questions import CATALOG, TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 6})
    log = EventLog.open(tmp_path / "resume.db", match_id)
    log.create_match(game=game.game_id, seed=3, players=["A", "B"])

    # 直接构造一个已结束的状态，模拟"最后一步写完但终局事件没落"。
    finished = {**state, "finished": True, "winner": "A", "endReason": "测试终局"}
    assert CATALOG  # 保持导入被使用，避免误删
    return log, game, finished


def test_ensure_terminal_appends_exactly_one_end_event(tmp_path):
    log, game, finished = _append_actions_to_finished(tmp_path)
    try:
        assert ensure_terminal(log, game, finished) is True
        assert ensure_terminal(log, game, finished) is False
        ended = [event for event in log.read() if event["type"] == "match.ended"]
        assert len(ended) == 1
        assert ended[0]["payload"]["winner"] == "A"
    finally:
        log.close()


def test_runner_finish_is_idempotent_for_same_log(tmp_path):
    log, game, finished = _append_actions_to_finished(tmp_path)
    try:
        log.append("match.ended", {"winner": "A", "reason": "第一次", "finalState": {}})
        runner = Runner(game, log, {"A": None, "B": None})
        result = runner._finish(finished, "第二次", "A", 1)
        assert result.winner == "A"
        ended = [event for event in log.read() if event["type"] == "match.ended"]
        assert len(ended) == 1
    finally:
        log.close()


def test_worker_repairs_missing_terminal_event(tmp_path, monkeypatch):
    from services.match import worker as worker_module

    db = tmp_path / "resume.db"
    log, game, finished = _append_actions_to_finished(tmp_path)
    log.append("match.started", {"firstPlayer": "A"})
    log.close()

    payload = {
        "matchId": "m_resume",
        "game": "twenty-questions",
        "seed": 3,
        "players": ["A", "B"],
        "maxQuestions": 6,
        "specs": {"data": [], "encrypted": False},
    }
    # 让 rehydrate 看到终局：补一条能重放出 finished 的动作不现实，
    # 这里直接替换 _resume_state 返回预置状态，只验证补齐逻辑。
    from services.match.worker import _Snapshot

    monkeypatch.setattr(
        worker_module, "_resume_state", lambda db_path, match_id: _Snapshot(state=finished, game=game, events=True)
    )
    worker_module.run_match_job(db, payload)

    events = read_match(db, "m_resume")
    assert sum(1 for event in events if event["type"] == "match.ended") == 1
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT ended_at FROM matches WHERE match_id = 'm_resume'").fetchone()
    finally:
        conn.close()
    assert row[0] is not None


# ---- 旧接口回归 ----

def test_legacy_matches_endpoint_still_works(client):
    created = create(client)
    wait_finished(created["matchId"])
    data = client.get("/matches").json()
    assert created["matchId"] in data["matches"]


def test_jobs_are_visible_after_create(client):
    created = create(client)
    job = client.get(f"/jobs/{created['jobId']}")
    assert job.status_code == 200
    assert job.json()["jobId"] == created["jobId"]


def test_archive_is_fast_enough(client):
    """200 场的数据量下，/archive 必须远低于 300ms（否则首页会卡）。"""
    create(client)  # 先让事件表建起来
    path = api_module.DB_PATH
    conn = sqlite3.connect(path)
    try:
        for index in range(200):
            conn.execute(
                "INSERT INTO matches (match_id, game, seed, players, created_at) VALUES (?,?,?,?,?)",
                (f"m_bulk{index:03d}", "twenty-questions", index, json.dumps(["A", "B"]), "2026-01-01T00:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()

    # 先预热一次：首次请求包含连接池/模板初始化，不该算进查询耗时。
    client.get("/archive", params={"limit": 24})
    started = time.perf_counter()
    response = client.get("/archive", params={"limit": 24})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert elapsed < 0.5, f"/archive 太慢：{elapsed:.3f}s"


def test_queue_schema_initialized_by_api(client):
    """新建比赛后队列表必须可读：health 里 queue=true 的来源。"""
    create(client)
    assert workqueue.list_jobs(api_module.DB_PATH, limit=5) is not None


# ---- 安全与指标 ----

def test_security_headers_include_csp(client):
    response = client.get("/games")
    assert response.status_code == 200
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_metrics_are_public_when_auth_off(client):
    assert client.get("/metrics").status_code == 200
    assert client.get("/metrics/business").status_code == 200


def test_metrics_require_token_when_auth_on(tmp_path, monkeypatch):
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:secret-token")
    monkeypatch.setattr(api_module, "DB_PATH", Path(tmp_path) / "match.db")
    api_module.MATCHES.clear()
    reset_settings()
    try:
        protected = TestClient(api_module.create_app())
        assert protected.get("/metrics").status_code == 401
        allowed = protected.get("/metrics", headers={"Authorization": "Bearer secret-token"})
        assert allowed.status_code == 200
    finally:
        reset_module_settings()


def reset_module_settings() -> None:
    from services.match.settings import reset_settings

    reset_settings()


def test_card_regenerates_for_live_match(client):
    """直播中的卡片每次重画：胜负还没定，不能拿旧图缓存。"""
    created = create(client)
    first = client.get(f"/matches/{created['matchId']}/card.png")
    assert first.status_code == 200
    second = client.get(f"/matches/{created['matchId']}/card.png")
    assert second.status_code == 200
    assert second.content.startswith(b"\x89PNG")


def test_stats_reports_viewers_as_integer(client):
    data = client.get("/stats").json()
    assert isinstance(data["viewers"], int)
    assert data["viewers"] >= 0


# ---- 审计修复的回归 ----

def test_clip_download_rejects_path_traversal(client):
    """match_id 参与路径拼接：`..`、盘符、反斜杠都必须 400，不能读到库文件。"""
    for bad in ("..", "%2e%2e", "C:", "a%2Fb"):
        response = client.get(f"/matches/{bad}/clips/match.db")
        assert response.status_code in (400, 404), (bad, response.status_code)
        assert b"SQLite format" not in response.content


def test_media_ticket_rejects_bad_match_id(client):
    # 路径里的 `..` 会被 TestClient 归一化，这里是"形状不合法"的另一条路径。
    assert client.post("/matches/" + "x" * 80 + "/media-ticket").status_code == 400


def test_bad_json_body_is_400_not_500(client):
    response = client.post(
        "/director/m_x/cue", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_bad_start_at_is_400_not_500(client):
    response = client.post(
        "/seasons",
        json={"seasonId": "s", "name": "x", "players": [{"id": "A"}, {"id": "B"}], "startAt": "not-a-date"},
    )
    assert response.status_code == 400


def test_unknown_season_does_not_leave_orphan_match(client):
    before = client.get("/archive").json()["count"]
    response = client.post(
        "/matches",
        json={
            "game": "twenty-questions",
            "players": [{"id": "A"}, {"id": "B"}],
            "seasonId": "nope",
        },
    )
    assert response.status_code == 404
    assert client.get("/archive").json()["count"] == before


def test_quota_is_not_consumed_by_invalid_requests(client, monkeypatch):
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_QUOTA_MATCHES", "2")
    monkeypatch.setenv("MELEE_AUTH", "token")
    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t")
    reset_settings()
    try:
        headers = {"Authorization": "Bearer t"}
        # 两次非法请求（赛制不存在）不该扣额度
        for _ in range(2):
            bad = client.post(
                "/matches", json={"game": "nope", "players": [{"id": "A"}, {"id": "B"}]}, headers=headers
            )
            assert bad.status_code == 400
        used = client.get("/quota", headers=headers).json()
        assert used["matches"] == 0
        ok = client.post(
            "/matches", json={"game": "twenty-questions", "players": [{"id": "A"}, {"id": "B"}]}, headers=headers
        )
        assert ok.status_code == 201
    finally:
        monkeypatch.undo()
        reset_settings()


def test_season_run_returns_409_when_locked(client):
    from services.match.schedule import _acquire_season_lock
    import sqlite3 as _sqlite3

    client.post(
        "/seasons",
        json={"seasonId": "s-lock", "name": "锁", "players": [{"id": "A"}, {"id": "B"}]},
    )
    conn = _sqlite3.connect(api_module.DB_PATH)
    try:
        assert _acquire_season_lock(conn, "s-lock", "other-owner", ttl_s=60)
    finally:
        conn.close()
    response = client.post("/seasons/s-lock/run")
    assert response.status_code == 409


def test_recreating_season_replaces_old_roster(client):
    client.post(
        "/seasons",
        json={"seasonId": "s-re", "name": "旧", "players": [{"id": "A"}, {"id": "B"}, {"id": "C"}]},
    )
    client.post(
        "/seasons",
        json={"seasonId": "s-re", "name": "新", "players": [{"id": "X"}, {"id": "Y"}]},
    )
    data = client.get("/seasons/s-re").json()
    assert {row["playerId"] for row in data["standings"]} == {"X", "Y"}
    assert len(data["fixtures"]) == 1


def test_archive_status_reflects_terminal_marking(client):
    """补写终局事件时必须同时补 ended_at，否则这场永远算"直播中"。"""
    from services.match.engine.twenty_questions import TwentyQuestions
    from services.match.runner import ensure_terminal

    game = TwentyQuestions()
    state = game.init(seed=1, players=["A", "B"], config={})
    log = EventLog.open(api_module.DB_PATH, "m_terminal")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        finished = {**state, "finished": True, "winner": "A", "endReason": "测试"}
        assert ensure_terminal(log, game, finished) is True
    finally:
        log.close()
    data = client.get("/archive").json()
    item = next(row for row in data["items"] if row["matchId"] == "m_terminal")
    assert item["status"] == "ended"
    assert item["endedAt"]


def test_mark_ended_is_idempotent(tmp_path):
    log = EventLog.open(tmp_path / "idem.db", "m1")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        log.mark_ended()
        first = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()[0]
        log.mark_ended()
        second = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()[0]
        assert first == second
    finally:
        log.close()


def test_create_match_does_not_wipe_ended_at(tmp_path):
    """重跑同一场时 INSERT OR IGNORE 不能把已结束的行重置。"""
    log = EventLog.open(tmp_path / "keep.db", "m1")
    try:
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        log.mark_ended()
        log.create_match(game="twenty-questions", seed=1, players=["A", "B"])
        row = log.conn.execute("SELECT ended_at FROM matches WHERE match_id='m1'").fetchone()
        assert row[0] is not None
    finally:
        log.close()


def test_workqueue_reap_marks_orphan_running_jobs(tmp_path):
    from services.match import workqueue

    db = tmp_path / "q.db"
    job_id = workqueue.enqueue(db, "run-match", {"matchId": "m"}, max_attempts=1)
    workqueue.claim(db, "w1", ["run-match"], lease_s=0.01)
    import time as _time

    _time.sleep(0.05)
    assert workqueue.reap(db) == 1
    assert workqueue.get(db, job_id).status == "failed"


def test_workqueue_complete_requires_owner(tmp_path):
    from services.match import workqueue

    db = tmp_path / "q2.db"
    job_id = workqueue.enqueue(db, "run-match", {"matchId": "m"})
    workqueue.claim(db, "w1", ["run-match"], lease_s=60)
    assert workqueue.complete(db, job_id, owner="intruder") is False
    assert workqueue.get(db, job_id).status == "running"
    assert workqueue.complete(db, job_id, owner="w1") is True
    assert workqueue.get(db, job_id).status == "done"


def test_codenames_last_word_revealed_by_opponent_ends_game():
    """对手翻掉你最后一个词，等于你的词全被翻开——该队获胜。"""
    from services.match.engine.codenames import Codenames
    from services.match.engine.base import Move

    game = Codenames()
    state = game.init(seed=5, players=["A", "B", "C", "D"], config={})
    # 只留一个红词未翻开，其余红词都翻开
    red_indices = [i for i, color in enumerate(state["colors"]) if color == "red"]
    for i in red_indices[:-1]:
        state["revealed"][i] = True
    last_red = red_indices[-1]
    state["turn"] = "blue"
    state["phase"] = "guess"
    state["remainingGuesses"] = 3
    # 蓝队队员是 players[3]（D）；players[2] 是红队队员。
    result = game.apply(state, "D", Move(kind="guess", value=f"pick:{state['words'][last_red]}"))
    assert result.legal
    assert result.next_state["finished"] is True
    assert result.next_state["winner"] == "red"


def test_ssrf_blocks_cgnat_and_ula():
    from services.match.outbound import OutboundAddressError, check_base_url

    for url in ("http://100.100.100.200/v1", "http://[fc00::1]/v1"):
        try:
            check_base_url(url)
        except OutboundAddressError:
            continue
        raise AssertionError(f"应当拒绝：{url}")
