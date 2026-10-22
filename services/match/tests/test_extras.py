from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

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

import sqlite3


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
    assert "排除 2 个候选" in line


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
    assert "猜中" in line


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
