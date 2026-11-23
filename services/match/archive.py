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

from .db import connect

_MATCH_COLUMNS = """
        m.match_id, m.game, m.seed, m.players, m.created_at, m.ended_at,
        m.source, m.source_id, m.played_at,
        (SELECT COUNT(*) FROM events e WHERE e.match_id = m.match_id) AS events,
        (SELECT MIN(e.at) FROM events e WHERE e.match_id = m.match_id) AS first_at,
        (SELECT MAX(e.at) FROM events e WHERE e.match_id = m.match_id) AS last_at
    """

SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_matches_created ON matches (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created ON events (match_id, seq);
-- 排行榜要「所有 match.ended，按 seq 倒序取前 N」。没有这条复合索引时
-- 计划是 SCAN events + TEMP B-TREE 排序：事件表上万后，每次打开首页都要
-- 把所有事件读一遍再排序。type 在前是等值条件，seq 在后用于跳过排序。
CREATE INDEX IF NOT EXISTS idx_events_type_seq ON events (type, seq DESC);
"""


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return connect(db_path, timeout=5.0, rows=True)


def _ensure(db_path: str | Path) -> None:
    from .db import ensure_schema
    from .eventlog import SCHEMA as EVENT_SCHEMA
    from .migrations import migrate

    conn = _connect(db_path)
    try:
        ensure_schema(conn, db_path, EVENT_SCHEMA, "eventlog")
        ensure_schema(conn, db_path, SCHEMA, "archive")
        migrate(conn)
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()


def _label_of(game_id: str, lang: str = "en") -> str:
    """赛制展示名：按请求语言取，未登记的新赛制回退注册表。"""
    from .messages import game_label

    return game_label(game_id, lang)


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


def _terminals(conn: sqlite3.Connection, match_ids: list[str]) -> dict[str, dict[str, Any]]:
    """一次查出多场的终局 payload，避免每行再查一次（N+1）。

    同一场若有多条 match.ended（历史脏数据），取 seq 最大的一条，
    与单场查询的语义保持一致。
    """
    if not match_ids:
        return {}
    placeholders = ",".join("?" for _ in match_ids)
    rows = conn.execute(
        f"SELECT match_id, payload FROM events WHERE type = 'match.ended' "
        f"AND match_id IN ({placeholders}) ORDER BY match_id, seq",  # nosec B608 - 只有占位符是拼的
        match_ids,
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        try:
            out[row["match_id"]] = json.loads(row["payload"])
        except json.JSONDecodeError:
            continue
    return out


def _row_to_item(row: sqlite3.Row, payload: dict[str, Any], lang: str = "en") -> dict[str, Any]:
    try:
        players = json.loads(row["players"])
    except (TypeError, json.JSONDecodeError):
        players = []
    return {
        "matchId": row["match_id"],
        "game": row["game"],
        "gameLabel": _label_of(row["game"], lang),
        # 不回显 seed：它是隐藏信息的熵源（20Q 目标、代号配色、Kuhn 手牌），
        # 赛事库对所有观众开放，回显等于公开答案。
        "players": players,
        "status": "ended" if row["ended_at"] else "live",
        "winner": payload.get("winner"),
        "reason": payload.get("reason", ""),
        "events": row["events"],
        "createdAt": row["created_at"],
        "playedAt": row["played_at"] or row["created_at"],
        "source": row["source"] or "melee",
        "sourceId": row["source_id"] or "",
        "sourceLabel": (
            ("External import" if lang == "en" else "外部导入")
            if (row["source"] or "melee") != "melee"
            else ("Site match" if lang == "en" else "站内赛事")
        ),
        "endedAt": row["ended_at"],
        "firstAt": row["first_at"],
        "lastAt": row["last_at"],
    }


def list_matches(
    db_path: str | Path,
    *,
    limit: int = 24,
    offset: int = 0,
    game: str | None = None,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    """分页列出比赛。status 支持 live / ended；q 搜选手名与 matchId。"""
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
        if source == "melee":
            where.append("m.source = 'melee'")
        elif source == "import":
            where.append("m.source != 'melee'")
        elif source and source not in {"all", ""}:
            where.append("m.source = ?")
            params.append(source)
        if q:
            # players 是 JSON 文本，LIKE 足以覆盖"按选手名找一场"。
            where.append("(m.match_id LIKE ? OR m.players LIKE ?)")
            needle = f"%{q}%"
            params.extend([needle, needle])
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        # 只有上面这几个固定片段会被拼进 clause，用户输入全部走参数绑定（nosec: B608）。
        total = conn.execute(
            f"SELECT COUNT(*) FROM matches m{clause}", params  # nosec B608
        ).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT {_MATCH_COLUMNS}
            FROM matches m{clause}
            ORDER BY COALESCE(m.played_at, m.created_at) DESC, m.match_id DESC
            LIMIT ? OFFSET ?
            """,  # nosec B608
            (*params, limit, offset),
        ).fetchall()

        payloads = _terminals(conn, [row["match_id"] for row in rows])
        items = [
            _row_to_item(row, payloads.get(row["match_id"]) or {}, lang)
            for row in rows
        ]
        return {"count": total, "limit": limit, "offset": offset, "items": items}
    finally:
        conn.close()


def find_match(db_path: str | Path, match_id: str) -> dict[str, Any] | None:
    """按 id 取单场，供分享卡等按需生成路径使用。

    不走 `list_matches(limit=500)` 再线性查找：那会把整库最近 500 场
    连同事件计数全部算一遍，只为拿一行。
    """
    _ensure(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT {_MATCH_COLUMNS}
            FROM matches m WHERE m.match_id = ?
            """,  # nosec B608 - fragment is a module constant; match_id is bound
            (match_id,),
        ).fetchone()
        if row is None:
            return None
        payload = _terminal(conn, match_id) or {}
        return _row_to_item(row, payload)
    finally:
        conn.close()


def overview(db_path: str | Path, lang: str = "en", source: str = "all") -> dict[str, Any]:
    """首页与统计页要的聚合值。全部来自 SQL 计数。

    站内赛事与外部导入必须能分开看：默认 `all` 给出总量与来源构成，
    但 `topWinners` 只统计 `source='melee'`，导入的 Kaggle 对局不能把
    选手胜场刷进站内榜单。需要看纯导入数据时传 `source=import`。
    """
    _ensure(db_path)
    conn = _connect(db_path)
    try:
        scoped = "" if source in ("all", "", None) else (
            " WHERE source = 'melee'" if source == "melee" else
            " WHERE source != 'melee'" if source == "import" else
            " WHERE source = ?"
        )
        params: tuple[Any, ...] = () if source in ("all", "", None, "melee", "import") else (source,)
        total = conn.execute(f"SELECT COUNT(*) FROM matches{scoped}", params).fetchone()[0]  # nosec B608
        ended = conn.execute(f"SELECT COUNT(*) FROM matches{scoped}{' AND' if scoped else ' WHERE'} ended_at IS NOT NULL", params).fetchone()[0]  # nosec B608
        events = conn.execute(
            f"SELECT COUNT(*) FROM events e JOIN matches m ON m.match_id = e.match_id{scoped}", params  # nosec B608
        ).fetchone()[0]
        by_game = [
            {"game": row[0], "label": _label_of(row[0], lang), "matches": row[1]}
            for row in conn.execute(
                f"SELECT game, COUNT(*) FROM matches{scoped} GROUP BY game ORDER BY COUNT(*) DESC",  # nosec B608
                params,
            )
        ]

        # 胜者榜只读站内赛事；source=all 时也不把导入的 100 场混进来。
        winners: dict[str, int] = {}
        winner_source = "m.source = 'melee'" if source in ("all", "", None) else (
            "m.source != 'melee'" if source == "import" else "m.source = ?"
        )
        winner_params: tuple[Any, ...] = () if source in ("all", "", None, "melee", "import") else (source,)
        for row in conn.execute(
            f"SELECT e.payload FROM events e JOIN matches m ON m.match_id = e.match_id "
            f"WHERE e.type = 'match.ended' AND {winner_source} ORDER BY e.seq DESC LIMIT 2000",  # nosec B608
            winner_params,
        ):
            try:
                winner = json.loads(row[0]).get("winner")
            except json.JSONDecodeError:
                continue
            # 团队赛的胜者是 red / blue，不是选手；混进"胜者榜"会读成一个人叫"蓝队"。
            if winner and winner not in {"red", "blue"}:
                winners[winner] = winners.get(winner, 0) + 1
        top = sorted(winners.items(), key=lambda item: (-item[1], item[0]))[:8]

        # activity 默认只统计站内赛事。外部 episode 没有可信的绝对时间：
        # Kaggle 只给每手耗时，played_at 是用导入时刻倒推的，拿它画
        # "近 14 天活动"等于把 100 场历史棋全堆到今天。需要看导入节奏时
        # 显式传 source=import；source=all 也不把两者混在一起。
        activity_where = "played_at IS NOT NULL AND source = 'melee'"
        activity_params: tuple[Any, ...] = ()
        if source == "import":
            activity_where = "played_at IS NOT NULL AND source != 'melee'"
        elif source not in ("all", "", None, "melee"):
            activity_where = "played_at IS NOT NULL AND source = ?"
            activity_params = (source,)
        recent_days = [
            {"day": row[0], "matches": row[1]}
            for row in conn.execute(
                f"SELECT substr(COALESCE(played_at, created_at), 1, 10) AS day, COUNT(*) FROM matches "
                f"WHERE {activity_where} GROUP BY day ORDER BY day DESC LIMIT 14",  # nosec B608
                activity_params,
            )
        ]
        imported_days = [
            {"day": row[0], "matches": row[1]}
            for row in conn.execute(
                "SELECT substr(COALESCE(played_at, created_at), 1, 10) AS day, COUNT(*) FROM matches "
                "WHERE played_at IS NOT NULL AND source != 'melee' GROUP BY day ORDER BY day DESC LIMIT 14"
            )
        ]
        sources = [
            {"source": row[0] or "melee", "matches": row[1]}
            for row in conn.execute("SELECT source, COUNT(*) FROM matches GROUP BY source ORDER BY COUNT(*) DESC")
        ]
        melee_total = conn.execute("SELECT COUNT(*) FROM matches WHERE source = 'melee'").fetchone()[0]
        import_total = conn.execute("SELECT COUNT(*) FROM matches WHERE source != 'melee'").fetchone()[0]
        return {
            "matches": total,
            "finished": ended,
            "live": max(0, total - ended),
            "events": events,
            "byGame": by_game,
            "topWinners": [{"player": name, "wins": wins} for name, wins in top],
            "activity": list(reversed(recent_days)),
            "importedActivity": list(reversed(imported_days)),
            "activityScope": "melee" if source in ("all", "", None, "melee") else source,
            "sources": sources,
            "meleeMatches": melee_total,
            "importedMatches": import_total,
            "sourceScope": source or "all",
            "topWinnersScope": "melee" if source in ("all", "", None) else source,
        }
    finally:
        conn.close()
