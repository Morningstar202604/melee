"""实时外部对局桥：外部 arena 每产生一个快照，就推进一次 melee 直播。

它不是"导入完成后的回放"，而是把外部 episode 的**增量快照**投影成
melee 事件流：第一帧建场，后续帧只追加新增的棋步，最后一帧收尾。
所以同一套 SSE、导播、回放、战报、高光全部复用，外部 arena 不需要
实现 melee 的事件协议，只要把已发生的 steps 再发一次。

边界：
- 每一帧必须是**前缀稳定**的：第 N 帧的 steps 是第 N+1 帧的前缀；
  删步/改历史返回 409，避免已播出的画面被静默改写。
- 同一 (source, episodeId) 只建一场；重复帧幂等返回当前进度。
- 这是 push 桥，不主动轮询外部 API；集成方在自己的事件回调里调用。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .db import connect
from .episode import Episode, build_export, parse_episode
from .eventlog import EventLog, read_match
from .portable import import_match

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

MAX_LIVE_STEPS = 10_000


class LiveEpisodeError(ValueError):
    """外部快照不满足前缀约束，或不能在当前状态推进。"""


def _lock_for(match_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(match_id, threading.Lock())


def find_live_match(db_path: str | Path, source: str, source_id: str) -> str | None:
    if not source_id:
        return None
    from .archive import SCHEMA as ARCHIVE_SCHEMA
    from .db import ensure_schema
    from .eventlog import SCHEMA as EVENT_SCHEMA
    from .migrations import migrate

    conn = connect(str(db_path), timeout=30.0)
    try:
        ensure_schema(conn, str(db_path), EVENT_SCHEMA, "eventlog")
        ensure_schema(conn, str(db_path), ARCHIVE_SCHEMA, "archive")
        migrate(conn)
        row = conn.execute(
            "SELECT match_id FROM matches WHERE source = ? AND source_id = ? LIMIT 1",
            (source, source_id),
        ).fetchone()
        return str(row[0]) if row else None
    finally:
        conn.close()


def _event_count(db_path: str | Path, match_id: str) -> int:
    return len(read_match(db_path, match_id))


def ingest_live_episode(
    db_path: str | Path,
    document: Any,
    *,
    fmt: str = "auto",
    finished: bool = False,
    metadata: dict[str, Any] | None = None,
    episode: Episode | None = None,
) -> dict[str, Any]:
    """接收一帧外部 episode 快照；返回当前进度。

    参数 `finished` 由集成方在外部终局时置 true。不要用"最后一帧一定有
    match.ended"这种猜测：外部 arena 可能先发终局状态，再补奖励。
    """
    if episode is None:
        episode = parse_episode(document, fmt)
    if metadata:
        episode.config = {**episode.config, **metadata}
    if len(episode.steps) > MAX_LIVE_STEPS:
        raise LiveEpisodeError(f"live episode has too many steps (>{MAX_LIVE_STEPS})")

    source = episode.source
    source_id = episode.episode_id
    if not source_id:
        raise LiveEpisodeError("live import requires episodeId (stable external episode key)")

    match_id = find_live_match(db_path, source, source_id)
    if match_id is None:
        # 建场和首帧必须在同一把锁里完成，否则两个首帧会各建一场。
        # source_id 的唯一索引仍会兜底，但这里先避免把无谓的错误抛给用户。
        ephemeral = f"live:{source}:{source_id}"
        with _lock_for(ephemeral):
            match_id = find_live_match(db_path, source, source_id)
            if match_id is None:
                header = build_export(episode, finished=False, live=True)
                header_doc = {**header, "events": header["events"][:2]}
                result = import_match(db_path, header_doc)
                match_id = str(result["matchId"])
                # 如果两个进程同时建场，唯一索引会让后到的一方返回
                # 已存在的 match_id；继续走同一套 append 逻辑即可。
                if result.get("deduplicated"):
                    return ingest_live_episode(
                        db_path, document, fmt=fmt, finished=finished, metadata=metadata
                    )
    assert match_id is not None

    with _lock_for(match_id):
        existing = read_match(db_path, match_id)
        if not existing:
            raise LiveEpisodeError("live match has no events")
        # 已结束的场次只接受同内容的重复帧，不允许复活。
        ended = next((e for e in existing if e.get("type") == "match.ended"), None)
        if ended is not None:
            return _result(match_id, existing, duplicate=True, finished=True, appended=0)

        projected = build_export(episode, finished=finished, live=True)
        events = projected["events"]
        # 前缀稳定：已知事件数不能超过新投影（否则外部删步/改历史）。
        # 前两条是 created/started；之后每手固定 5 条事件。
        if len(existing) > len(events):
            raise LiveEpisodeError(
                f"snapshot rewinds history: persisted {len(existing)} events, incoming {len(events)}"
            )
        # 长度够不代表历史没被改写：逐条比对已播出的事件内容（忽略 at，
        # 因为快照会用新的导入时刻重新锚定时间轴）。类型、选手或 payload
        # 任一变化都说明外部改了历史，必须 409，而不是继续追加。
        for index in range(2, len(existing)):
            old = existing[index]
            new = events[index]
            if (
                old.get("type") != new.get("type")
                or old.get("player") != new.get("player")
                or old.get("payload") != new.get("payload")
            ):
                raise LiveEpisodeError(
                    f"snapshot rewrote history at event {index}; refusing to diverge from what was broadcast"
                )
        todo = events[len(existing):]
        if finished and todo and todo[-1]["type"] == "match.ended":
            terminal = todo.pop()
        else:
            terminal = None

        log = EventLog.open(db_path, match_id)
        appended = 0
        for event in todo:
            log.append(
                str(event["type"]),
                dict(event["payload"]),
                player=event.get("player"),
                at=event.get("at"),
                visibility=event.get("visibility"),
            )
            appended += 1
        # 终局帧也要给出镜头收束，否则实时桥结束得比回放桥安静。
        # 必须写在 match.ended 之前：ended 是终态，之后再追加事件会让
        # 回放尾帧出现"已结束还在切镜"。
        if finished:
            log.append(
                "director.cue",
                {
                    "shot": "slowmo",
                    "target": episode.players[0] if episode.players else "",
                    "durationMs": 3000,
                    "trigger": "last move",
                    "triggerKey": "cue.last_move",
                    "triggerParams": {},
                },
            )
        if terminal is not None:
            log.append_terminal(dict(terminal["payload"]))
        log.conn.close()
        existing = read_match(db_path, match_id)
        return _result(match_id, existing, duplicate=False, finished=finished, appended=appended)


def _result(match_id: str, events: list[dict[str, Any]], *, duplicate: bool, finished: bool, appended: int) -> dict[str, Any]:
    return {
        "matchId": match_id,
        "live": not finished,
        "finished": finished,
        "duplicate": duplicate,
        "appended": appended,
        "events": len(events),
        "steps": sum(1 for event in events if event.get("type") == "action.applied"),
    }
