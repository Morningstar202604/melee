"""编队：把「一组 AI」解析成可执行的对局席位。

为什么需要单独一层：
- 赛制只知道"需要几个位置、每个位置什么角色"，不应该知道选手叫什么；
- 调用方（API / 赛季 / 脚本）想自定义"红队两名队长 + 三名队员"这种编制；
- 同一套编队要能表达 2 人、4 人、6 人、8 人，而不是每个赛制写死人数。

三个概念严格分开，这是公平性的基础：
- **player**：一个 AI 的身份（模型、密钥、人格），由调用方给；
- **seat**：赛制里的一个位置（红队队长 #1、蓝队队员 #2……），由规则给；
- **team**：席位所属阵营，决定信息可见性与胜负归属。

编队解析只做"把 N 个 player 放进 M 个 seat"，不涉及任何隐藏信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence


class RosterError(ValueError):
    """编队非法：人数、角色、队伍配置有冲突。"""


@dataclass(frozen=True)
class Seat:
    """赛制的一个位置。`key` 在单场对局内唯一。"""

    key: str
    team: str
    role: str

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "team": self.team, "role": self.role}


@dataclass
class Roster:
    """解析结果：玩家 → 席位的映射，以及按队伍分组的席位。"""

    players: list[str]
    seats: list[Seat]
    by_player: dict[str, Seat] = field(default_factory=dict)
    by_team: dict[str, list[Seat]] = field(default_factory=dict)

    def team_of(self, player: str) -> str | None:
        seat = self.by_player.get(player)
        return seat.team if seat else None

    def teammates_of(self, player: str) -> list[str]:
        """同队其他选手（不含自己）。"""
        team = self.team_of(player)
        if team is None:
            return []
        return [p for p, seat in self.by_player.items() if seat.team == team and p != player]

    def captains_of(self, team: str) -> list[str]:
        return [p for p, seat in self.by_player.items() if seat.team == team and seat.role == "captain"]


def build_two_sided_roster(players: Sequence[str]) -> Roster:
    """两人赛制：交替分边，先手由调用方按 seed 决定（这里只做席位）。"""
    if len(players) < 2:
        raise RosterError("至少需要 2 名选手")
    seats = [
        Seat(key=f"P{index}", team=f"side{index % 2}", role="player")
        for index in range(len(players))
    ]
    return _assemble(players, seats)


def build_codenames_roster(
    players: Sequence[str],
    *,
    teams: int = 2,
    captains_per_team: int = 1,
    agents_per_team: int = 1,
) -> Roster:
    """代号编队：N 队 × (队长数 + 队员数)。

    默认 2 队 × (1 队长 + 1 队员) = 4 人，与旧行为一致；
    想打 3 队或每队 2 名队员，只改参数，不需要改引擎。

    队长看得到配色、队员看不到——这是本赛制的隐藏信息核心，
    所以角色必须在席位里显式表达，不能靠"第 0 个是队长"这种隐含约定。
    """
    if teams < 2:
        raise RosterError("代号至少需要 2 支队伍")
    if captains_per_team < 1:
        raise RosterError("每队至少需要 1 名队长")
    if agents_per_team < 1:
        raise RosterError("每队至少需要 1 名队员")
    needed = teams * (captains_per_team + agents_per_team)
    if len(players) != needed:
        raise RosterError(f"{teams} 队 × ({captains_per_team} 队长 + {agents_per_team} 队员) 需要 {needed} 名选手，收到 {len(players)}")

    seats: list[Seat] = []
    cursor = 0
    for team_index in range(teams):
        team = _team_name(team_index)
        for captain_index in range(captains_per_team):
            seats.append(Seat(key=f"{team}-captain{captain_index + 1}", team=team, role="captain"))
            cursor += 1
        for agent_index in range(agents_per_team):
            seats.append(Seat(key=f"{team}-agent{agent_index + 1}", team=team, role="agent"))
            cursor += 1
    return _assemble(players, seats)


def team_names(count: int) -> list[str]:
    """给前端展示用的队伍名。超过预置色板时自动编号，不写死上限。"""
    return [_team_name(index) for index in range(count)]


_PALETTE = ("red", "blue", "green", "yellow", "purple", "orange", "cyan", "pink")


def _team_name(index: int) -> str:
    if index < len(_PALETTE):
        return _PALETTE[index]
    return f"team{index + 1}"


def _assemble(players: Iterable[str], seats: list[Seat]) -> Roster:
    players = list(players)
    if len(set(players)) != len(players):
        raise RosterError("选手 id 必须唯一")
    if len(players) != len(seats):
        raise RosterError(f"席位 {len(seats)} 个，选手 {len(players)} 名，数量必须一致")
    roster = Roster(players=players, seats=seats)
    for player, seat in zip(players, seats, strict=True):
        roster.by_player[player] = seat
        roster.by_team.setdefault(seat.team, []).append(seat)
    return roster


def describe(roster: Roster) -> list[dict[str, str]]:
    """给 API/前端的安全视图：只含身份与角色，不含任何隐藏信息。"""
    return [
        {"player": player, **seat.as_dict()}
        for player, seat in roster.by_player.items()
    ]
