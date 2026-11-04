"""可移植对局文件：导出/导入一份自包含 JSON（Lichess PGN 的类比物）。

为什么值得做：事件流是 melee 的唯一事实源，把它打包成单文件，
任何实例都能原样重放一场比赛——跨实例分享、赛后归档、做数据集
都靠这一条路。导出物包含 game/seed/players/config/全部事件，
导入后成为只读回放场次（不会重跑引擎，赢了就是赢了）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

EXPORT_FORMAT = "melee.export/1"


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def export_match(
    db_path: str | Path,
    match_id: str,
    *,
    reveal: bool = False,
    require_ended_check: bool = False,
) -> dict[str, Any] | None:
    """读出一场比赛的自包含快照；比赛不存在时返回 None。

    可见性与 /events 契约一致：默认只导出 public 事件；
    `reveal=True` 揭秘私有事件，`require_ended_check=True` 时
    揭秘必须发生在比赛结束后（由路由层传入，保持单一裁决点）。
    """
    from .eventlog import read_match
    from .routes.common import visible

    events = read_match(db_path, match_id)
    if not events:
        return None
    if reveal:
        # 与 /events 契约完全一致：比赛没结束就不能揭秘。
        if not any(e["type"] == "match.ended" for e in events):
            raise ValueError("match not ended; reveal is not allowed yet")
    else:
        # 过滤私有事件时必须**保留 seq 连续性**：直接丢掉会让 seq 出现空洞，
        # 导入端按"seq 必须从 0 连续"校验，于是任何含私有事件的比赛都导不进来
        # （实测 test_export_roundtrip 直接 ValueError）。
        # 做法：私有事件替换成一条占位（同样的 seq/type），内容抹掉。
        events = [
            e if visible(e, None) else {**e, "payload": {"redacted": True}}
            for e in events
        ]
    created = events[0]["payload"]
    ended = [e for e in events if e["type"] == "match.ended"]
    return {
        "format": EXPORT_FORMAT,
        "matchId": match_id,
        "fingerprint": _fingerprint({
            "game": created.get("game", ""),
            "seed": created.get("seed"),
            "players": created.get("players", []),
            "config": created.get("config", {}),
        }),
        "game": created.get("game", ""),
        "seed": created.get("seed"),
        "players": created.get("players", []),
        "config": created.get("config", {}),
        "createdAt": events[0]["at"],
        "endedAt": ended[-1]["at"] if ended else None,
        "events": [
            {
                "seq": e["seq"],
                "type": e["type"],
                "at": e["at"],
                "player": e.get("player"),
                "payload": e["payload"],
                "visibility": e.get("visibility"),
            }
            for e in events
        ],
    }


def _fingerprint(document: dict[str, Any]) -> str:
    """可复现指纹：同 game+seed+players+config 必重放出场。"""
    material = {
        "game": document.get("game"),
        "seed": document.get("seed"),
        "players": document.get("players"),
        "config": document.get("config"),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def import_match(db_path: str | Path, document: dict[str, Any], *, new_match_id: str | None = None) -> dict[str, Any]:
    """把导出文件写成只读回放场次。返回 {matchId, events}。

    保留原 matchId 会让两实例的 id 冲突（导入两份同 id 的导出会互相覆盖），
    所以默认生成新 id，并在事件里替换 matchId 以维持"事件属于这一场"。
    """
    from .eventlog import EventLog, has_match

    if document.get("format") != EXPORT_FORMAT:
        raise ValueError("unsupported export format")
    if not isinstance(document.get("events"), list) or not document["events"]:
        raise ValueError("export has no events")

    match_id = new_match_id or _new_id()
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        ensure = _ensure_tables(conn, Path(db_path))
    finally:
        conn.close()
    _ = ensure

    log = EventLog.open(Path(db_path), match_id)
    try:
        if has_match(Path(db_path), match_id):
            raise ValueError("match id already exists")
        first = document["events"][0]
        created = dict(first["payload"])
        created["players"] = document.get("players", created.get("players", []))
        log.create_match(document["game"], document.get("seed") or 0, created.get("players", []))
        last_seq = -1
        for event in document["events"]:
            seq = int(event["seq"])
            if seq != last_seq + 1:
                raise ValueError("event seqs must be contiguous starting at 0")
            last_seq = seq
            payload = event["payload"]
            at = event.get("at")
            # 导入是回放：at 原样保留，重放时间线不漂移。
            log.append(
                event["type"],
                payload,
                player=event.get("player"),
                at=at,
                visibility=event.get("visibility") or "public",
            )
        if document.get("endedAt"):
            log.mark_ended()
    finally:
        log.close()

    return {"matchId": match_id, "events": len(document["events"]), "fingerprint": _fingerprint(document)}


def _new_id() -> str:
    import uuid

    return "m_" + uuid.uuid4().hex[:12]


def _ensure_tables(conn: sqlite3.Connection, db_path: Path) -> bool:
    """导入前确保 matches/events 表存在（空库导入场景）。"""
    from .archive import SCHEMA as ARCHIVE_SCHEMA
    from .db import ensure_schema
    from .eventlog import SCHEMA as EVENT_SCHEMA

    ensure_schema(conn, db_path, EVENT_SCHEMA, "eventlog")
    ensure_schema(conn, db_path, ARCHIVE_SCHEMA, "archive")
    return True
