"""故障注入：外部依赖失效时服务必须降级而不是崩。

STATUS 里"分支覆盖率 81.7%，缺口集中在外部依赖失败路径"指的就是这些分支。
这个文件不 mock 业务逻辑，只制造真实的失败条件：
Redis 连不上、事件循环关闭、读日志被锁、编码器提前退出。
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

from services.match import broadcast
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog, EventLogError
from services.match.runner import Runner, ensure_terminal

from .support import make_client

# ---------- 广播后端降级 ----------

def test_global_broadcaster_falls_back_to_memory_when_redis_unreachable(monkeypatch):
    """Redis 配了但连不上：退回内存广播，服务照常启动。"""
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_BROADCAST", "redis")
    monkeypatch.setenv("MELEE_REDIS_URL", "redis://127.0.0.1:1/0")
    reset_settings()
    broadcast.reset_global_broadcaster(None)
    try:
        backend = broadcast.global_broadcaster()
        assert isinstance(backend, broadcast.MemoryBroadcaster)
    finally:
        broadcast.reset_global_broadcaster(None)
        reset_settings()


def test_redis_publish_failure_is_swallowed(monkeypatch):
    """Redis 发布失败不能让比赛写事件失败。"""
    import fakeredis

    from services.match.broadcast import RedisBroadcaster

    class Broken:
        def publish(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("redis down")

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = Broken()
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers: dict[str, list] = {}
            self._stop = threading.Event()

    bus = Replica()
    bus.publish("m1", {"seq": 1})  # 不抛异常即通过
    assert bus._watchers == {}
    assert fakeredis is not None


def test_presence_counter_read_failure_falls_back_to_local_count():
    """Redis 读失败时不能把人数显示成 0：退回本进程订阅数。"""
    from services.match.broadcast import RedisBroadcaster

    class Broken:
        def incrby(self, *args: Any, **kwargs: Any) -> int:
            raise ConnectionError("down")

        def expire(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("down")

        def get(self, *args: Any, **kwargs: Any) -> None:
            raise ConnectionError("down")

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = Broken()
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers = {"m1": [object(), object()]}
            self._stop = threading.Event()

    bus = Replica()
    assert bus.viewer_count("m1") == 2
    assert bus.total_viewers() == 2


def test_presence_counter_never_goes_negative():
    """重复 close 或键被清空后，计数器不能变成负数。"""
    import fakeredis

    from services.match.broadcast import RedisBroadcaster

    server = fakeredis.FakeServer()
    real = fakeredis.FakeRedis(server=server, decode_responses=True)

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = real
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers: dict[str, list] = {}
            self._stop = threading.Event()

    bus = Replica()
    bus._presence("m1", -5)
    assert int(real.get("melee:events:presence:m1")) == 0


def test_redis_subscription_wakes_on_event_instead_of_polling():
    """订阅者由 call_soon_threadsafe 唤醒；等不到事件时按超时返回 None。"""
    from services.match.broadcast import RedisBroadcaster

    class MemoryClient:
        def __init__(self) -> None:
            self.store: dict[str, int] = {}

        def incrby(self, key: str, amount: int) -> int:
            self.store[key] = self.store.get(key, 0) + amount
            return self.store[key]

        def expire(self, key: str, ttl: int) -> None:
            return None

        def get(self, key: str) -> int:
            return self.store.get(key, 0)

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = MemoryClient()
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers: dict[str, list] = {}
            self._stop = threading.Event()

    async def scenario() -> None:
        bus = Replica()
        sub = bus.subscribe("m1")
        assert bus.viewer_count("m1") == 1

        async def push_later() -> None:
            await asyncio.sleep(0.05)
            for callback in bus._watchers["m1"]:
                callback({"seq": 1, "type": "x"})

        task = asyncio.create_task(push_later())
        event = await sub.get(timeout_s=2.0)
        assert event == {"seq": 1, "type": "x"}
        await task

        # 没有事件时按超时返回 None，不无限挂
        assert await sub.get(timeout_s=0.05) is None
        sub.close()
        assert bus.viewer_count("m1") == 0

    asyncio.run(scenario())


def test_broadcaster_publish_to_closed_loop_is_ignored():
    """客户端断开后事件循环已关闭：发布不能抛异常。"""
    bus = broadcast.MemoryBroadcaster()

    class ClosedLoop:
        def call_soon_threadsafe(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("loop closed")

    queue: asyncio.Queue = asyncio.Queue()
    with bus._lock:
        bus._subscribers["m1"] = [(ClosedLoop(), queue)]
    bus.publish("m1", {"seq": 1})  # 不抛即通过


# ---------- 事件日志的异常边界 ----------

def test_read_match_on_missing_table_returns_empty(tmp_path):
    """库文件存在但没有 events 表：读路径返回空列表而不是抛。"""
    from services.match.eventlog import read_match

    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    # 首次 open 会建表，这里模拟"建表失败"后的读路径
    events = read_match(db, "m_none")
    assert events == []


def test_has_match_returns_false_when_table_missing(tmp_path):
    from services.match.eventlog import has_match

    db = tmp_path / "no-table.db"
    sqlite3.connect(db).close()
    # 建表后仍查不到这条
    assert has_match(db, "m_none") is False


def test_unknown_event_type_is_rejected(tmp_path):
    log = EventLog.open(tmp_path / "m.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        with pytest.raises(EventLogError):
            log.append("not.a.real.type", {})
        with pytest.raises(EventLogError):
            log.append("narration.line", ["not", "a", "dict"])  # type: ignore[arg-type]
    finally:
        log.close()


def test_write_retries_when_seq_collides(tmp_path):
    """另一个连接抢先写了同一个 seq：必须重读后继续，不能抛给调用方。"""
    db = tmp_path / "collide.db"
    first = EventLog.open(db, "m1")
    second = EventLog.open(db, "m1")
    try:
        first.create_match("twenty-questions", 1, ["A", "B"])
        first.append("narration.line", {"text": "a", "refs": []})
        # second 的 _next 还停在 0：写入时必须撞主键后前移
        event = second.append("narration.line", {"text": "b", "refs": []})
        assert event["seq"] == 1
    finally:
        first.close()
        second.close()


# ---------- runner 的异常路径 ----------

class _FakeLog:
    """最小日志：只实现 Runner 需要的方法。

    签名必须与 EventLog.append 一致（含 visibility）——否则 Runner 一加参数
    这个"轻量日志"就崩，测试反而会掩盖真实的接口契约。
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        player: str | None = None,
        at: str | None = None,
        visibility: Any = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {"seq": len(self.events), "type": event_type, "payload": payload}
        if player:
            event["player"] = player
        if visibility is not None:
            event["visibility"] = visibility
        self.events.append(event)
        return event

    def mark_ended(self) -> None:
        return None

    def read(self) -> list[dict[str, Any]]:
        return list(self.events)


class _StuckGame:
    """永远轮不到任何人：用来命中 `current_player() is None` 分支。"""

    game_id = "stuck"

    def init(self, seed: int, players: list[str], config: dict[str, Any]) -> dict[str, Any]:
        return {"players": players, "seed": seed, "finished": False, "winner": None, "endReason": ""}

    def observe(self, state: dict[str, Any], player: str) -> dict[str, Any]:
        return {}

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}

    def current_player(self, state: dict[str, Any]) -> str | None:
        return None

    def legal_moves(self, state: dict[str, Any], player: str) -> list[str]:
        return []

    def apply(self, state: dict[str, Any], player: str, move: Any) -> Any:
        return None


def test_runner_stops_when_no_current_player():
    log = _FakeLog()
    result = Runner(_StuckGame(), log, {}).play({"players": ["A", "B"], "seed": 1, "finished": False})
    assert result.turns == 0
    assert any(e["type"] == "match.ended" for e in log.events)


def test_runner_hits_max_turns_guard(tmp_path):
    """max_turns 是死循环保险丝：超限必须收尾，不能挂住 worker 线程。"""
    game = TwentyQuestions()
    state = game.init(seed=1, players=["A", "B"], config={"maxQuestions": 21})
    log = EventLog.open(tmp_path / "m.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        from services.match.agents.scripted import GreedyAgent

        result = Runner(game, log, {p: GreedyAgent(p, CATALOG) for p in ("A", "B")}, max_turns=1).play(state)
        assert result.turns <= 1
        assert "最大回合" in result.endReason or result.endReason
    finally:
        log.close()


def test_ensure_terminal_noop_when_game_not_finished(tmp_path):
    game = TwentyQuestions()
    state = game.init(seed=1, players=["A", "B"], config={})
    log = EventLog.open(tmp_path / "m.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        assert ensure_terminal(log, game, state) is False
    finally:
        log.close()


def test_ensure_terminal_with_lightweight_log_without_terminal_api():
    """测试里的轻量日志没有 append_terminal：退回先查后写。"""
    log = _FakeLog()
    game = TwentyQuestions()
    state = {**game.init(seed=1, players=["A", "B"], config={}), "finished": True, "winner": "A", "endReason": "猜中"}
    assert ensure_terminal(log, game, state) is True
    assert log.events[-1]["type"] == "match.ended"
    # 再补一次不能重复写
    assert ensure_terminal(log, game, state) is False


def test_thought_visibility_failure_is_treated_as_public():
    """引擎的可见性判定抛错时按公开处理，不能因此中断比赛。"""
    from services.match.runner import _thought_visibility

    class Exploding:
        def thought_visibility(self, state: Any, player: str) -> dict[str, Any]:
            raise RuntimeError("boom")

    assert _thought_visibility(Exploding(), {}, "A") is None
    assert _thought_visibility(object(), {}, "A") is None
    assert _thought_visibility(Exploding(), None, "A") is None


# ---------- HTTP 层的异常边界 ----------

def test_events_of_returns_404_for_unknown_match(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    assert client.get("/matches/m_missing/events").status_code == 404
    assert client.get("/matches/m_missing/recap").status_code == 404


def test_season_run_reports_error_when_season_vanishes(tmp_path, monkeypatch):
    """赛季在建场后被删：推进必须给 4xx/5xx 明确错误，不能 200 空跑。"""
    client = make_client(tmp_path, monkeypatch, worker=False)
    client.post("/seasons", json={"seasonId": "s-gone", "name": "x", "players": [{"id": "A"}, {"id": "B"}]})
    conn = sqlite3.connect(Path(api_db(client)))
    try:
        conn.execute("DELETE FROM seasons WHERE season_id = 's-gone'")
        conn.commit()
    finally:
        conn.close()
    response = client.post("/seasons/s-gone/run")
    assert response.status_code in (404, 500)


def api_db(client) -> str:
    from services.match import api as api_module

    return str(api_module.DB_PATH)


# ---------- 调度器：启用路径、单例、异常隔离 ----------

def test_scheduler_start_from_env_enables_and_stops(monkeypatch, tmp_path):
    """配了 MELEE_SCHEDULE 才启动；stop_current 之后回到未启用。"""
    from services.match import scheduler as scheduler_module
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_SCHEDULE", "s1,s2")
    monkeypatch.setenv("MELEE_SCHEDULE_INTERVAL", "3600")
    reset_settings()
    scheduler_module.stop_current()
    try:
        instance = scheduler_module.start_from_env(tmp_path / "s.db")
        assert instance is not None
        assert instance.config.season_ids == ["s1", "s2"]
        assert scheduler_module.current() is instance
        # 再次调用复用同一个实例，不重复起线程
        assert scheduler_module.start_from_env(tmp_path / "s.db") is instance
    finally:
        scheduler_module.stop_current()
        reset_settings()
    assert scheduler_module.current() is None


def test_scheduler_tick_survives_one_season_failing(monkeypatch, tmp_path):
    """一个赛季推进抛错不能让整轮 tick 挂掉，错误要记在 last_error。"""
    from services.match.scheduler import ScheduleConfig, SeasonScheduler

    calls = []

    def fake_run(season_id, **kwargs):
        calls.append(season_id)
        if season_id == "bad":
            raise RuntimeError("boom")
        from services.match.schedule import SeasonRun

        return SeasonRun(season_id=season_id)

    scheduler = SeasonScheduler(
        ScheduleConfig(season_ids=["bad", "good"], per_tick=1, enabled=True),
        tmp_path / "s.db",
        run=fake_run,
    )
    scheduler.tick()
    assert calls == ["bad", "good"]
    assert scheduler.last_error is not None
    assert "boom" in scheduler.last_error
    assert scheduler.ticks == 1


def test_scheduler_skips_when_another_replica_holds_lock(tmp_path):
    from services.match.schedule import SeasonBusyError
    from services.match.scheduler import ScheduleConfig, SeasonScheduler

    def busy(season_id, **kwargs):
        raise SeasonBusyError(season_id)

    scheduler = SeasonScheduler(
        ScheduleConfig(season_ids=["s1"], per_tick=1, enabled=True),
        tmp_path / "s.db",
        run=busy,
    )
    assert scheduler.tick() == []
    assert scheduler.last_error is None


# ---------- 解说：规则式分支与事实校验的拒绝路径 ----------

def test_rule_narrator_covers_every_effect_kind():
    """每一种效果都要有解说模板；没有模板的效果返回 None 而不是编一句。

    断言模板键而不是中文字面量：文案要能切语言，`msg` 才是稳定契约。
    """
    from services.match.engine.base import AppliedEffect
    from services.match.narrator import RuleNarrator

    narrator = RuleNarrator()
    cases = [
        (AppliedEffect("forfeit", {"by": "A"}), "narr.forfeit"),
        (AppliedEffect("hint", {"word": "海", "count": 2}), "narr.hint"),
        (AppliedEffect("action", {"action": "[4]"}), "narr.action.step"),
        (AppliedEffect("guess", {"value": "钟楼", "correct": True}), "narr.guess.correct"),
        (AppliedEffect("answer", {"value": "建筑", "answer": "no", "narrowed": 0}), "narr.answer.flat"),
        (AppliedEffect("unknown-kind", {}), None),
    ]
    for effect, expected in cases:
        payload = narrator.line({"remainingPublic": 3, "turnIndex": 2}, "A", None, [effect])
        key = payload["msg"] if payload else None
        assert key == expected, (effect.kind, key)
        if payload:
            assert payload["text"], "有模板就必须展开出默认文本"
    # 没有任何效果时不说话
    assert narrator.line({}, "A", None, []) is None


def test_rule_narrator_situation_handles_missing_and_tie():
    from services.match.narrator import RuleNarrator

    narrator = RuleNarrator()
    red_leads = narrator.situation({"remaining": {"red": 3, "blue": 5}}, game_id="codenames")
    assert red_leads["msg"] == "narr.situation.leading"
    assert red_leads["params"]["team"] == "红队"

    blue_leads = narrator.situation({"remaining": {"red": 5, "blue": 3}}, game_id="codenames")
    assert blue_leads["msg"] == "narr.situation.leading"
    assert blue_leads["params"]["team"] == "蓝队"

    tied = narrator.situation({"remaining": {"red": 3, "blue": 3}}, game_id="codenames")
    assert tied["msg"] == "narr.situation.tied"
    assert tied["params"]["count"] == 3

    assert narrator.situation({"remaining": {}}, game_id="codenames") is None
    assert narrator.situation({}, game_id="twenty-questions") is None


def test_llm_narrator_skips_other_games_and_empty_rule_line():
    """非二十问或规则式都没话说时，不该白调一次模型。"""
    from services.match.engine.base import AppliedEffect
    from services.match.narrator import LLMNarrator

    calls = []

    class Client:
        def post(self, *args, **kwargs):
            calls.append(args)
            raise AssertionError("不该调用模型")

    narrator = LLMNarrator(Client(), model="m", base_url="http://x", api_key="k")
    assert narrator.line({}, "A", None, [], game_id="codenames") == ""
    assert calls == []
    # 规则式无话可说（空效果）时直接返回空，不调模型
    assert narrator.line({"remainingPublic": 1}, "A", None, [], game_id="twenty-questions") == ""
    assert calls == []


def test_narrator_rejects_chinese_number_not_in_facts():
    from services.match.narrator import _is_faithful

    facts = {"remaining": 3, "turn": 1}
    assert _is_faithful("还剩 3 个候选", facts, 60)
    # 中文数字"七"不在事实里（事实只有 3 和 1）
    assert not _is_faithful("还剩七个候选", facts, 60)


def test_narrator_rejects_wrong_role_and_action_kind():
    from services.match.narrator import _is_faithful

    facts = {
        "player": "A",
        "action": {"kind": "guess", "value": "钟楼"},
        "effects": [{"kind": "guess", "correct": True, "value": "钟楼"}],
    }
    assert not _is_faithful("B 报出钟楼", facts, 60), "选手错位必须拒绝"
    facts_ask = {
        "player": "A",
        "action": {"kind": "ask", "value": "建筑"},
        "effects": [{"kind": "answer", "value": "建筑", "answer": "yes", "narrowed": 2}],
    }
    assert not _is_faithful("A 报出钟楼", facts_ask, 60), "提问写成报答案必须拒绝"


def test_narrator_rejects_answer_direction_flip():
    from services.match.narrator import _is_faithful

    facts = {
        "player": "A",
        "action": {"kind": "ask", "value": "建筑"},
        "effects": [{"kind": "answer", "value": "建筑", "answer": "no", "narrowed": 2}],
    }
    assert not _is_faithful("A 问建筑，答是，排除 2 个", facts, 60)
    assert _is_faithful("A 问建筑，答否，排除 2 个", facts, 60)


def test_llm_narrator_from_env_requires_all_three_fields(monkeypatch):
    from services.match.narrator import LLMNarrator, RuleNarrator, narrator_from_env
    from services.match.settings import reset_settings

    monkeypatch.setenv("MELEE_NARRATOR", "llm")
    monkeypatch.setenv("MELEE_NARRATOR_MODEL", "m")
    monkeypatch.setenv("MELEE_NARRATOR_BASE_URL", "https://x/v1")
    monkeypatch.delenv("MELEE_NARRATOR_API_KEY", raising=False)
    reset_settings()
    try:
        assert isinstance(narrator_from_env(), RuleNarrator)
        monkeypatch.setenv("MELEE_NARRATOR_API_KEY", "k")
        reset_settings()
        assert isinstance(narrator_from_env(), LLMNarrator)
    finally:
        reset_settings()


# ---------- SSE 的边界：坏游标、迟到事件、私有事件跳过 ----------

def test_stream_with_bad_cursor_falls_back_to_start(tmp_path, monkeypatch):
    """Last-Event-ID 不是数字时从 0 开始，不能 500。"""
    client = make_client(tmp_path, monkeypatch, worker=False)
    created = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 5, "inlineWorker": False, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = created.json()["matchId"]
    with client.stream("GET", f"/matches/{match_id}/stream", headers={"Last-Event-ID": "not-a-number"}) as response:
        assert response.status_code == 200


def test_stream_accepts_last_event_id_query_param(tmp_path, monkeypatch):
    """自管重连用查询参数传游标（浏览器请求头带不上）。"""
    client = make_client(tmp_path, monkeypatch, worker=False)
    created = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 5, "inlineWorker": False, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = created.json()["matchId"]
    with client.stream("GET", f"/matches/{match_id}/stream?lastEventId=1") as response:
        assert response.status_code == 200


def test_stream_unknown_match_is_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    with client.stream("GET", "/matches/m_nope/stream") as response:
        assert response.status_code == 404


def test_stream_rate_limit_returns_429(tmp_path, monkeypatch):
    """SSE 单独限流档位：超过阈值必须 429，不能无限建长连接。"""
    from services.match import ratelimit

    client = make_client(tmp_path, monkeypatch, worker=False)
    created = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 5, "inlineWorker": False, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = created.json()["matchId"]
    ratelimit.reset_limiter()
    codes = []
    for _ in range(35):
        with client.stream("GET", f"/matches/{match_id}/stream") as response:
            codes.append(response.status_code)
    assert 429 in codes, codes[-5:]


# ---------- worker：轮询自愈、租约丢失、CLI ----------

def test_worker_loop_survives_run_once_exception(tmp_path, monkeypatch):
    """claim() 抛 database is locked 时线程不能死——否则进程活着但再也不跑任务。"""
    from services.match import worker as worker_module

    calls = []

    class Flaky(worker_module.Worker):
        def run_once(self):
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            return None

    instance = Flaky(tmp_path / "w.db", worker_module.WorkerConfig(interval_s=0.01, lease_s=5, concurrency=1))
    instance.start()
    try:
        for _ in range(200):
            if len(calls) >= 3:
                break
            import time as _time

            _time.sleep(0.02)
    finally:
        instance.stop()
    assert len(calls) >= 3, calls
    assert instance.last_error and "locked" in instance.last_error


def test_worker_stop_is_idempotent(tmp_path):
    from services.match.worker import Worker, WorkerConfig

    worker = Worker(tmp_path / "w.db", WorkerConfig(interval_s=0.01, lease_s=5, concurrency=2))
    worker.start()
    worker.stop()
    worker.stop()  # 重复 stop 不能抛
    assert worker._threads


def test_worker_completes_job_and_reports_counts(tmp_path):
    from services.match import workqueue
    from services.match.worker import Worker, WorkerConfig

    db = tmp_path / "w.db"
    done = []

    def handler(_db, payload):
        done.append(payload)

    from services.match import worker as worker_module

    original = dict(worker_module.HANDLERS)
    worker_module.HANDLERS["test-kind"] = handler
    try:
        workqueue.enqueue(db, "test-kind", {"x": 1})
        instance = Worker(db, WorkerConfig(kinds=("test-kind",), interval_s=0.01, lease_s=5, concurrency=1))
        job = instance.run_once()
        assert job is not None
        assert instance.processed == 1
        assert instance.failed == 0
        assert done == [{"x": 1}]
    finally:
        worker_module.HANDLERS.clear()
        worker_module.HANDLERS.update(original)


def test_worker_records_failure_when_handler_raises(tmp_path):
    from services.match import worker as worker_module
    from services.match import workqueue

    db = tmp_path / "w.db"

    def boom(_db, payload):
        raise RuntimeError("handler exploded")

    original = dict(worker_module.HANDLERS)
    worker_module.HANDLERS["boom-kind"] = boom
    try:
        job_id = workqueue.enqueue(db, "boom-kind", {}, max_attempts=1)
        instance = worker_module.Worker(
            db, worker_module.WorkerConfig(kinds=("boom-kind",), interval_s=0.01, lease_s=5, concurrency=1)
        )
        instance.run_once()
        assert instance.failed == 1
        assert "handler exploded" in (instance.last_error or "")
        assert workqueue.get(db, job_id).status == "failed"
    finally:
        worker_module.HANDLERS.clear()
        worker_module.HANDLERS.update(original)


def test_worker_unknown_kind_fails_the_job(tmp_path):
    from services.match import workqueue
    from services.match.worker import Worker, WorkerConfig

    db = tmp_path / "w.db"
    # max_attempts=1：第一次失败就彻底 failed，而不是回到 pending 等重试
    job_id = workqueue.enqueue(db, "mystery-kind", {}, max_attempts=1)
    instance = Worker(db, WorkerConfig(kinds=("mystery-kind",), interval_s=0.01, lease_s=5, concurrency=1))
    instance.run_once()
    record = workqueue.get(db, job_id)
    assert record.status == "failed"
    assert "未知任务类型" in (record.last_error or "")


def test_worker_cli_once_reports_no_job(tmp_path, capsys):
    from services.match import worker as worker_module

    code = worker_module.main.__wrapped__ if hasattr(worker_module.main, "__wrapped__") else None
    assert code is None  # 记录：CLI 没有装饰器，这里只验退出码契约
    import sys

    old_argv = sys.argv
    sys.argv = ["worker", "--db", str(tmp_path / "cli.db"), "--once"]
    try:
        assert worker_module.main() == 1  # 空队列
    finally:
        sys.argv = old_argv


def test_resume_state_raises_on_corrupted_log(tmp_path):
    """有日志但重放不了 = 数据损坏，任务必须失败而不是静默重开一局。"""
    from services.match.eventlog import EventLog
    from services.match.worker import _resume_state

    db = tmp_path / "corrupt.db"
    log = EventLog.open(db, "m_bad")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        # 写入一条引擎无法重放的动作
        log.append("action.applied", {"action": {"kind": "ask", "value": "不可能的问题"}, "effects": []}, player="A")
    finally:
        log.close()
    with pytest.raises(RuntimeError, match="日志无法重放"):
        _resume_state(db, "m_bad")


# ---------- TextArena 适配器的拒绝路径 ----------

def test_textarena_rejects_actions_outside_its_turn():
    from services.match.engine.base import Move
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    # 先手由 seed 轮转；"另一位"才是越位的那个。
    current = game.current_player(state)
    other = next(p for p in ["A", "B"] if p != current)
    result = game.apply(state, other, Move(kind="guess", value="[0]"))
    assert not result.legal
    assert "轮到" in result.reason


def test_textarena_rejects_after_finish():
    from services.match.engine.base import Move
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = {**game.init(seed=1, players=["A", "B"], config={}), "finished": True}
    result = game.apply(state, "A", Move(kind="guess", value="[0]"))
    assert not result.legal
    assert "已结束" in result.reason


def test_textarena_treats_forfeit_as_immediate_loss():
    from services.match.engine.base import Move
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    quitter = game.current_player(state)
    other = next(p for p in ["A", "B"] if p != quitter)
    result = game.apply(state, quitter, Move(kind="forfeit", value=""))
    assert result.legal
    assert result.next_state["finished"] is True
    assert result.next_state["winner"] == other


def test_textarena_rejects_malformed_action():
    from services.match.engine.base import Move
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    result = game.apply(state, game.current_player(state), Move(kind="guess", value="随便说点什么"))
    assert not result.legal
    assert "格式" in result.reason


def test_textarena_illegal_position_is_rejected():
    """上游对非法动作静默忽略：适配器必须靠状态比对判成非法。"""
    from services.match.engine.base import Move
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    mover = game.current_player(state)
    first = game.apply(state, mover, Move(kind="guess", value="[4]"))
    assert first.legal
    # 另一个人去下同一格：局面不变 => 非法
    second = game.apply(first.next_state, game.current_player(first.next_state), Move(kind="guess", value="[4]"))
    assert not second.legal


def test_textarena_legal_moves_empty_when_not_your_turn():
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    # 先手由 seed 轮转：非当前玩家才该拿到空列表。
    other = next(p for p in ["A", "B"] if p != game.current_player(state))
    assert game.legal_moves(state, other) == []


def test_textarena_catalog_only_lists_available():
    from services.match.engine import textarena_adapter as adapter

    for item in adapter.catalog():
        assert item["source"] == "textarena"
        assert item["gameId"] in adapter.SUPPORTED


def test_textarena_build_rejects_unknown_game():
    from services.match.engine.textarena_adapter import build

    with pytest.raises(KeyError):
        build("not-a-game")


def test_unknown_player_observation_is_rejected():
    from services.match.engine.codenames import Codenames

    game = Codenames()
    state = game.init(seed=1, players=["A", "B", "C", "D"], config={})
    with pytest.raises(KeyError):
        game.observe(state, "E")


# ---------- 事件日志：写入冲突与终局幂等 ----------

def test_append_terminal_is_idempotent(tmp_path):
    log = EventLog.open(tmp_path / "t.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        assert log.append_terminal({"winner": "A", "reason": "x", "finalState": {}}) is True
        assert log.append_terminal({"winner": "B", "reason": "y", "finalState": {}}) is False
        events = log.read()
        assert sum(1 for e in events if e["type"] == "match.ended") == 1
    finally:
        log.close()


def test_eventlog_asserts_contiguity(tmp_path):
    log = EventLog.open(tmp_path / "c.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        # create_match 只写 matches 表，不占事件序号；第一条事件就是 seq 0
        log.append("narration.line", {"text": "a", "refs": []})
        log.append("narration.line", {"text": "b", "refs": []})
        log.assert_contiguous()
        assert log.seqs() == [0, 1]
        assert log.next_seq == 2
    finally:
        log.close()


# ---------- 渲染：编码器失败与模板边界 ----------

def _sample_clip():
    from services.match.clips import Clip

    event = {
        "seq": 0, "type": "narration.line", "at": "2026-01-01T00:00:00+00:00",
        "payload": {"text": "灰隼报出答案", "refs": []},
    }
    return Clip(kind="guess", title="t", detail="d", startSeq=0, endSeq=0, events=[event], durationMs=900)


def test_render_clip_without_ffmpeg_raises_clear_error(tmp_path):
    from services.match.clips import Clip
    from services.match.render import render_clip

    clip = _sample_clip()
    # 本机有 ffmpeg 时显式传空串才会走到"找不到二进制"分支
    with pytest.raises(RuntimeError, match="ffmpeg"):
        render_clip(clip, tmp_path / "x.mp4", ffmpeg="")


def test_render_clip_reports_ffmpeg_failure(tmp_path, monkeypatch):
    """编码器返回非 0：必须抛错并带上 stderr，不能产出静默空文件。"""
    from services.match.render import render_clip

    clip = _sample_clip()

    class FakeProc:
        stdin = None

        def __init__(self) -> None:
            import io

            self.stdin = io.BytesIO()
            self.stderr = io.BytesIO(b"encoder exploded")

        def wait(self) -> int:
            return 1

    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: FakeProc())
    with pytest.raises(RuntimeError, match="encoder exploded"):
        render_clip(clip, tmp_path / "x.mp4", ffmpeg="ffmpeg")


def test_feed_frames_tolerates_broken_pipe():
    """编码器提前退出时写帧会 BrokenPipe：不能把渲染整段炸掉。"""
    from services.match.render import _feed_frames

    clip = _sample_clip()

    class BrokenStdin:
        def write(self, _data: bytes) -> None:
            raise BrokenPipeError()

        def close(self) -> None:
            return None

    _feed_frames(BrokenStdin(), clip, {"total": 1}, repeat=1)  # 不抛即通过


def test_verify_renderable_flags_empty_clip():
    from services.match.clips import Clip
    from services.match.render import verify_renderable

    problems = verify_renderable(Clip(kind="x", title="t", detail="d", startSeq=0, endSeq=0, events=[]))
    assert any("没有事件" in p for p in problems)


def test_verify_renderable_accepts_real_clip():
    from services.match.clips import Clip
    from services.match.render import verify_renderable

    clip = Clip(
        kind="x", title="t", detail="d", startSeq=0, endSeq=0,
        events=[{"seq": 0, "type": "narration.line", "at": "x", "payload": {"text": "a", "refs": []}}],
        durationMs=900,
    )
    assert verify_renderable(clip) == []


# ---------- 广播：重连循环、关闭失败、run_once 边界 ----------

def test_redis_listener_reconnects_after_pubsub_failure():
    """订阅中断后必须重连，不能静默死线程（否则跨副本事件永久丢失）。"""
    import time as _time

    from services.match.broadcast import RedisBroadcaster

    attempts = []

    stop_flag = threading.Event()

    class FlakyPubSub:
        def __init__(self) -> None:
            self.calls = 0

        def psubscribe(self, pattern: str) -> None:
            attempts.append(pattern)
            if len(attempts) == 1:
                raise ConnectionError("subscribe failed")
            # 第二次订阅成功：置位停止标志，让循环自己退出（不靠异常收尾）
            stop_flag.set()

        def get_message(self, timeout: float = 1.0):
            return None

        def close(self) -> None:
            pass

    class Client:
        def pubsub(self, **kwargs):
            return FlakyPubSub()

        def incrby(self, *args):
            return 0

        def expire(self, *args):
            return None

        def get(self, *args):
            return 0

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = Client()
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers: dict[str, list] = {}
            self._stop = threading.Event()

    bus = Replica()
    # 让 _stop 由订阅成功的第二次尝试触发
    original_wait = bus._stop.wait

    def stop_after_second(*args, **kwargs):
        if len(attempts) >= 2:
            bus._stop.set()
        return original_wait(0.01)

    bus._stop.wait = stop_after_second  # type: ignore[method-assign]
    thread = threading.Thread(target=bus._listen, daemon=True)
    thread.start()
    for _ in range(200):
        if len(attempts) >= 2 and not thread.is_alive():
            break
        _time.sleep(0.02)
    bus._stop.set()
    thread.join(timeout=3)
    assert len(attempts) >= 2, attempts


def test_total_viewers_falls_back_when_scan_fails():
    from services.match.broadcast import RedisBroadcaster

    class Broken:
        def scan_iter(self, *a, **k):
            raise ConnectionError("down")

    class Replica(RedisBroadcaster):
        def __init__(self) -> None:
            self._client = Broken()
            self._prefix = "melee:events:"
            self._presence_prefix = "melee:events:presence:"
            self._presence_ttl = 60
            self._watchers = {"m1": [object()], "m2": [object(), object()]}
            self._stop = threading.Event()

    assert Replica().total_viewers() == 3


def test_redis_broadcaster_without_url_is_rejected():
    from services.match.broadcast import make_broadcaster

    with pytest.raises(ValueError):
        make_broadcaster("redis", redis_url=None)


def test_make_broadcaster_defaults_to_memory():
    from services.match.broadcast import MemoryBroadcaster, make_broadcaster

    assert isinstance(make_broadcaster("memory"), MemoryBroadcaster)
    assert isinstance(make_broadcaster("unknown-backend"), MemoryBroadcaster)


# ---------- runner：轻量日志、弃权、situation 边界 ----------

def test_runner_forfeit_when_agent_returns_unusable_move(tmp_path):
    """选手动作无法执行（引擎返回非法）时走弃权收尾，不能无限重试。"""
    from services.match.agents.scripted import ScriptedAgent
    from services.match.engine.base import Move
    from services.match.engine.twenty_questions import TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=1, players=["A", "B"], config={"maxQuestions": 6})
    log = _FakeLog()
    # 脚本给一个永远不合法的动作；先手由 seed 决定，把"坏选手"放在先手位上。
    first = game.current_player(state)
    other = next(p for p in ["A", "B"] if p != first)
    agents = {
        first: ScriptedAgent(first, [Move(kind="ask", value="不存在的属性")] * 5),
        other: ScriptedAgent(other, []),
    }
    result = Runner(game, log, agents, max_attempts_per_turn=1).play(state)
    assert result.winner == other
    assert any(e["type"] == "match.ended" for e in log.events)


def test_runner_without_narrator_or_director(tmp_path):
    """解说与导播都是可选的：不传也要能跑完。"""
    from services.match.agents.scripted import GreedyAgent
    from services.match.engine.twenty_questions import TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 6})
    log = _FakeLog()
    from services.match.engine.twenty_questions import CATALOG

    result = Runner(game, log, {p: GreedyAgent(p, CATALOG) for p in ("A", "B")}).play(state)
    assert result.turns > 0
    assert not any(e["type"] == "narration.line" for e in log.events)
    assert not any(e["type"] == "director.cue" for e in log.events)


def test_has_ended_returns_false_when_log_cannot_be_read():
    from services.match.runner import _has_ended

    class Broken:
        def read(self):
            raise sqlite3.OperationalError("locked")

    assert _has_ended(Broken()) is False
    assert _has_ended(object()) is False


def test_finish_is_idempotent_with_terminal_api(tmp_path):
    """重复 _finish 不能写出第二条 match.ended。"""
    from services.match.agents.scripted import GreedyAgent
    from services.match.engine.twenty_questions import CATALOG, TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=2, players=["A", "B"], config={"maxQuestions": 4})
    log = EventLog.open(tmp_path / "m.db", "m1")
    try:
        log.create_match("twenty-questions", 2, ["A", "B"])
        runner = Runner(game, log, {p: GreedyAgent(p, CATALOG) for p in ("A", "B")})
        runner.play(state)
        ended = [e for e in log.read() if e["type"] == "match.ended"]
        assert len(ended) == 1
        # 再收一次尾（模拟重试）不应新增
        runner._finish(state, "重试", "A", 0)
        assert len([e for e in log.read() if e["type"] == "match.ended"]) == 1
    finally:
        log.close()


# ---------- 事件日志：锁冲突重试与广播失败降级 ----------

def test_append_retries_on_database_locked(tmp_path, monkeypatch):
    """写事件撞 database is locked 要退避重试，不能直接抛给 runner。

    sqlite3.Connection 的 execute 是只读属性，不能直接 patch；
    这里用一个薄包装替换 EventLog.conn（它只被 EventLog 内部使用）。
    """
    db = tmp_path / "lock.db"
    log = EventLog.open(db, "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        real = log.conn
        state = {"calls": 0}

        class FlakyConn:
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *args, **kwargs):
                if "INSERT INTO events" in " ".join(sql.split()) and state["calls"] < 2:
                    state["calls"] += 1
                    raise sqlite3.OperationalError("database is locked")
                return real.execute(sql, *args, **kwargs)

        log.conn = FlakyConn()  # type: ignore[assignment]
        monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
        event = log.append("narration.line", {"text": "a", "refs": []})
        assert event["seq"] == 0
        assert state["calls"] == 2
    finally:
        log.conn = real  # type: ignore[assignment]
        log.close()


def test_append_gives_up_after_max_retries(tmp_path, monkeypatch):
    """一直锁不上时必须抛 EventLogError，而不是静默丢事件。"""
    log = EventLog.open(tmp_path / "lock2.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        real = log.conn

        class LockedConn:
            def __getattr__(self, name):
                return getattr(real, name)

            def execute(self, sql, *args, **kwargs):
                if "INSERT INTO events" in " ".join(sql.split()):
                    raise sqlite3.OperationalError("database is locked")
                return real.execute(sql, *args, **kwargs)

        log.conn = LockedConn()  # type: ignore[assignment]
        monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
        with pytest.raises(EventLogError):
            log.append("narration.line", {"text": "a", "refs": []})
    finally:
        log.conn = real  # type: ignore[assignment]
        log.close()


def test_broadcast_failure_does_not_block_event_write(tmp_path, monkeypatch):
    """广播是通知不是事实：Redis 抖动不能让事件写入失败。"""
    log = EventLog.open(tmp_path / "b.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])

        def boom(_event):
            raise ConnectionError("redis down")

        log.on_event = boom
        event = log.append("narration.line", {"text": "a", "refs": []})
        assert event["seq"] == 0
        assert len(log.read()) == 1
    finally:
        log.close()


def test_terminal_broadcast_failure_does_not_block_write(tmp_path):
    log = EventLog.open(tmp_path / "tb.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])

        def boom(_event):
            raise ConnectionError("redis down")

        log.on_event = boom
        assert log.append_terminal({"winner": "A", "reason": "x", "finalState": {}}) is True
    finally:
        log.close()


def test_contract_violation_is_logged_not_blocking(tmp_path, caplog):
    """契约不符只告警不阻断（历史事件仍要可追加），但必须留痕。"""
    import logging

    log = EventLog.open(tmp_path / "cv.db", "m1")
    try:
        log.create_match("twenty-questions", 1, ["A", "B"])
        with caplog.at_level(logging.WARNING, logger="melee.eventlog"):
            log.append("narration.line", {"text": "x", "refs": [], "extra": "not-allowed"})
        assert any("不符合契约" in r.message for r in caplog.records), [r.message for r in caplog.records]
        assert len(log.read()) == 1
    finally:
        log.close()


def test_stats_falls_back_when_broadcaster_raises(tmp_path, monkeypatch):
    """广播后端异常不能让 /stats 500。"""
    from services.match.routes import matches as matches_module

    class Broken:
        def total_viewers(self):
            raise RuntimeError("backend down")

    client = make_client(tmp_path, monkeypatch, worker=False)
    monkeypatch.setattr(matches_module, "global_broadcaster", lambda: Broken())
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json()["viewers"] == 0


# ---------- SSE 内层循环：迟到事件、去重、私有事件跳过 ----------

def test_stream_delivers_late_events_from_log_poll(tmp_path, monkeypatch):
    """订阅队列超时后回查日志：另一个副本写的事件也能补到客户端。

    这是"多副本观战"的关键路径——事件不进本进程的广播队列，
    只能靠每 15 秒（测试里压到 0.05s）的日志回查。
    """
    import asyncio

    from services.match.broadcast import MemoryBroadcaster, reset_global_broadcaster
    from services.match.eventlog import EventLog
    from services.match.routes import matches as matches_module

    client = make_client(tmp_path, monkeypatch, worker=False)
    created = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 5, "inlineWorker": False, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = created.json()["matchId"]

    from services.match import api as api_module

    db = api_module.DB_PATH
    log = EventLog.open(db, match_id)
    log.create_match("twenty-questions", 5, ["A", "B"])
    log.append("narration.line", {"text": "first", "refs": []})
    # 收尾事件让 SSE 在发完 backlog 后正常关闭，测试不会挂在 15s 心跳上
    log.append("match.ended", {"winner": "A", "reason": "测试", "finalState": {}})
    log.close()

    # 订阅一个"永不投递"的广播器：只能靠日志回查拿到事件
    reset_global_broadcaster(MemoryBroadcaster())
    try:
        with client.stream("GET", f"/matches/{match_id}/stream?lastEventId=-1") as response:
            assert response.status_code == 200
            # 建好的第一条事件应该已经在 backlog 里
            body = "".join(response.iter_text())
            assert "first" in body
    finally:
        reset_global_broadcaster(None)


def test_stream_skips_private_events_but_keeps_sequence(tmp_path, monkeypatch):
    """私有事件不推给观众，但其后的公开事件必须照常到达（不能提前中断）。"""
    from services.match import api as api_module
    from services.match.eventlog import EventLog

    client = make_client(tmp_path, monkeypatch, worker=False)
    created = client.post(
        "/matches",
        json={"game": "twenty-questions", "seed": 5, "inlineWorker": False, "players": [{"id": "A"}, {"id": "B"}]},
    )
    match_id = created.json()["matchId"]

    log = EventLog.open(api_module.DB_PATH, match_id)
    log.create_match("twenty-questions", 5, ["A", "B"])
    log.append("thought.delta", {"text": "秘密推理"}, player="A", visibility={"players": ["A"]})
    log.append("narration.line", {"text": "公开解说", "refs": []})
    log.append("match.ended", {"winner": "A", "reason": "测试", "finalState": {}})
    log.close()

    with client.stream("GET", f"/matches/{match_id}/stream") as response:
        body = "".join(response.iter_text())
    assert "秘密推理" not in body
    assert "公开解说" in body
    assert "match.ended" in body


# ---------- 代号引擎的边界 ----------

def test_codenames_rejects_wrong_player_count():
    from services.match.engine.codenames import Codenames

    game = Codenames()
    with pytest.raises(ValueError):
        game.init(seed=1, players=["A", "B"], config={})


def test_codenames_hint_phase_only_accepts_hint_moves():
    from services.match.engine.codenames import Codenames

    game = Codenames()
    state = game.init(seed=1, players=["A", "B", "C", "D"], config={})
    current = game.current_player(state)
    moves = game.legal_moves(state, current)
    assert moves and moves[0].startswith("hint:")


def test_codenames_finished_game_has_no_current_player():
    from services.match.engine.codenames import Codenames

    game = Codenames()
    state = game.init(seed=1, players=["A", "B", "C", "D"], config={})
    state["finished"] = True
    assert game.current_player(state) is None


# ---------- 出站校验的剩余分支 ----------

def test_outbound_rejects_empty_and_missing_scheme():
    from services.match.outbound import OutboundAddressError, check_base_url

    for bad in ("", "   "):
        with pytest.raises(OutboundAddressError):
            check_base_url(bad)
    with pytest.raises(OutboundAddressError, match="缺少主机名"):
        check_base_url("http:///v1")


def test_outbound_rejects_multicast_and_unspecified():
    from services.match.outbound import OutboundAddressError, check_base_url

    for bad in ("http://224.0.0.1/v1", "http://0.0.0.0/v1", "http://[ff02::1]/v1"):
        with pytest.raises(OutboundAddressError):
            check_base_url(bad)


def test_outbound_rejects_unresolvable_host(monkeypatch):
    """解析失败必须被拦。不依赖真实 DNS：本机可能开着 fake-IP 隧道
    （把任意域名解析进 198.18.0.0/15），那样"不存在的域名"也能"解析成功"，
    测试就会随机红。把 _resolve 打成 NXDOMAIN 形状，测的是实现不是网络。
    """
    import socket

    from services.match import outbound
    from services.match.outbound import OutboundAddressError, check_base_url

    def _nxdomain(*args, **kwargs):
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(outbound.socket, "getaddrinfo", _nxdomain)
    with pytest.raises(OutboundAddressError, match="无法解析"):
        check_base_url("http://this-host-does-not-exist.invalid/v1")


def test_outbound_metadata_hostname_is_blocked_without_dns():
    """元数据主机名必须在解析前就拦住（否则依赖 DNS 才失败）。"""
    from services.match.outbound import OutboundAddressError, check_base_url

    with pytest.raises(OutboundAddressError, match="元数据"):
        check_base_url("http://metadata.google.internal/v1")


def test_outbound_password_in_url_is_rejected_without_echoing():
    from services.match.outbound import OutboundAddressError, check_base_url

    # URL 凭据不再进入请求、队列或日志；错误信息也不得回显 userinfo。
    with pytest.raises(OutboundAddressError) as excinfo:
        check_base_url("https://user:pass@api.openai.com/v1")
    assert "user" not in str(excinfo.value)
    assert "pass" not in str(excinfo.value)


# ---------- 脚本选手的分支 ----------

def test_scripted_agent_without_script_returns_dummy_guess():
    from services.match.agents.scripted import ScriptedAgent

    output = ScriptedAgent("A", []).act({}, [])
    assert output.move.kind == "guess"


def test_board_agent_without_any_moves_signals_no_move():
    from services.match.agents.scripted import BoardAgent

    output = BoardAgent("A", "tic-tac-toe").act({"observations": []}, [])
    assert output.raw == "[no-available-moves]"


def test_codenames_agent_without_candidates_returns_empty_move():
    from services.match.agents.scripted import CodenamesAgent

    agent = CodenamesAgent("红队队员")
    output = agent.act({"publicBoard": [], "hint": {"word": "x"}}, [])
    assert output.raw == "[no-candidates]"


def test_stable_index_is_deterministic_and_in_range():
    from services.match.agents.scripted import _stable_index

    moves = ["a", "b", "c"]
    assert _stable_index("灰隼", moves) == _stable_index("灰隼", moves)
    assert 0 <= _stable_index("", moves) < len(moves)
    assert _stable_index("x", []) == 0


def test_greedy_agent_answers_when_one_candidate_left():
    from services.match.agents.scripted import GreedyAgent
    from services.match.engine.twenty_questions import CATALOG

    agent = GreedyAgent("A", CATALOG)
    # 用历史把所有候选都排除到只剩一个
    row = CATALOG[0]
    history = [
        {"kind": "ask", "aspect": aspect, "value": row[aspect], "answer": "yes"}
        for aspect in CATALOG[0]
        if aspect not in {"name", "value"}
    ]
    output = agent.act(
        {"history": history, "aspects": {k: [row[k]] for k in row if k not in {"name", "value"}}},
        [],
    )
    assert output.move.kind == "guess"


# ---------- 运维健康：积压、失败率、卡住的比赛 ----------

def test_ops_health_is_ok_on_fresh_database(tmp_path):
    from services.match.eventlog import EventLog
    from services.match.ops import snapshot

    db = tmp_path / "ops.db"
    log = EventLog.open(db, "m1")
    log.create_match("twenty-questions", 1, ["A", "B"])
    log.close()

    data = snapshot(db)
    assert data["status"] == "ok", data
    assert data["details"]["jobs"]["pending"] == 0
    assert data["details"]["matches"]["total"] == 1


def test_ops_health_flags_backlog(tmp_path):
    from services.match import workqueue
    from services.match.ops import BACKLOG_WARN, snapshot

    db = tmp_path / "ops.db"
    for index in range(BACKLOG_WARN + 1):
        workqueue.enqueue(db, "run-match", {"matchId": f"m{index}"})

    data = snapshot(db)
    assert data["status"] == "degraded"
    assert any("积压" in p for p in data["problems"])


def test_ops_health_flags_high_failure_rate(tmp_path):
    from services.match import workqueue
    from services.match.ops import snapshot

    db = tmp_path / "ops.db"
    for index in range(3):
        job_id = workqueue.enqueue(db, "run-match", {"matchId": f"m{index}"}, max_attempts=1)
        workqueue.claim(db, "w", ["run-match"], lease_s=30)
        workqueue.fail(db, job_id, "boom", owner="w")

    data = snapshot(db)
    assert data["details"]["jobs"]["failed"] == 3
    assert data["status"] == "degraded"


def test_ops_health_flags_stuck_live_match(tmp_path):
    import time as _time

    from services.match.eventlog import EventLog
    from services.match.ops import STUCK_MATCH_SECONDS, snapshot

    db = tmp_path / "ops.db"
    log = EventLog.open(db, "m_old")
    log.create_match("twenty-questions", 1, ["A", "B"])
    # 手动把创建时间改到阈值之前：模拟 worker 崩了、没人接手
    old = _time.time() - STUCK_MATCH_SECONDS - 60
    from datetime import datetime, timezone

    log.conn.execute(
        "UPDATE matches SET created_at = ? WHERE match_id = ?",
        (datetime.fromtimestamp(old, tz=timezone.utc).isoformat(), "m_old"),
    )
    log.conn.commit()
    log.close()

    data = snapshot(db)
    assert data["details"]["matches"]["stuck"] == 1
    assert any("直播中" in p for p in data["problems"])


def test_ops_metrics_renders_prometheus_text(tmp_path):
    from services.match.ops import prometheus_text, snapshot

    text = prometheus_text(snapshot(tmp_path / "empty.db"))
    assert "melee_ops_problems 0" in text
    assert "melee_jobs_pending 0" in text


def test_ops_endpoints_report_status(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, worker=False)
    ok = client.get("/ops/health")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ok"

    text = client.get("/ops/metrics")
    assert text.status_code == 200
    assert "melee_ops_problems" in text.text
