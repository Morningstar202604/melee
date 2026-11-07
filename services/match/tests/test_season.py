from __future__ import annotations

import sqlite3

from services.match.season import Season, load, persist


def build(n: int = 8) -> Season:
    season = Season("s1", "2026 秋季")
    for index in range(n):
        pid = chr(ord("A") + index)
        season.add_player(pid, f"选手{pid}", persona=f"{pid} 的风格", model=f"model-{pid}")
    season.build_round_robin()
    return season


def test_round_robin_pair_count():
    season = build(8)
    assert len(season.fixtures) == 28


def test_round_robin_no_repeat_in_same_round():
    season = build(8)
    by_round: dict[int, list[str]] = {}
    for round_no, a, b, _scheduled in season.fixtures:
        by_round.setdefault(round_no, []).extend([a, b])
    for round_no, players in by_round.items():
        assert len(players) == len(set(players)), f"第 {round_no} 轮有人重复出场"


def test_round_robin_covers_every_pair_once():
    season = build(8)
    pairs = {tuple(sorted((a, b))) for _r, a, b, _s in season.fixtures}
    assert len(pairs) == 28


def test_stands_are_computed_from_results():
    season = build(4)
    season.record("m1", "A", "B", "A")
    season.record("m2", "C", "D", "C")
    season.record("m3", "A", "C", "A")

    table = {s.player_id: s for s in season.standings()}
    assert table["A"].wins == 2
    assert table["B"].losses == 1
    assert table["C"].wins == 1 and table["C"].losses == 1
    assert table["A"].rating > table["B"].rating


def test_standings_are_idempotent():
    season = build(4)
    season.record("m1", "A", "B", "A")
    first = [s.as_dict() for s in season.standings()]
    second = [s.as_dict() for s in season.standings()]
    assert first == second


def test_recording_same_match_twice_does_not_double_count():
    season = build(4)
    season.record("m1", "A", "B", "A")
    season.record("m1", "A", "B", "B")
    table = {s.player_id: s for s in season.standings()}
    assert table["B"].wins == 1
    assert table["A"].losses == 1


def test_rivalries_need_repeated_meetings():
    season = build(4)
    season.record("m1", "A", "B", "A")
    assert season.rivalries(min_games=2) == []
    season.record("m2", "A", "B", "A")
    rivalries = season.rivalries(min_games=2)
    assert len(rivalries) == 1
    assert rivalries[0]["games"] == 2
    assert rivalries[0]["leador"] == "A"


def test_persist_and_load_roundtrip(tmp_path):
    path = tmp_path / "season.db"
    conn = sqlite3.connect(path)
    season = build(4)
    season.record("m1", "A", "B", "A", "猜中")
    persist(conn, season)

    loaded = load(conn, "s1")
    conn.close()

    assert loaded.name == "2026 秋季"
    assert set(loaded.roster) == {"A", "B", "C", "D"}
    assert len(loaded.fixtures) == 6
    assert loaded.results[0]["winner"] == "A"


def test_loaded_season_recomputes_same_standings(tmp_path):
    path = tmp_path / "season.db"
    conn = sqlite3.connect(path)
    season = build(4)
    season.record("m1", "A", "B", "A")
    season.record("m2", "C", "D", "C")
    persist(conn, season)
    original = [s.as_dict() for s in season.standings()]

    loaded = load(conn, "s1")
    conn.close()
    assert [s.as_dict() for s in loaded.standings()] == original


def test_draw_is_ranked_equal():
    season = build(4)
    season.record("m1", "A", "B", None)
    table = {s.player_id: s for s in season.standings()}
    assert table["A"].draws == 1
    assert table["B"].draws == 1
    assert table["A"].rating == table["B"].rating


def test_round_robin_without_schedule_has_no_times():
    season = build(4)
    assert all(scheduled is None for _r, _a, _b, scheduled in season.fixtures)


def test_round_robin_schedules_rounds_apart():
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin(start_at=1_000.0, interval_minutes=30)

    by_round: dict[int, list[float]] = {}
    for round_no, _a, _b, scheduled in season.fixtures:
        by_round.setdefault(round_no, []).append(scheduled)

    # 同一轮同时开赛
    for times in by_round.values():
        assert len(set(times)) == 1
    # 相邻两轮差 30 分钟
    ordered = [by_round[r][0] for r in sorted(by_round)]
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        assert later - earlier == 1800.0


def test_schedule_survives_persist(tmp_path):
    path = tmp_path / "s.db"
    conn = sqlite3.connect(path)
    season = Season("s1", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin(start_at=2_000.0, interval_minutes=15)
    persist(conn, season)

    loaded = load(conn, "s1")
    conn.close()
    assert [f[3] for f in loaded.fixtures] == [f[3] for f in season.fixtures]
    assert all(f[3] is not None for f in loaded.fixtures)


# ---------- 调度器自动发现 ----------
# 之前调度器必须用 MELEE_SCHEDULE 点名才启用，导致"建了带 startAt 的赛季却永远
# 不开赛"。现在默认扫描到点赛季；这三条测试锁住新契约。


def _seed_db(tmp_path):
    path = tmp_path / "sched.db"
    conn = sqlite3.connect(path)
    season = Season("due-1", "到期赛季")
    for pid in ("A", "B", "C", "D"):
        season.add_player(pid, pid)
    season.build_round_robin(start_at=100.0, interval_minutes=15)
    persist(conn, season)
    conn.close()
    return path


def test_due_season_ids_finds_only_due_scheduled_seasons(tmp_path):
    from services.match.schedule import due_season_ids

    path = _seed_db(tmp_path)
    # 全部场次排在 100s 起：以 200s 为"现在"，赛季到期。
    assert due_season_ids(path, now=200.0) == ["due-1"]
    # 排期开始前不到期。
    assert due_season_ids(path, now=50.0) == []
    # 全部打完后不再到期。
    conn = sqlite3.connect(path)
    conn.execute("UPDATE fixtures SET match_id = 'm1' WHERE season_id = 'due-1'")
    conn.commit()
    conn.close()
    assert due_season_ids(path, now=200.0) == []


def test_due_season_ids_ignores_manual_seasons(tmp_path):
    """没给 startAt 的赛季必须保持纯手动：调度器不能替用户开赛。"""
    from services.match.schedule import due_season_ids

    path = tmp_path / "manual.db"
    conn = sqlite3.connect(path)
    season = Season("manual-1", "手动赛季")
    for pid in ("A", "B", "C", "D"):
        season.add_player(pid, pid)
    season.build_round_robin()  # 不给 start_at
    persist(conn, season)
    conn.close()
    assert due_season_ids(path, now=10**9) == []


def test_scheduler_config_from_env_matrix(monkeypatch):
    from services.match.scheduler import ScheduleConfig
    from services.match.settings import reset_settings

    monkeypatch.delenv("MELEE_SCHEDULE", raising=False)
    reset_settings()
    config = ScheduleConfig.from_env()
    assert config.enabled and config.season_ids is None  # 默认自动发现

    monkeypatch.setenv("MELEE_SCHEDULE", "a,b")
    reset_settings()
    config = ScheduleConfig.from_env()
    assert config.enabled and config.season_ids == ["a", "b"]  # 点名成为过滤

    monkeypatch.setenv("MELEE_SCHEDULE", "off")
    reset_settings()
    config = ScheduleConfig.from_env()
    assert not config.enabled


def test_scheduler_tick_auto_discovers_due_season(tmp_path):
    """tick 在未点名时必须自己找到期赛季并推进。"""
    from services.match.scheduler import ScheduleConfig, SeasonScheduler

    path = _seed_db(tmp_path)
    calls = []

    def fake_run(season_id, *, db_path, max_fixtures=None):
        calls.append(season_id)

        class _Result:
            outcomes = []

        return _Result()

    scheduler = SeasonScheduler(
        ScheduleConfig(season_ids=None, per_tick=2),
        path,
        run=fake_run,
        scheduler=_IdleScheduler(),
    )
    scheduler.tick()
    assert calls == ["due-1"], "未点名时必须自动发现到期赛季"


class _IdleScheduler:
    """tick 不需要真实 APScheduler；给个空壳满足构造与 running 属性。"""

    running = False

    def get_job(self, _job_id):
        return None

    def shutdown(self, wait=False):
        return None


# ---------- 双循环与宿敌 ----------
# 单循环下任意人数都凑不满 rivalries 的"≥2 场"阈值，界面上宿敌永远为空。
# 这不是文案问题，是功能不可达：双循环让面板真的有数据。


def test_double_round_robin_doubles_every_pairing():
    from services.match.season import Season

    season = Season("d", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin()
    single = len(season.fixtures)
    season.build_round_robin(double=True)
    assert len(season.fixtures) == single * 2 == 12

    pairs = {}
    for _round, a, b, _scheduled in season.fixtures:
        pairs[tuple(sorted((a, b)))] = pairs.get(tuple(sorted((a, b))), 0) + 1
    assert set(pairs.values()) == {2}, pairs


def test_double_round_robin_swaps_home_and_away():
    """次回合交换主客：整季的先手分布仍然对称。"""
    from services.match.season import Season

    season = Season("d", "x")
    for pid in "AB":
        season.add_player(pid, pid)
    season.build_round_robin(double=True)
    orders = {(a, b) for _r, a, b, _s in season.fixtures}
    assert orders == {("A", "B"), ("B", "A")}


def test_rivalries_appear_with_double_round_robin():
    from services.match.season import Season

    season = Season("d", "x")
    for pid in "ABCD":
        season.add_player(pid, pid)
    season.build_round_robin(double=True)
    for index, (_round, a, b, _scheduled) in enumerate(season.fixtures):
        season.record(f"m{index}", a, b, a if index % 2 == 0 else b, "test")
    rivalries = season.rivalries()
    assert rivalries, "双循环下宿敌面板必须有数据"
    assert all(r["games"] >= 2 for r in rivalries)
    assert len(rivalries) == 6
