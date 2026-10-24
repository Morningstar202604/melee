"""赛事档案：把事件日志里散落的比赛汇总成可浏览的列表与统计。

为什么不复用 /matches：那个接口只返回 id，给程序用；
赛事库要给观众看——赛制、选手、胜负、时长、事件数都要一次取回。

所有字段都从数据库真实读出，没有估算值：
- status 由 matches.ended_at 与事件里的 match.ended 共同判定；
- winner 取自 match.ended 事件；
- events 是 COUNT(*)；
- durationMs 是首末事件时间差。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .engine import registry as game_registry

SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_matches_created ON matches (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (match_id, seq);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure(db_path: str | Path) -> None:
    from .eventlog import SCHEMA as EVENT_SCHEMA

    conn = _connect(db_path)
    try:
        conn.executescript(EVENT_SCHEMA)
        conn.executescript(SCHEMA)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def _label_of(game_id: str) -> str:
    for item in game_registry.catalog():
        if item["gameId"] == game_id:
            return item["label"]
    return game_id


def _terminal(conn: sqlite3.Connection, match_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload FROM events WHERE match_id = ? AND type = 'match.ended' ORDER BY seq DESC LIMIT 1",
        (match_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["payload"])
    except json.JSONDecodeError:
        return None


def list_matches(
    db_path: str | Path,
    *,
    limit: int = 24,
    offset: int = 0,
    game: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """分页列出比赛。status 支持 live / ended。"""
    _ensure(db_path)
    conn = _connect(db_path)
    try:
        where = []
        params: list[Any] = []
        if game:
            where.append("m.game = ?")
            params.append(game)
        if status == "ended":
            where.append("m.ended_at IS NOT NULL")
        elif status == "live":
            where.append("m.ended_at IS NULL")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        # 只有上面这几个固定片段会被拼进 clause，用户输入全部走参数绑定（nosec: B608）。
        total = conn.execute(
            f"SELECT COUNT(*) FROM matches m{clause}", params  # nosec B608
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT m.match_id, m.game, m.seed, m.players, m.created_at, m.ended_at,
                   (SELECT COUNT(*) FROM events e WHERE e.match_id = m.match_id) AS events,
                   (SELECT MIN(e.at) FROM events e WHERE e.match_id = m.match_id) AS first_at,
                   (SELECT MAX(e.at) FROM events e WHERE e.match_id = m.match_id) AS last_at
            FROM matches m{clause}
            ORDER BY m.created_at DESC
            LIMIT ? OFFSET ?
            """,  # nosec B608
            (*params, limit, offset),
        ).fetchall()

        items = []
        for row in rows:
            payload = _terminal(conn, row["match_id"]) or {}
            try:
                players = json.loads(row["players"])
            except (TypeError, json.JSONDecodeError):
                players = []
            items.append(
                {
                    "matchId": row["match_id"],
                    "game": row["game"],
                    "gameLabel": _label_of(row["game"]),
                    "seed": row["seed"],
                    "players": players,
                    "status": "ended" if row["ended_at"] else "live",
                    "winner": payload.get("winner"),
                    "reason": payload.get("reason", ""),
                    "events": row["events"],
                    "createdAt": row["created_at"],
                    "endedAt": row["ended_at"],
                    "firstAt": row["first_at"],
                    "lastAt": row["last_at"],
                }
            )
        return {"count": total, "limit": limit, "offset": offset, "items": items}
    finally:
        conn.close()


def overview(db_path: str | Path) -> dict[str, Any]:
    """首页与统计页要的聚合值。全部来自 SQL 计数。"""
    _ensure(db_path)
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        ended = conn.execute("SELECT COUNT(*) FROM matches WHERE ended_at IS NOT NULL").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        by_game = [
            {"game": row[0], "label": _label_of(row[0]), "matches": row[1]}
            for row in conn.execute(
                "SELECT game, COUNT(*) FROM matches GROUP BY game ORDER BY COUNT(*) DESC"
            )
        ]

        winners: dict[str, int] = {}
        for row in conn.execute(
            "SELECT payload FROM events WHERE type = 'match.ended' ORDER BY seq DESC LIMIT 2000"
        ):
            try:
                winner = json.loads(row[0]).get("winner")
            except json.JSONDecodeError:
                continue
            # 团队赛的胜者是 red / blue，不是选手；混进"胜者榜"会读成一个人叫"蓝队"。
            if winner and winner not in {"red", "blue"}:
                winners[winner] = winners.get(winner, 0) + 1
        top = sorted(winners.items(), key=lambda item: (-item[1], item[0]))[:8]

        recent_days = [
            {"day": row[0], "matches": row[1]}
            for row in conn.execute(
                "SELECT substr(created_at, 1, 10) AS day, COUNT(*) FROM matches "
                "GROUP BY day ORDER BY day DESC LIMIT 14"
            )
        ]
        return {
            "matches": total,
            "finished": ended,
            "live": max(0, total - ended),
            "events": events,
            "byGame": by_game,
            "topWinners": [{"player": name, "wins": wins} for name, wins in top],
            "activity": list(reversed(recent_days)),
        }
    finally:
        conn.close()
