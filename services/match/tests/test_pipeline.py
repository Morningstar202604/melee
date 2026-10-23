from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from services.match.agents.scripted import GreedyAgent
from services.match.clips import build_clips
from services.match.director import Director
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator, _is_faithful
from services.match.rehydrate import RehydrateError, known_matches, rehydrate, summarize
from services.match.render import build_context, draw_frame, find_ffmpeg, render_clip, verify_renderable
from services.match.runner import Runner
from services.match.schedule import run_season
from services.match.scheduler import SeasonScheduler, ScheduleConfig
from services.match.season import Season, persist


def play(seed: int = 7, db: Path | None = None, match_id: str = "m") -> tuple[Path, list[dict]]:
    path = db or Path(tempfile.mkdtemp()) / "m.db"
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    log = EventLog.open(path, match_id)
    log.create_match(game.game_id, seed, ["A", "B"])
    Runner(
        game,
        log,
        {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)},
        narrator=RuleNarrator(),
        director=Director(),
    ).play(state)
    events = log.read()
    log.close()
    return path, events


# ---------- 重放 ----------

def test_rehydrate_matches_live_result():
    path, events = play()
    rebuilt = rehydrate(path, "m")
    end = next(e for e in events if e["type"] == "match.ended")
    assert rebuilt.finished
    assert rebuilt.state["winner"] == end["payload"]["winner"]
    assert rebuilt.public_state()["remainingPublic"] == end["payload"]["finalState"]["remainingPublic"]


def test_rehydrate_rejects_unknown_match():
    path = Path(tempfile.mkdtemp()) / "m.db"
    with pytest.raises(RehydrateError):
        rehydrate(path, "nope")


def test_known_matches_lists_everything():
    path, _events = play()
    assert "m" in known_matches(path)


def test_summary_has_current_player_while_running():
    path = Path(tempfile.mkdtemp()) / "m.db"
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    log = EventLog.open(path, "live")
    log.create_match(game.game_id, 3, ["A", "B"])

    # 只写开局与一手，制造"进行中"的日志
    log.append("match.created", {"game": game.game_id, "players": ["A", "B"], "seed": 3})
    log.append("match.started", {"firstPlayer": "A"})
    result = game.apply(state, "A", __import__("services.match.engine.base", fromlist=["Move"]).Move("ask", "category=建筑"))
    assert result.legal
    log.append(
        "action.applied",
        {"action": {"kind": "ask", "value": "category=建筑"}, "effects": [{"kind": e.kind, **e.detail} for e in result.effects]},
        player="A",
    )
    log.close()

    summary = summarize(rehydrate(path, "live"))
    assert summary["finished"] is False
    assert summary["currentPlayer"] == "B"
    assert summary["turnIndex"] == 1


# ---------- 渲染 ----------

@pytest.mark.skipif(find_ffmpeg() is None, reason="环境里没有 ffmpeg")
def test_render_produces_playable_file(tmp_path):
    path, events = play()
    clip = build_clips(events, limit=1)[0]
    assert verify_renderable(clip) == []

    result = render_clip(clip, tmp_path / "clip.mp4", per_event_ms=300, all_events=events)
    assert result.path.exists()
    assert result.bytes > 1000
    assert result.frames == len(clip.events) * 3  # 300ms @ 10fps


def test_frame_is_deterministic():
    _path, events = play()
    clip = build_clips(events, limit=1)[0]
    context = build_context(events)
    target = clip.events[0]

    first = draw_frame(target, clip=clip, context={**context, "index": 0})
    second = draw_frame(target, clip=clip, context={**context, "index": 0})
    assert first.tobytes() == second.tobytes()


def test_context_counts_full_match_not_window():
    _path, events = play()
    context = build_context(events)
    applied = [e for e in events if e["type"] == "action.applied"]
    assert sum(context["asksBy"].values()) == len(applied)


def test_verify_renderable_rejects_empty_clip():
    from services.match.clips import Clip

    problems = verify_renderable(Clip(kind="x", title="t", detail="d", startSeq=0, endSeq=0, events=[]))
    assert problems


# ---------- 调度 ----------

def test_scheduler_disabled_by_default(tmp_path):
    """没配置赛季时 start() 不注册 job（APScheduler 不会启动）。"""
    scheduler = SeasonScheduler(ScheduleConfig(), tmp_path / "s.db")
    scheduler.start()
    assert scheduler.running is False
    assert scheduler.tick() == []


def test_scheduler_tick_plays_one_fixture(tmp_path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    scheduler = SeasonScheduler(ScheduleConfig(season_ids=["s1"], per_tick=1, enabled=True), db)
    played = scheduler.tick()
    assert len(played) == 1

    played_again = scheduler.tick()
    assert len(played_again) == 1
    assert played_again[0]["matchId"] != played[0]["matchId"]


def test_scheduler_stops_when_season_done(tmp_path):
    db = tmp_path / "s.db"
    conn = sqlite3.connect(db)
    season = Season("s1", "x")
    for pid in "AB":
        season.add_player(pid, pid)
    season.build_round_robin()
    persist(conn, season)
    conn.close()

    scheduler = SeasonScheduler(ScheduleConfig(season_ids=["s1"], per_tick=5, enabled=True), db)
    assert len(scheduler.tick()) == 1
    assert scheduler.tick() == []


# ---------- 语义级解说校验 ----------

def facts(kind: str, answer: str | None, player: str = "A") -> dict:
    effects = [{"kind": "answer", "answer": answer, "narrowed": 2, "value": "海边"}] if answer else [
        {"kind": "guess", "value": "灯塔", "correct": True}
    ]
    return {
        "player": player,
        "action": {"kind": kind, "value": "place=海边"},
        "effects": effects,
        "remaining": 3,
    }


def test_semantic_answer_direction_is_enforced():
    assert _is_faithful("A 问海边，答否，剩 3 个", facts("ask", "no"), 60)
    assert not _is_faithful("A 问海边，答是，剩 3 个", facts("ask", "no"), 60)
    assert _is_faithful("A 问海边，答是，剩 3 个", facts("ask", "yes"), 60)
    assert not _is_faithful("A 问海边，答否，剩 3 个", facts("ask", "yes"), 60)


def test_semantic_role_is_enforced():
    assert not _is_faithful("B 问海边，答否", facts("ask", "no"), 60)


def test_semantic_action_kind_is_enforced():
    guess_facts = facts("guess", None)
    assert not _is_faithful("A 问海边", guess_facts, 60)
    assert _is_faithful("A 报出灯塔", guess_facts, 60)


def test_english_words_do_not_trigger_role_check():
    # "AB" 里出现的 B 不该被当作选手 B
    assert _is_faithful("A 的 AB 测试通过，剩 3 个", facts("ask", "no"), 60)


# ---------- CLI / 探针的可执行性（覆盖 0% 的两个入口） ----------

def test_demo_runs_and_prints_timeline(tmp_path):
    from services.match import demo

    payload = demo.run(7, str(tmp_path / "demo.db"))
    assert payload["events"], "demo 应产出事件"
    assert payload["view"]["winner"] in {"A", "B"}
    assert any(e["type"] == "narration.line" for e in payload["events"])


def test_textarena_probe_cli_reports_environment():
    from services.match.engine import textarena_adapter
    from services.match.engine import textarena_probe

    if not textarena_adapter.is_available():
        pytest.skip("未安装 textarena")
    outcome = textarena_probe.probe("TicTacToe-v0")
    assert outcome["ok"] is True, outcome["reason"]
    assert outcome["action"]


def test_demo_main_prints_timeline(tmp_path, capsys, monkeypatch):
    from services.match import demo

    monkeypatch.setattr("sys.argv", ["demo", "--seed", "11", "--db", str(tmp_path / "demo.db")])
    assert demo.main() == 0
    out = capsys.readouterr().out
    assert "种子 11" in out
    assert "结束：" in out


def test_probe_main_reports_single_env(capsys):
    from services.match.engine import textarena_adapter
    from services.match.engine import textarena_probe

    if not textarena_adapter.is_available():
        pytest.skip("未安装 textarena")
    code = textarena_probe.main(["probe", "TicTacToe-v0"])
    assert code == 0
    out = capsys.readouterr().out
    assert "TicTacToe-v0" in out
    assert "可用" in out


# ---------- API 边界：渲染 / 剪辑下载 / 调度接口 ----------

def test_render_endpoint_returns_503_without_ffmpeg(client, monkeypatch):
    from services.match import api as api_module

    monkeypatch.setattr(api_module, "find_ffmpeg", lambda: None)
    response = client.post("/matches/whatever/clips/render")
    assert response.status_code == 503
    assert "ffmpeg" in response.text


def test_clip_download_rejects_path_traversal(client):
    assert client.get("/matches/m1/clips/..%2f..%2fetc%2fpasswd").status_code in {400, 404}
    assert client.get("/matches/m1/clips/nope.mp4").status_code == 404


def test_scheduler_tick_requires_configuration(client):
    response = client.post("/scheduler/tick")
    assert response.status_code == 400
    assert "MELEE_SCHEDULE" in response.text


def test_parse_start_at_accepts_unix_iso_and_empty():
    from services.match.api import _parse_start_at

    assert _parse_start_at(None) is None
    assert _parse_start_at("") is None
    assert _parse_start_at(1_700_000_000) == 1_700_000_000.0
    assert _parse_start_at("1700000000") == 1_700_000_000.0
    assert _parse_start_at("2026-10-22T00:00:00Z") == 1792627200.0
    # 无时区的 ISO 按 UTC 解释
    assert _parse_start_at("2026-10-22T00:00:00") == 1792627200.0


def test_visibility_filter():
    from services.match.api import visible

    assert visible({"visibility": "public"}, None)
    assert visible({}, "A")
    assert visible({"visibility": {"players": ["A"]}}, "A")
    assert not visible({"visibility": {"players": ["A"]}}, "B")
    assert not visible({"visibility": {"players": ["A"]}}, None)
    # 结构异常时按可见处理（不在读路径上 fail-closed，避免整场消失）
    assert visible({"visibility": "weird"}, None)


def test_backup_script_roundtrip(tmp_path):
    """备份脚本产出的文件必须能直接打开并读到数据。"""
    import subprocess
    import sys

    import sqlite3

    db = tmp_path / "m.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(a); INSERT INTO t VALUES(1);")
    conn.commit()
    conn.close()

    out = tmp_path / "backups"
    result = subprocess.run(
        [sys.executable, "scripts/backup.py", "--db", str(db), "--out", str(out), "--keep", "2"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    files = sorted(out.glob("*.db"))
    assert len(files) == 1
    restored = sqlite3.connect(files[0])
    try:
        assert restored.execute("SELECT * FROM t").fetchall() == [(1,)]
    finally:
        restored.close()


def test_scheduler_runs_job_and_records_errors(tmp_path, monkeypatch):
    """调度 tick 内部抛错时要记录到 last_error，且不中断其它赛季。"""
    from services.match.scheduler import ScheduleConfig, SeasonScheduler

    calls: list[str] = []

    def boom(season_id, **_kwargs):
        calls.append(season_id)
        raise RuntimeError("赛季坏了")

    scheduler = SeasonScheduler(
        ScheduleConfig(season_ids=["s1", "s2"], interval_s=60, per_tick=1, enabled=True),
        tmp_path / "x.db",
        run=boom,
    )
    assert scheduler.tick() == []
    assert calls == ["s1", "s2"]
    assert scheduler.last_error == "赛季坏了"
    assert scheduler.ticks == 1


def test_render_clip_without_ffmpeg_raises(tmp_path):
    from services.match.clips import Clip
    from services.match.render import render_clip

    clip = Clip(kind="x", title="t", detail="d", startSeq=0, endSeq=0, events=[], durationMs=100)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        render_clip(clip, tmp_path / "x.mp4", ffmpeg="")


def test_render_clips_batch_renders_each(tmp_path, monkeypatch):
    """批量渲染：每段都调用一次，文件名带序号和 kind。"""
    from services.match.clips import Clip
    from services.match import render as render_module

    calls = []

    def fake_render(clip, out_path, **_kwargs):
        calls.append(Path(out_path).name)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"x")
        return render_module.RenderResult(path=out_path, frames=1, duration_ms=100, bytes=1)

    monkeypatch.setattr(render_module, "render_clip", fake_render)
    clips = [
        Clip(kind="guess", title="a", detail="", startSeq=0, endSeq=2, events=[], durationMs=100),
        Clip(kind="end", title="b", detail="", startSeq=3, endSeq=5, events=[], durationMs=100),
    ]
    render_module.render_clips(clips, tmp_path, prefix="clip")
    assert calls == ["clip-01-guess.mp4", "clip-02-end.mp4"]


def test_frame_renders_codename_and_board_events():
    """帧绘制不能因为缺字段抛异常：三种赛制的关键事件都要能画。"""
    from services.match.render import build_context, draw_frame

    events = [
        {"seq": 0, "matchId": "m", "type": "match.created", "at": "2026-01-01T00:00:00Z",
         "payload": {"game": "codenames", "players": ["A", "B", "C", "D"], "seed": 1}},
        {"seq": 1, "matchId": "m", "type": "state.changed", "at": "2026-01-01T00:00:01Z",
         "payload": {"publicState": {"remaining": {"red": 8, "blue": 7}, "board": [], "turn": "red",
                                      "phase": "hint", "hint": None, "turnIndex": 1, "history": [
                                          {"by": "A", "kind": "hint", "value": "海 2"}]}}},
        {"seq": 2, "matchId": "m", "type": "match.ended", "at": "2026-01-01T00:00:02Z",
         "payload": {"winner": "red", "reason": "翻完全部己方词", "finalState": {}}},
    ]
    context = build_context(events)
    assert context["gameKind"] == "codenames"
    for event in events:
        image = draw_frame(event, context={**context, "index": 0})
        assert image.size == (1600, 900)
