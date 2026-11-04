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
from .season import load as _load


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


def due_season_ids(db_path: str | Path, *, now: float | None = None) -> list[str]:
    """有"已到点且未打"场次的赛季 id，按最早到期时间排序。

    这是调度器自动发现的依据：只认排了时间且到点的赛季——
    没给 startAt 的赛季保持纯手动（用户没要自动推进，就不能替他开赛）。
    """
    moment = time.time() if now is None else now
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        rows = conn.execute(
            """
            SELECT season_id, MIN(scheduled_at) AS due_at
            FROM fixtures
            WHERE match_id IS NULL AND scheduled_at IS NOT NULL AND scheduled_at <= ?
            GROUP BY season_id
            ORDER BY due_at
            """,
            (moment,),
        ).fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        # 全新部署还没有 fixtures 表：没有赛季，自然没有到点的。
        return []
    finally:
        conn.close()


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
        season = _load(conn, season_id)

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

            outcome = _play_fixture(
                season,
                round_no=round_no,
                a=a,
                b=b,
                db_path=db_path,
                agent_factory=agent_factory,
            )
            season.record(outcome.matchId, a, b, outcome.winner, outcome.reason)
            persist(conn, season)
            run.outcomes.append(outcome)
            if on_match is not None:
                on_match(outcome)

        return run
    finally:
        try:
            _release_season_lock(conn, season_id, owner)
        finally:
            conn.close()


def _play_fixture(
    season: Season,
    *,
    round_no: int,
    a: str,
    b: str,
    db_path: str | Path,
    agent_factory: Callable[[str], Any] | None,
) -> MatchOutcome:
    """跑一场 fixture，可断点续跑。

    日志已跑完 → 只读结果补记分（绝不重跑，否则追加第二条 match.created）；
    日志有前半段 → 重放状态继续，不重写 match.created。
    """
    match_id = f"{season.season_id}_r{round_no}_{a}_{b}"
    game = game_factory(season.game)
    config = {"maxQuestions": 21} if season.game == "twenty-questions" else {}
    existing = _existing_state(db_path, match_id)

    if existing.finished:
        return MatchOutcome(
            matchId=match_id,
            round=round_no,
            a=a,
            b=b,
            winner=existing.winner,
            reason=existing.reason,
            events=len(existing.events),
        )

    # 种子必须对任意选手名稳定：ord() 只接受单字符，中文名会直接崩。
    fresh = game.init(seed=_stable_seed(round_no, a, b), players=[a, b], config=config)
    state = existing.state if existing.state is not None else fresh

    log = EventLog.open(db_path, match_id)
    try:
        # 重跑同一 fixture 时不能重置 matches 行（会抹掉 ended_at），
        # create_match 内部已是 INSERT OR IGNORE。
        log.create_match(game=game.game_id, seed=fresh["seed"], players=[a, b])
        factory = agent_factory or (lambda pid: default_agent(pid, season.game))
        agents = {a: factory(a), b: factory(b)}
        result = Runner(
            game,
            log,
            agents,
            narrator=narrator_from_env(),
            director=Director(),
            resume=bool(existing.events),
        ).play_with_budget(state)
        events = log.read()
    finally:
        log.close()

    return MatchOutcome(
        matchId=match_id,
        round=round_no,
        a=a,
        b=b,
        winner=result.winner,
        reason=result.endReason,
        events=len(events),
    )


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


@dataclass
class _Existing:
    """同一 fixture 的既有日志状态，用于断点续跑。"""

    state: dict[str, Any] | None = None
    finished: bool = False
    winner: str | None = None
    reason: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


def _existing_state(db_path: str | Path, match_id: str) -> _Existing:
    """读同一 fixture 的既有事件日志。

    两种情况必须区分：
    - 日志已跑完（有 match.ended）：只补记分，绝不重跑；
    - 日志有前半段：从重放出来的状态继续，不重写 match.created。
    旧实现无条件 `game.init` 后重跑，重跑会写第二条 match.created，
    `rehydrate` 从此在同一场里看到两个种子，比赛永久损坏。
    """
    from .eventlog import read_match
    from .rehydrate import RehydrateError, rehydrate

    try:
        events = read_match(db_path, match_id)
    except Exception:  # noqa: BLE001 - 表还没建：全新库
        return _Existing()
    if not events:
        return _Existing()
    try:
        rebuilt = rehydrate(db_path, match_id)
    except RehydrateError as exc:
        # 有日志但重放不了 = 数据损坏。静默重开一局只会再追加一条
        # match.created，把日志写得更烂；这里必须让整个推进失败并暴露原因。
        raise RuntimeError(f"赛季对局 {match_id} 的日志无法重放：{exc}") from exc

    if not any(event["type"] == "match.ended" for event in events):
        return _Existing(state=rebuilt.state, events=events)

    ended = next((e for e in reversed(events) if e["type"] == "match.ended"), None)
    payload = ended["payload"] if ended else {}
    return _Existing(
        state=rebuilt.state,
        finished=True,
        winner=payload.get("winner"),
        reason=str(payload.get("reason", "")),
        events=events,
    )
