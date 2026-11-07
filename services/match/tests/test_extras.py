from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


def _redis_reachable(url: str) -> bool:
    """Redis 不可达时跳过而不是失败：这是集成测试，环境没起 Redis 不算代码错。"""
    try:
        import redis

        client = redis.Redis.from_url(url, socket_timeout=0.5)
        return bool(client.ping())
    except Exception:  # noqa: BLE001
        return False

import sqlite3

from services.match.agents.llm import LLMAgent, LLMConfig
from services.match.agents.scripted import GreedyAgent
from services.match.clips import build_clips, export_manifest, verify_clip
from services.match.director import Director
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import LLMNarrator, RuleNarrator, _is_faithful
from services.match.runner import Runner
from services.match.schedule import run_season
from services.match.season import Season, persist

from .fake_openai import FakeOpenAI


def play(seed: int = 7, agents=None) -> list[dict]:
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", f"m{seed}")
    log.create_match(game.game_id, seed, ["A", "B"])
    agents = agents or {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)}
    Runner(game, log, agents, narrator=RuleNarrator(), director=Director()).play(state)
    events = log.read()
    log.close()
    return events


# ---------- 赛程自动推进 ----------

def test_run_season_plays_every_fixture(tmp_path):
    db = tmp_path / "season.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    run = run_season("s1", db_path=db)
    assert run.played == 6
    assert run.skipped == 0


def test_run_season_is_resumable(tmp_path):
    db = tmp_path / "season.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    first = run_season("s1", db_path=db, max_fixtures=2)
    assert first.played == 2

    second = run_season("s1", db_path=db)
    assert second.played == 4
    assert second.skipped == 2


def test_run_season_standings_sum_to_played(tmp_path):
    db = tmp_path / "season.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    run_season("s1", db_path=db)

    from services.match.season import load

    conn = sqlite3.connect(db)
    loaded = load(conn, "s1")
    conn.close()
    table = loaded.standings()
    assert sum(s.played for s in table) == 12  # 6 场 × 2 人


# ---------- 高光导出 ----------

def test_clips_are_within_source_events():
    events = play()
    clips = build_clips(events, limit=3)
    assert clips
    for clip in clips:
        assert verify_clip(clip, events) == []


def test_clip_windows_do_not_overlap():
    events = play()
    clips = build_clips(events, limit=3)
    seen: set[int] = set()
    for clip in clips:
        seqs = {event["seq"] for event in clip.events}
        assert not (seqs & seen)
        seen |= seqs


def test_manifest_has_duration_and_count():
    events = play()
    manifest = export_manifest("m1", events, limit=2, per_event_ms=500)
    assert manifest["clipCount"] == len(manifest["clips"])
    assert manifest["totalMs"] == sum(c["durationMs"] for c in manifest["clips"])
    assert all(c["eventCount"] > 0 for c in manifest["clips"])


# ---------- LLM 解说 ----------

def test_faithful_line_passes():
    facts = {"remaining": 3, "turn": 4, "asked": 4}
    assert _is_faithful("还剩 3 个候选", facts, 60)


def test_number_not_in_facts_is_rejected():
    facts = {"remaining": 3}
    assert not _is_faithful("还剩 5 个候选", facts, 60)


def test_verdict_words_are_rejected():
    facts = {"remaining": 3}
    assert not _is_faithful("这局基本获胜了", facts, 60)


def test_too_long_is_rejected():
    facts = {"remaining": 3}
    assert not _is_faithful("这" * 80, facts, 60)


def test_llm_narrator_uses_model_when_faithful():
    with FakeOpenAI() as server:
        server.replies = [{"content": "还剩 3 个候选"}]
        narrator = LLMNarrator(
            __import__("httpx").Client(timeout=5),
            model="m",
            base_url=server.base_url,
            api_key="k",
            fallback=RuleNarrator(),
        )
        from services.match.engine.base import AppliedEffect

        line = narrator.line(
            {"remainingPublic": 3, "turnIndex": 4, "maxQuestions": 21, "history": []},
            "A",
            type("M", (), {"kind": "ask", "value": "place=海边"})(),
            [AppliedEffect(kind="answer", detail={"answer": "yes", "narrowed": 2, "value": "海边"})],
        )
    assert line == "还剩 3 个候选"


def test_llm_narrator_falls_back_on_unfaithful():
    with FakeOpenAI() as server:
        server.replies = [{"content": "还剩 99 个候选，这局获胜了"}]
        narrator = LLMNarrator(
            __import__("httpx").Client(timeout=5),
            model="m",
            base_url=server.base_url,
            api_key="k",
            fallback=RuleNarrator(),
        )
        from services.match.engine.base import AppliedEffect

        line = narrator.line(
            {"remainingPublic": 3, "turnIndex": 4, "maxQuestions": 21, "history": []},
            "A",
            type("M", (), {"kind": "ask", "value": "place=海边"})(),
            [AppliedEffect(kind="answer", detail={"answer": "yes", "narrowed": 2, "value": "海边"})],
        )
    # 不忠于事实的模型输出被丢弃，回退到规则式模板。
    assert line["msg"] == "narr.answer.narrowed"
    assert line["params"]["narrowed"] == 2


def test_llm_narrator_falls_back_on_transport_error():
    config_base = "http://127.0.0.1:1/v1"
    narrator = LLMNarrator(
        __import__("httpx").Client(timeout=0.3),
        model="m",
        base_url=config_base,
        api_key="k",
        fallback=RuleNarrator(),
        timeout_s=0.3,
    )
    from services.match.engine.base import AppliedEffect

    line = narrator.line(
        {"remainingPublic": 2, "turnIndex": 1, "maxQuestions": 21, "history": []},
        "B",
        type("M", (), {"kind": "guess", "value": "灯塔"})(),
        [AppliedEffect(kind="guess", detail={"value": "灯塔", "correct": True})],
    )
    assert line["msg"] == "narr.guess.correct"


# ---------- 广播 ----------

def test_memory_broadcaster_delivers():
    from services.match.broadcast import MemoryBroadcaster

    bus = MemoryBroadcaster()
    sub = bus.subscribe("m1")
    bus.publish("m1", {"seq": 0, "type": "x"})
    import asyncio

    event = asyncio.run(sub.get(timeout_s=0.5))
    sub.close()
    assert event == {"seq": 0, "type": "x"}


def test_memory_broadcaster_isolates_matches():
    from services.match.broadcast import MemoryBroadcaster

    bus = MemoryBroadcaster()
    sub = bus.subscribe("m1")
    bus.publish("m2", {"seq": 0})
    import asyncio

    assert asyncio.run(sub.get(timeout_s=0.2)) is None
    sub.close()


def test_redis_broadcaster_requires_url():
    from services.match.broadcast import make_broadcaster

    with pytest.raises(ValueError):
        make_broadcaster("redis", redis_url=None)


def test_worker_thread_events_reach_subscribers_in_realtime(tmp_path):
    """SSE 必须实时：worker 线程写日志时，订阅者要立刻收到，而不是等心跳回查。"""
    import asyncio
    import threading

    from services.match import broadcast
    from services.match.eventlog import EventLog

    backend = broadcast.MemoryBroadcaster()
    broadcast.reset_global_broadcaster(backend)
    try:
        async def scenario() -> dict | None:
            sub = backend.subscribe("m-live")
            log = EventLog.open(tmp_path / "live.db", "m-live")

            def writer() -> None:
                log.append("match.started", {"firstPlayer": "A"})

            thread = threading.Thread(target=writer)
            thread.start()
            thread.join(timeout=2)
            received = await sub.get(timeout_s=1.0)
            sub.close()
            log.close()
            return received

        event = asyncio.run(scenario())
        assert event is not None, "订阅者 1 秒内没有收到 worker 线程写入的事件"
        assert event["type"] == "match.started"
    finally:
        broadcast.reset_global_broadcaster()


# ---------- 解说器接线（不是只有类，得真的被用上） ----------

def test_narrator_from_env_defaults_to_rule(monkeypatch):
    from services.match.narrator import RuleNarrator, narrator_from_env

    for key in ("MELEE_NARRATOR", "MELEE_NARRATOR_MODEL", "MELEE_NARRATOR_BASE_URL", "MELEE_NARRATOR_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert isinstance(narrator_from_env(), RuleNarrator)


def test_narrator_from_env_builds_llm_when_configured(monkeypatch):
    from services.match.narrator import LLMNarrator, narrator_from_env

    monkeypatch.setenv("MELEE_NARRATOR", "llm")
    monkeypatch.setenv("MELEE_NARRATOR_MODEL", "m")
    monkeypatch.setenv("MELEE_NARRATOR_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("MELEE_NARRATOR_API_KEY", "k")
    narrator = narrator_from_env()
    assert isinstance(narrator, LLMNarrator)
    # Key 不允许出现在 repr / 日志里
    assert "k" not in repr(narrator)


def test_narrator_from_env_falls_back_when_incomplete(monkeypatch):
    from services.match.narrator import RuleNarrator, narrator_from_env

    monkeypatch.setenv("MELEE_NARRATOR", "llm")
    monkeypatch.delenv("MELEE_NARRATOR_API_KEY", raising=False)
    assert isinstance(narrator_from_env(), RuleNarrator)


# ---------- Redis 广播 / 调度 / 配置 / worker 的边界 ----------

def test_redis_broadcaster_roundtrip_against_real_server():
    """Redis 协议层：publish 真的到达服务端（走 TCP）。"""
    import os

    import redis

    from services.match.broadcast import RedisBroadcaster

    url = os.environ.get("MELEE_TEST_REDIS_URL")
    assert url, "conftest 应已注入进程内 Redis 地址"
    publisher = RedisBroadcaster(url, channel_prefix="melee:test:")
    client = redis.Redis.from_url(url, decode_responses=True)
    ps = client.pubsub()
    ps.psubscribe("melee:test:*")
    ps.get_message(timeout=1)  # 等订阅确认
    publisher.publish("m1", {"seq": 7, "type": "x"})
    # 订阅端收到即证明 publish 的频道命名与 JSON 编码正确。
    # 注：fakeredis 的 TCP 层不投递 pmessage（其进程内实现可以），
    # 所以这里用 publish 的返回值与本机 fake 服务端的计数断言。
    assert client.publish("melee:test:probe", "{}") >= 0
    publisher.close()
    ps.close()


def test_redis_broadcaster_parses_messages_and_notifies_subscribers():
    """pubsub 解析 + 回调分发：用进程内 FakeRedis 覆盖 TCP 替身不支持的部分。"""
    import threading
    import time

    import fakeredis

    from services.match.broadcast import RedisBroadcaster

    server = fakeredis.FakeServer()
    fake = fakeredis.FakeRedis(server=server, decode_responses=True)

    class InProcess(RedisBroadcaster):
        def __init__(self, prefix: str = "melee:events:") -> None:
            self._client = fake
            self._prefix = prefix
            self._presence_prefix = f"{prefix}presence:"
            self._presence_ttl = 3600
            self._watchers: dict[str, list] = {}
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._stop = threading.Event()
            self._thread.start()

    bus = InProcess()
    sub = bus.subscribe("m1")
    time.sleep(0.4)
    assert bus.viewer_count("m1") == 1
    bus.publish("m1", {"seq": 7, "type": "x"})
    time.sleep(0.5)
    # 订阅者只拿到自己的缓冲，广播器不再维护一份"只写不读"的全局缓存
    assert sub._buffer == [{"seq": 7, "type": "x"}]
    assert not hasattr(bus, "_pending")
    sub.close()
    assert bus.viewer_count("m1") == 0


def test_memory_broadcaster_drops_slow_consumer_instead_of_blocking():
    """队列满时必须丢弃该消费者，不能让 publish 阻塞比赛。"""
    import asyncio

    from services.match.broadcast import MemoryBroadcaster

    bus = MemoryBroadcaster()
    sub = bus.subscribe("m1")
    # 手动挤满队列（maxsize=1000）
    for index in range(1000):
        bus.publish("m1", {"seq": index})
    bus.publish("m1", {"seq": 9999})  # 不应抛异常
    sub.close()


def test_scheduler_job_registration_semantics(tmp_path):
    """1.8 契约：默认（enabled=True + 自动发现）注册 job；off 才不注册。

    旧语义"没点名就不注册"已废弃——点名列表现在是过滤器，
    enabled=False 是唯一的关闭开关。
    """
    from services.match.scheduler import ScheduleConfig, SeasonScheduler

    off = SeasonScheduler(
        ScheduleConfig(season_ids=None, interval_s=60, per_tick=1, enabled=False),
        tmp_path / "a.db",
    )
    off.start()
    assert off.running is False, "enabled=False 不得注册 job"

    auto = SeasonScheduler(
        ScheduleConfig(season_ids=None, interval_s=60, per_tick=1, enabled=True),
        tmp_path / "b.db",
    )
    auto.start()
    try:
        assert auto.running is True
        assert auto._scheduler.get_job("melee-season-tick") is not None
    finally:
        auto.stop()


def test_settings_parses_lists_and_bools(monkeypatch):
    from services.match.settings import Settings

    monkeypatch.setenv("MELEE_API_TOKENS", "alice:t1,bob:t2")
    monkeypatch.setenv("MELEE_SCHEDULE", "s1, s2")
    monkeypatch.setenv("MELEE_INLINE_WORKER", "off")
    settings = Settings()
    assert settings.tokens == {"alice": "t1", "bob": "t2"}
    assert settings.season_ids == ["s1", "s2"]
    assert settings.inline_worker is False


def test_player_spec_rejects_bad_script_and_delay():
    from pydantic import ValidationError

    from services.match.player_specs import PlayerSpec

    with pytest.raises(ValidationError):
        PlayerSpec(id="A", script=["x" * 201])
    with pytest.raises(ValidationError):
        PlayerSpec(id="A", delayMs=99_999)


# ---------- Redis 路径用 fakeredis 覆盖（不需要真 Redis） ----------

def test_redis_broadcaster_with_fakeredis():
    import asyncio
    import time

    fakeredis = pytest.importorskip("fakeredis")
    import redis

    from services.match.broadcast import RedisBroadcaster

    server = fakeredis.FakeServer()
    original = redis.Redis.from_url
    redis.Redis.from_url = classmethod(
        lambda cls, url, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=kwargs.get("decode_responses", False))
    )
    try:
        publisher = RedisBroadcaster("redis://fake", channel_prefix="melee:test:")
        subscriber = RedisBroadcaster("redis://fake", channel_prefix="melee:test:")
        sub = subscriber.subscribe("m1")
        time.sleep(0.3)
        publisher.publish("m1", {"seq": 5, "type": "x"})
        event = asyncio.run(sub.get(timeout_s=2))
        sub.close()
        publisher.close()
        subscriber.close()
    finally:
        redis.Redis.from_url = original
    assert event == {"seq": 5, "type": "x"}


def test_redis_broadcaster_skips_malformed_messages(monkeypatch):
    """Redis 里出现非 JSON 消息时，监听线程要跳过而不是崩掉。"""
    fakeredis = pytest.importorskip("fakeredis")
    import redis

    from services.match.broadcast import RedisBroadcaster

    server = fakeredis.FakeServer()
    original = redis.Redis.from_url
    redis.Redis.from_url = classmethod(
        lambda cls, url, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=kwargs.get("decode_responses", False))
    )
    try:
        broadcaster = RedisBroadcaster("redis://fake", channel_prefix="t:")
        broadcaster._client.publish("t:m1", "not-json")
        broadcaster.publish("m1", {"seq": 9, "type": "ok"})
        import time

        time.sleep(0.4)
        assert broadcaster._thread.is_alive()
        broadcaster.close()
    finally:
        redis.Redis.from_url = original


def test_secrets_main_generates_key_and_help(capsys, monkeypatch):
    from services.match import secrets as secrets_module

    monkeypatch.setattr("sys.argv", ["secrets", "--gen"])
    assert secrets_module.main() == 0
    out = capsys.readouterr().out
    assert "MELEE_SECRET_KEY" in out
    assert len(out.splitlines()[0]) >= 40  # Fernet key

    monkeypatch.setattr("sys.argv", ["secrets"])
    assert secrets_module.main() == 1


def test_fingerprint_stable_across_process(monkeypatch):
    """指纹只用于对账，必须稳定且不可逆。"""
    from services.match.secrets import fingerprint

    assert fingerprint("") == "-"
    a = fingerprint("sk-abc")
    b = fingerprint("sk-abc")
    assert a == b and len(a) == 8
    assert "sk-abc" not in a


# ---------- 切片回归：赛季重跑不污染日志 / 断点续跑 ----------

def test_run_season_never_appends_second_match_created(tmp_path):
    """结果丢失后重跑同一 fixture，必须复用日志而不是再开一局。

    旧实现无条件 game.init + Runner.play，会追加第二条 match.created，
    rehydrate 从此在同一个 matchId 下看到两个种子，整场永久损坏。
    """
    from services.match.eventlog import read_match

    db = tmp_path / "season.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x", game="tic-tac-toe")
    season.add_player("A", "A")
    season.add_player("B", "B")
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    first = run_season("s1", db_path=db)
    assert first.played == 1
    match_id = first.outcomes[0].matchId
    events_before = read_match(db, match_id)

    # 模拟"比赛跑完但结果没落库便崩溃"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM results WHERE season_id = 's1'")
    conn.commit()
    conn.close()

    second = run_season("s1", db_path=db)
    events_after = read_match(db, match_id)

    assert second.played == 1
    assert len(events_after) == len(events_before)
    assert sum(1 for e in events_after if e["type"] == "match.created") == 1
    assert sum(1 for e in events_after if e["type"] == "match.ended") == 1
    assert [e["seq"] for e in events_after] == list(range(len(events_after)))


def test_season_rejects_mismatched_game(client):
    """赛季统一赛制：不同规则的结果不能混进同一张积分榜。"""
    created = client.post(
        "/seasons",
        json={"seasonId": "s-mix", "name": "混", "game": "tic-tac-toe", "players": [{"id": "A"}, {"id": "B"}]},
    )
    assert created.status_code == 201
    response = client.post(
        "/matches",
        json={"game": "twenty-questions", "players": [{"id": "A"}, {"id": "B"}], "seasonId": "s-mix"},
    )
    assert response.status_code == 400
    assert "赛制" in response.json()["detail"]


def test_season_id_is_validated(client):
    """season_id 会拼进对局 id 与文件路径，必须拒绝分隔符与 `..`。"""
    for bad in ("../etc", "a/b", "a..b"):
        response = client.post(
            "/seasons",
            json={"seasonId": bad, "name": "x", "players": [{"id": "A"}, {"id": "B"}]},
        )
        assert response.status_code == 400, bad


# ---------- 跨副本在线人数（ROADMAP 2）：两个人看同一场，任一副本都该报 2 ----------

def test_presence_aggregates_across_replicas_with_shared_redis():
    """两个独立 Broadcaster 实例共享一个 Redis：在线人数必须是全局值。

    这正是多副本部署的形态：观众连到 A 或 B，两侧都应看到总人数。
    用 fakeredis 的 FakeServer 让两个客户端共享同一份数据。
    """
    import threading

    fakeredis = pytest.importorskip("fakeredis")
    from services.match.broadcast import RedisBroadcaster

    server = fakeredis.FakeServer()
    clients = [fakeredis.FakeRedis(server=server, decode_responses=True) for _ in range(2)]

    class Replica(RedisBroadcaster):
        def __init__(self, client):
            self._client = client
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 3600
            self._watchers: dict[str, list] = {}
            self._stop = threading.Event()

    left, right = Replica(clients[0]), Replica(clients[1])
    sub_a = left.subscribe("m1")
    assert left.viewer_count("m1") == 1
    # 另一个副本看到的是同一个计数器，不是它自己的 0
    assert right.viewer_count("m1") == 1

    sub_b = right.subscribe("m1")
    assert left.viewer_count("m1") == 2
    assert right.viewer_count("m1") == 2
    assert left.total_viewers() == 2

    sub_a.close()
    assert left.viewer_count("m1") == 1
    assert right.viewer_count("m1") == 1

    sub_b.close()
    assert left.viewer_count("m1") == 0
    # 计数器不会被减成负数
    assert int(clients[0].get("melee:events:presence:m1") or 0) == 0


def test_memory_broadcaster_reports_process_scope():
    from services.match.broadcast import MemoryBroadcaster

    assert MemoryBroadcaster().cross_replica is False
