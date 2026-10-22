"""赛季：角色档案、循环赛程、积分。

积分用 OpenSkill（多对局更稳，且能给出不确定性），Elo 仅作展示。
所有更新都可从「已结束的比赛列表」幂等重算——这是验收条件之一。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from openskill.models import PlackettLuce


SCHEMA = """
CREATE TABLE IF NOT EXISTS seasons (
  season_id  TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rosters (
  season_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  display   TEXT NOT NULL,
  persona   TEXT NOT NULL DEFAULT '',
  model     TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (season_id, player_id)
);

CREATE TABLE IF NOT EXISTS fixtures (
  season_id    TEXT NOT NULL,
  round        INTEGER NOT NULL,
  match_id     TEXT,
  player_a     TEXT NOT NULL,
  player_b     TEXT NOT NULL,
  scheduled_at REAL,
  PRIMARY KEY (season_id, round, player_a, player_b)
);

CREATE TABLE IF NOT EXISTS results (
  season_id TEXT NOT NULL,
  match_id  TEXT PRIMARY KEY,
  player_a  TEXT NOT NULL,
  player_b  TEXT NOT NULL,
  winner    TEXT,
  reason    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_results_season ON results (season_id);
"""


@dataclass
class Standing:
    player_id: str
    display: str
    wins: int = 0
    losses: int = 0
    draws: int = 0
    rating: float = 25.0
    uncertainty: float = 8.333

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def win_rate(self) -> float:
        return 0.0 if not self.played else self.wins / self.played

    def as_dict(self) -> dict[str, Any]:
        return {
            "playerId": self.player_id,
            "display": self.display,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "played": self.played,
            "winRate": round(self.win_rate, 3),
            "rating": round(self.rating, 2),
            "uncertainty": round(self.uncertainty, 2),
        }


@dataclass
class Season:
    season_id: str
    name: str
    roster: dict[str, dict[str, str]] = field(default_factory=dict)
    # (round, player_a, player_b, scheduled_at 或 None)
    fixtures: list[tuple[int, str, str, float | None]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)

    def add_player(self, player_id: str, display: str, persona: str = "", model: str = "") -> None:
        self.roster[player_id] = {"display": display, "persona": persona, "model": model}

    def build_round_robin(self, *, start_at: float | None = None, interval_minutes: float | None = None) -> None:
        """单循环：每两人打一场。轮次用圆桌法排，保证同轮不重复出场。

        给了 start_at 就按轮次排期：同一轮同时开赛，轮与轮之间隔 interval_minutes。
        不给时间就只排对阵，由调用方手动触发。
        """
        players = list(self.roster)
        if len(players) % 2:
            players.append("__bye__")

        n = len(players)
        rounds: list[tuple[int, str, str, float | None]] = []
        round_numbers = 0
        for r in range(n - 1):
            round_numbers += 1
            for i in range(n // 2):
                a = players[i]
                b = players[n - 1 - i]
                if "__bye__" in (a, b):
                    continue
                scheduled = None
                if start_at is not None and interval_minutes is not None:
                    scheduled = start_at + (r * interval_minutes * 60)
                rounds.append((r + 1, a, b, scheduled))
            players = [players[0]] + [players[-1]] + players[1:-1]
        self.fixtures = rounds

    def record(self, match_id: str, player_a: str, player_b: str, winner: str | None, reason: str = "") -> None:
        self.results = [r for r in self.results if r["matchId"] != match_id]
        self.results.append(
            {
                "matchId": match_id,
                "playerA": player_a,
                "playerB": player_b,
                "winner": winner,
                "reason": reason,
            }
        )

    def standings(self) -> list[Standing]:
        model = PlackettLuce()
        ratings: dict[str, Any] = {pid: model.rating(name=pid) for pid in self.roster}
        table = {pid: Standing(player_id=pid, display=self.roster[pid]["display"]) for pid in self.roster}

        for result in self.results:
            a, b, winner = result["playerA"], result["playerB"], result["winner"]
            if a not in ratings or b not in ratings:
                continue

            if winner is None:
                new_a, new_b = model.rate([[ratings[a]], [ratings[b]]], ranks=[1, 1])
                table[a].draws += 1
                table[b].draws += 1
            elif winner == a:
                new_a, new_b = model.rate([[ratings[a]], [ratings[b]]])
                table[a].wins += 1
                table[b].losses += 1
            elif winner == b:
                new_b, new_a = model.rate([[ratings[b]], [ratings[a]]])
                table[b].wins += 1
                table[a].losses += 1
            else:
                continue

            ratings[a], ratings[b] = new_a[0], new_b[0]

        for pid, rating in ratings.items():
            table[pid].rating = float(rating.mu)
            table[pid].uncertainty = float(rating.sigma)

        return sorted(
            table.values(),
            key=lambda s: (-s.rating, -s.wins, s.display),
        )

    def rivalries(self, min_games: int = 2) -> list[dict[str, Any]]:
        """宿敌：交手次数达到阈值，且胜负差最大。"""
        pairs: dict[tuple[str, str], dict[str, int]] = {}
        for result in self.results:
            key = tuple(sorted((result["playerA"], result["playerB"])))
            entry = pairs.setdefault(key, {"games": 0, key[0]: 0, key[1]: 0})
            entry["games"] += 1
            if result["winner"] in key:
                entry[result["winner"]] += 1

        out = []
        for (a, b), stat in pairs.items():
            if stat["games"] < min_games:
                continue
            out.append(
                {
                    "a": a,
                    "b": b,
                    "games": stat["games"],
                    "aWins": stat[a],
                    "bWins": stat[b],
                    "leador": a if stat[a] >= stat[b] else b,
                }
            )
        return sorted(out, key=lambda x: (-x["games"], -abs(x["aWins"] - x["bWins"])))


def persist(conn: sqlite3.Connection, season: Season) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO seasons (season_id, name, created_at) VALUES (?,?,datetime('now'))",
        (season.season_id, season.name),
    )
    for pid, info in season.roster.items():
        conn.execute(
            "INSERT OR REPLACE INTO rosters (season_id, player_id, display, persona, model) VALUES (?,?,?,?,?)",
            (season.season_id, pid, info["display"], info["persona"], info["model"]),
        )
    for round_no, a, b, scheduled in season.fixtures:
        conn.execute(
            "INSERT OR REPLACE INTO fixtures (season_id, round, player_a, player_b, scheduled_at) "
            "VALUES (?,?,?,?,?)",
            (season.season_id, round_no, a, b, scheduled),
        )
    for result in season.results:
        conn.execute(
            "INSERT OR REPLACE INTO results (season_id, match_id, player_a, player_b, winner, reason) "
            "VALUES (?,?,?,?,?,?)",
            (
                season.season_id,
                result["matchId"],
                result["playerA"],
                result["playerB"],
                result["winner"],
                result["reason"],
            ),
        )
    conn.commit()


def load(conn: sqlite3.Connection, season_id: str) -> Season:
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT name FROM seasons WHERE season_id = ?", (season_id,)).fetchone()
    if row is None:
        raise KeyError(season_id)
    season = Season(season_id=season_id, name=row[0])

    for pid, display, persona, model in conn.execute(
        "SELECT player_id, display, persona, model FROM rosters WHERE season_id = ? ORDER BY player_id",
        (season_id,),
    ):
        season.add_player(pid, display, persona, model)

    season.fixtures = [
        (r, a, b, scheduled)
        for r, a, b, scheduled in conn.execute(
            "SELECT round, player_a, player_b, scheduled_at FROM fixtures "
            "WHERE season_id = ? ORDER BY round, player_a",
            (season_id,),
        )
    ]

    for match_id, a, b, winner, reason in conn.execute(
        "SELECT match_id, player_a, player_b, winner, reason FROM results WHERE season_id = ? ORDER BY match_id",
        (season_id,),
    ):
        season.results.append(
            {"matchId": match_id, "playerA": a, "playerB": b, "winner": winner, "reason": reason}
        )
    return season
