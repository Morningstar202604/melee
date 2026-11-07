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
    from .eventlog import has_match

    problems = validate_export(document)
    if problems:
        # 一次把全部问题说清楚：调用方拿到的是"第 37 条事件 seq 应为 37"，
        # 而不是笼统的 unsupported export format——后者让人无从下手。
        raise ValueError("invalid export: " + "; ".join(problems[:5]))

    match_id = new_match_id or _new_id()
    if has_match(Path(db_path), match_id):
        raise ValueError("match id already exists")

    # 先整体校验再写：旧实现边校验边 append，每条 append 都 commit，
    # 遇到第 500 条坏数据时前 499 条已经落库，留下永远无法重放的半场。
    events = document["events"]
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        _ensure_tables(conn, Path(db_path))
        created = dict(events[0]["payload"])
        created["players"] = document.get("players", created.get("players", []))
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO matches (match_id, game, seed, players, created_at) VALUES (?,?,?,?,?)",
                (match_id, document["game"], int(document.get("seed") or 0), _json_players(created), _now()),
            )
            for event in events:
                conn.execute(
                    "INSERT INTO events (match_id, seq, type, player, at, payload, visibility) VALUES (?,?,?,?,?,?,?)",
                    (
                        match_id,
                        int(event["seq"]),
                        event["type"],
                        event.get("player"),
                        event.get("at") or _now(),
                        json.dumps(event["payload"], ensure_ascii=False),
                        _visibility_json(event.get("visibility")),
                    ),
                )
            if document.get("endedAt"):
                conn.execute("UPDATE matches SET ended_at = ? WHERE match_id = ?", (_now(), match_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    return {"matchId": match_id, "events": len(events), "fingerprint": _fingerprint(document)}


# 导入上限：一份导出文件是"一场比赛"，恶意或误操作的大文件不该把库撑爆。
MAX_IMPORT_EVENTS = 200_000
MAX_IMPORT_BYTES = 64 * 1024 * 1024

EVENT_ENVELOPE_REQUIRED = ("seq", "type", "payload")


def validate_export(document: Any) -> list[str]:
    """校验一份导出文档，返回问题清单（空列表 = 可以使用）。

    宁可一次列全，也不要写一半才失败。只做结构校验：语义（引擎规则）
    由回放时的 reducer 负责，导入不重跑引擎。
    """
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["document must be a JSON object"]
    if document.get("format") != EXPORT_FORMAT:
        problems.append(f"format must be {EXPORT_FORMAT!r}, got {document.get('format')!r}")
    game = document.get("game")
    if not isinstance(game, str) or not game.strip():
        problems.append("game must be a non-empty string")
    players = document.get("players")
    if not isinstance(players, list) or not all(isinstance(p, str) and p for p in players):
        problems.append("players must be a list of non-empty strings")

    events = document.get("events")
    if not isinstance(events, list):
        problems.append("events must be a list")
        return problems
    if not events:
        problems.append("events must not be empty")
        return problems
    if len(events) > MAX_IMPORT_EVENTS:
        problems.append(f"events exceeds {MAX_IMPORT_EVENTS}")
        return problems

    for index, event in enumerate(events):
        if not isinstance(event, dict):
            problems.append(f"event[{index}] must be an object")
            continue
        missing = [key for key in EVENT_ENVELOPE_REQUIRED if key not in event]
        if missing:
            problems.append(f"event[{index}] missing {','.join(missing)}")
        if "seq" in event and not isinstance(event["seq"], int):
            problems.append(f"event[{index}].seq must be an integer")
        if "type" in event and not isinstance(event["type"], str):
            problems.append(f"event[{index}].type must be a string")
        if "payload" in event and not isinstance(event["payload"], dict):
            problems.append(f"event[{index}].payload must be an object")
        if problems and len(problems) >= 20:
            problems.append("... more problems omitted")
            break
        # seq 必须从 0 连续：断了就说明文件被截断或拼接过
        if isinstance(event.get("seq"), int) and event["seq"] != index:
            problems.append(f"event[{index}].seq should be {index}, got {event['seq']}")
            break
    return problems


def _json_players(created: dict[str, Any]) -> str:
    return json.dumps(list(created.get("players") or []), ensure_ascii=False)


def _visibility_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else str(value)


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
