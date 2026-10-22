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
    for earlier, later in zip(ordered, ordered[1:]):
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
