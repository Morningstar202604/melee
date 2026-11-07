"""编队解析：N 队 / 多角色，且不泄露任何隐藏信息。"""

from __future__ import annotations

import pytest

from services.match.roster import (
    RosterError,
    build_codenames_roster,
    build_two_sided_roster,
    describe,
    team_names,
)


def test_two_sided_roster_alternates_sides():
    roster = build_two_sided_roster(["A", "B", "C", "D"])
    assert [roster.team_of(p) for p in roster.players] == ["side0", "side1", "side0", "side1"]
    assert len(roster.seats) == 4


def test_codenames_default_matches_legacy_four_players():
    """默认参数必须与旧的 4 人行为一致，不能因为重构改变线上语义。"""
    roster = build_codenames_roster(["r1", "b1", "r2", "b2"])
    assert [s.team for s in roster.seats] == ["red", "red", "blue", "blue"]
    assert [s.role for s in roster.seats] == ["captain", "agent", "captain", "agent"]
    assert roster.captains_of("red") == ["r1"]


def test_codenames_supports_three_teams():
    """三队编队：这是"不止红蓝两端"的最小证明。"""
    players = [f"p{i}" for i in range(6)]
    roster = build_codenames_roster(players, teams=3, captains_per_team=1, agents_per_team=1)
    assert set(roster.by_team) == {"red", "blue", "green"}
    assert len(roster.seats) == 6
    for team in ("red", "blue", "green"):
        assert len(roster.captains_of(team)) == 1


def test_codenames_supports_multiple_agents_per_team():
    players = [f"p{i}" for i in range(8)]
    roster = build_codenames_roster(players, teams=2, captains_per_team=1, agents_per_team=3)
    assert len(roster.by_team["red"]) == 4
    assert roster.teammates_of("p0") == ["p1", "p2", "p3"]


def test_codenames_supports_multiple_captains():
    """每队 2 队长 + 1 队员 = 3 人：席位按队伍连续分配。

    red  = p0(队长1) p1(队长2) p2(队员)
    blue = p3(队长1) p4(队长2) p5(队员)
    """
    players = [f"p{i}" for i in range(6)]
    roster = build_codenames_roster(players, teams=2, captains_per_team=2, agents_per_team=1)
    assert roster.captains_of("red") == ["p0", "p1"]
    assert roster.captains_of("blue") == ["p3", "p4"]
    assert roster.teammates_of("p0") == ["p1", "p2"]


def test_roster_rejects_wrong_player_count():
    with pytest.raises(RosterError, match="需要"):
        build_codenames_roster(["a", "b", "c"], teams=2)


def test_roster_rejects_duplicate_ids():
    with pytest.raises(RosterError, match="唯一"):
        build_two_sided_roster(["A", "A"])


def test_roster_rejects_single_team():
    with pytest.raises(RosterError, match="至少需要 2 支队伍"):
        build_codenames_roster(["a", "b"], teams=1)


def test_describe_contains_no_hidden_information():
    """编队视图给前端/日志用：只能有身份与角色，不能有配色或手牌。"""
    roster = build_codenames_roster(["r1", "b1", "r2", "b2"])
    rows = describe(roster)
    assert len(rows) == 4
    text = repr(rows)
    for forbidden in ("secretColors", "colors", "target", "player_cards", "player_hands"):
        assert forbidden not in text


def test_team_names_extend_beyond_palette():
    """超过 8 队也不能崩：自动编号。"""
    names = team_names(10)
    assert len(names) == 10
    assert len(set(names)) == 10
    assert names[8] == "team9"


def test_teammates_never_include_other_teams():
    players = [f"p{i}" for i in range(6)]
    roster = build_codenames_roster(players, teams=3, captains_per_team=1, agents_per_team=1)
    for player in players:
        for mate in roster.teammates_of(player):
            assert roster.team_of(mate) == roster.team_of(player)
            assert mate != player
