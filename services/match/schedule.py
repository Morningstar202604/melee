"""赛程推进：把 fixtures 按轮次跑完，并把结果写回赛季。

可断点续跑：只跑还没有结果的比赛。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents.scripted import BoardAgent, GreedyAgent
from .director import Director
from .engine import registry as game_registry
from .engine.twenty_questions import CATALOG
from .eventlog import EventLog
from .narrator import narrator_from_env
from .runner import Runner
from .season import Season, persist


def game_factory(game_id: str) -> Any:
    """按赛季赛制建引擎；未注册的赛制直接报错，而不是偷偷退回二十问。"""
    return game_registry.get(game_id)


def default_agent(player_id: str, game_id: str) -> Any:
    """默认选手按赛制分派，与 worker 的默认保持一致。"""
    if game_id == "twenty-questions":
        return GreedyAgent(player_id, CATALOG)
    if game_id == "codenames":
        from .agents.scripted import CodenamesAgent

        return CodenamesAgent(player_id)
    return BoardAgent(player_id, game_id)


class SeasonBusyError(RuntimeError):
    """同一赛季已有别的执行体在推进。"""


@dataclass
class MatchOutcome:
    matchId: str
    round: int
    a: str
    b: str
    winner: str | None
    reason: str
    events: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "matchId": self.matchId,
            "round": self.round,
            "a": self.a,
            "b": self.b,
            "winner": self.winner,
            "reason": self.reason,
            "events": self.events,
        }


@dataclass
class SeasonRun:
    season_id: str
    outcomes: list[MatchOutcome] = field(default_factory=list)
    skipped: int = 0

    @property
    def played(self) -> int:
        return len(self.outcomes)


LOCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS season_locks (
  season_id TEXT PRIMARY KEY,
  owner     TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


def _acquire_season_lock(conn: sqlite3.Connection, season_id: str, owner: str, ttl_s: float = 600.0) -> bool:
    """按赛季加一把数据库锁，避免两个副本/两次点击同时跑同一批 fixture。

    进程内的调度器锁挡不住多副本，也挡不住"手动推进 + 定时器"并发；
    这把锁在 SQLite 里，条件是"没有锁 or 锁已过期"。
    """
    conn.executescript(LOCK_SCHEMA)
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO season_locks (season_id, owner, expires_at) VALUES (?,?,?)",
        (season_id, owner, now + ttl_s),
    )
    cursor = conn.execute(
        "UPDATE season_locks SET owner = ?, expires_at = ? "
        "WHERE season_id = ? AND (owner = ? OR expires_at < ?)",
        (owner, now + ttl_s, season_id, owner, now),
    )
    conn.commit()
    return cursor.rowcount > 0


def _release_season_lock(conn: sqlite3.Connection, season_id: str, owner: str) -> None:
    conn.execute("DELETE FROM season_locks WHERE season_id = ? AND owner = ?", (season_id, owner))
    conn.commit()


def _played_matches(season: Season) -> set[tuple[int, str, str]]:
    done = {(r["playerA"], r["playerB"]) for r in season.results}
    done |= {(r["playerB"], r["playerA"]) for r in season.results}
    return {(round_no, a, b) for round_no, a, b, _scheduled in season.fixtures if (a, b) in done}


def run_season(
    season_id: str,
    *,
    db_path: str | Path,
    agent_factory: Callable[[str], Any] | None = None,
    max_fixtures: int | None = None,
    on_match: Callable[[MatchOutcome], None] | None = None,
) -> SeasonRun:
    """按轮次跑完剩余赛程。

    agent_factory 默认给每个选手一个贪心算法；接真实模型时传自己的工厂。
    """
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    owner = f"{os.getpid()}-{threading.get_ident()}"
    if not _acquire_season_lock(conn, season_id, owner):
        raise SeasonBusyError(f"赛季 {season_id} 正在被另一个执行体推进")
    try:
        season = Season(season_id, "")
        try:
            season = _load_without_raising(conn, season_id)
        except KeyError:
            raise

        finished = _played_matches(season)
        run = SeasonRun(season_id=season_id)

        for round_no, a, b, scheduled in season.fixtures:
            if max_fixtures is not None and run.played >= max_fixtures:
                break
            # 排了时间就等到点；没排就立刻可跑（手动触发场景）。
            if scheduled is not None and scheduled > time.time():
                break
            if (round_no, a, b) in finished:
                run.skipped += 1
                continue

            match_id = f"{season_id}_r{round_no}_{a}_{b}"
            # 赛季可以指定赛制：不同赛制的比分不能混进同一张榜。
            game = game_factory(season.game)
            config = {"maxQuestions": 21} if season.game == "twenty-questions" else {}
            # 种子必须对任意选手名稳定：ord() 只接受单字符，中文名会直接崩。
            state = game.init(
                seed=_stable_seed(round_no, a, b), players=[a, b], config=config
            )

            log = EventLog.open(db_path, match_id)
            # 重跑同一 fixture 时不能重置 matches 行（会抹掉 ended_at），
            # create_match 内部已是 INSERT OR IGNORE。
            log.create_match(game=game.game_id, seed=state["seed"], players=[a, b])

            factory = agent_factory or (lambda pid: default_agent(pid, season.game))
            agents = {a: factory(a), b: factory(b)}
            result = Runner(game, log, agents, narrator=narrator_from_env(), director=Director()).play(state)
            events = log.read()
            log.close()

            season.record(match_id, a, b, result.winner, result.endReason)
            persist(conn, season)

            outcome = MatchOutcome(
                matchId=match_id,
                round=round_no,
                a=a,
                b=b,
                winner=result.winner,
                reason=result.endReason,
                events=len(events),
            )
            run.outcomes.append(outcome)
            if on_match is not None:
                on_match(outcome)

        return run
    finally:
        try:
            _release_season_lock(conn, season_id, owner)
        finally:
            conn.close()


def _stable_seed(round_no: int, a: str, b: str) -> int:
    """由轮次与两个选手名生成稳定种子（同参数必得同种子）。

    用位置加权哈希，不用 Python 的 hash()——后者每次进程启动都变，
    会让"同一赛季重跑得到同样结果"这条保证失效。
    """
    total = round_no * 1000
    for index, name in enumerate((a, b)):
        acc = 0
        for ch in name:
            acc = (acc * 131 + ord(ch)) % 100000
        total += acc * (17 if index == 0 else 43)
    return total % 2_147_483_647


def _load_without_raising(conn: sqlite3.Connection, season_id: str) -> Season:
    from .season import load

    return load(conn, season_id)
