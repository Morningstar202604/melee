from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EVENT_TYPES = {    "match.created",
    "match.started",
    "round.started",
    "turn.started",
    "thought.delta",
    "action.proposed",
    "action.rejected",
    "action.applied",
    "state.changed",
    "narration.line",
    "match.ended",
    "director.cue",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
  match_id   TEXT PRIMARY KEY,
  game       TEXT NOT NULL,
  seed       INTEGER NOT NULL,
  players    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  ended_at   TEXT
);

CREATE TABLE IF NOT EXISTS events (
  match_id TEXT NOT NULL,
  seq      INTEGER NOT NULL,
  type     TEXT NOT NULL,
  player   TEXT,
  at       TEXT NOT NULL,
  payload  TEXT NOT NULL,
  PRIMARY KEY (match_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events (match_id, type);
"""


class EventLogError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enable_wal(conn: sqlite3.Connection, attempts: int = 5) -> None:
    """切 WAL；并发打开时被锁占用就退避重试，仍失败则保持默认日志模式。

    为什么不用 try/except pass：切不过去不该让启动失败——WAL 是性能优化，
    默认的 delete 模式在单写多读下也能工作，只是并发写更容易撞锁。
    但必须留下日志，否则线上会以为已经开了 WAL。
    """
    import logging
    import time as _time

    logger = logging.getLogger("melee.eventlog")
    for attempt in range(attempts):
        try:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode and str(mode[0]).lower() == "wal":
                return
            logger.warning("journal_mode 切换未生效：%s", mode)
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            _time.sleep(0.05 * (attempt + 1))
    logger.warning("连续 %d 次切换 WAL 失败，继续使用默认 journal 模式", attempts)


@dataclass
class EventLog:
    """append-only 事件日志。seq 由日志分配，调用方无法跳号、乱序或重复。"""

    conn: sqlite3.Connection
    match_id: str
    _next: int = 0
    on_event: Any = None
    lock: threading.Lock = threading.Lock()

    @classmethod
    def open(cls, path: str | Path, match_id: str) -> "EventLog":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # runner 在线程池里执行，事件由它写；reader 在事件循环里读。
        # 同一连接跨线程使用需要显式放开限制，并用锁保证写入串行。
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        # 先设 busy_timeout 再切 WAL：切 journal_mode 需要短暂独占锁，
        # 并发 open 时若别的连接正持有锁，会直接抛 "database is locked"（CI 上实测）。
        conn.execute("PRAGMA busy_timeout=30000")
        _enable_wal(conn)
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA synchronous=NORMAL")
        row = conn.execute(
            "SELECT COALESCE(MAX(seq) + 1, 0) FROM events WHERE match_id = ?", (match_id,)
        ).fetchone()
        # 每条事件都推给广播：SSE 才能实时收到 worker 线程写的事件。
        # 之前这条线没接，观众只能等 15 秒心跳回查日志，等同于"假直播"。
        from .broadcast import publish_event

        return cls(conn=conn, match_id=match_id, _next=int(row[0]), on_event=publish_event)

    def create_match(self, game: str, seed: int, players: list[str]) -> None:
        # INSERT OR IGNORE：重跑同一场时不能整行替换。
        # OR REPLACE 会把 ended_at / created_at 抹成 NULL，已结束的比赛会变回"直播中"。
        self.conn.execute(
            "INSERT OR IGNORE INTO matches (match_id, game, seed, players, created_at) VALUES (?,?,?,?,?)",
            (self.match_id, game, seed, json.dumps(players, ensure_ascii=False), _now()),
        )
        self.conn.commit()

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        player: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise EventLogError(f"未知事件类型 {event_type}")
        if not isinstance(payload, dict):
            raise EventLogError("payload 必须是对象")

        event: dict[str, Any] = {
            "seq": self._next,
            "matchId": self.match_id,
            "type": event_type,
            "at": at or _now(),
            "payload": payload,
        }
        if player is not None:
            event["player"] = player

        payload_json = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            # 多个连接（API、worker、导播台）会同时写同一场比赛：
            # 各自的 _next 可能撞号，必须撞了重读再写，而不是抛给调用方。
            self._insert_with_retry(event, payload_json)
        if self.on_event is not None:
            # 广播是通知，不是事实：Redis 抖动不能让事件写入失败，
            # 否则一次 Redis 重启会让正在跑的比赛整局作废。
            try:
                self.on_event(event)
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 广播必须可降级  # aqg: top-level boundary
                logging.getLogger("melee.eventlog").warning("事件广播失败（已忽略）：%s", exc)
        return event

    def _insert_with_retry(self, event: dict[str, Any], payload_json: str) -> None:
        for attempt in range(8):
            event["seq"] = self._next
            try:
                self.conn.execute(
                    "INSERT INTO events (match_id, seq, type, player, at, payload) VALUES (?,?,?,?,?,?)",
                    (
                        self.match_id,
                        event["seq"],
                        event["type"],
                        event.get("player"),
                        event["at"],
                        payload_json,
                    ),
                )
                self.conn.commit()
                self._next += 1
                return
            except sqlite3.IntegrityError:
                self._next = self._max_seq()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))
        raise EventLogError(f"写入事件失败：{self.match_id} 连续 8 次序号冲突")

    def _max_seq(self) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq) + 1, 0) FROM events WHERE match_id = ?",
            (self.match_id,),
        ).fetchone()
        return int(row[0])

    def append_terminal(self, payload: dict[str, Any]) -> bool:
        """原子地追加唯一的 match.ended。返回是否真的写入。

        先查后写跨连接存在竞态（租约过期时两个 worker 同时收尾），
        这里用 BEGIN IMMEDIATE 把"查 + 插 + 标记结束"放进一个事务。
        """
        payload_json = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                exists = self.conn.execute(
                    "SELECT 1 FROM events WHERE match_id = ? AND type = 'match.ended' LIMIT 1",
                    (self.match_id,),
                ).fetchone()
                if exists is not None:
                    self.conn.rollback()
                    return False
                # 事务内重读 seq：别的连接可能在我们上次读之后又追加了事件，
                # 用陈旧的 self._next 会撞主键。
                row = self.conn.execute(
                    "SELECT COALESCE(MAX(seq) + 1, 0) FROM events WHERE match_id = ?",
                    (self.match_id,),
                ).fetchone()
                self._next = int(row[0])
                event: dict[str, Any] = {
                    "seq": self._next,
                    "matchId": self.match_id,
                    "type": "match.ended",
                    "at": _now(),
                    "payload": payload,
                }
                self.conn.execute(
                    "INSERT INTO events (match_id, seq, type, player, at, payload) VALUES (?,?,?,?,?,?)",
                    (self.match_id, event["seq"], "match.ended", None, event["at"], payload_json),
                )
                self.conn.execute(
                    "UPDATE matches SET ended_at = COALESCE(ended_at, ?) WHERE match_id = ?",
                    (_now(), self.match_id),
                )
                self.conn.commit()
            # aqg: top-level boundary
            except Exception:
                self.conn.rollback()
                raise
            self._next += 1
        if self.on_event is not None:
            try:
                self.on_event(event)
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 同 append  # aqg: top-level boundary
                logging.getLogger("melee.eventlog").warning("终局事件广播失败（已忽略）：%s", exc)
        return True

    def mark_ended(self) -> None:
        """幂等：已标记过就保留第一次的时间，避免重试把结束时间往后推。"""
        self.conn.execute(
            "UPDATE matches SET ended_at = COALESCE(ended_at, ?) WHERE match_id = ?",
            (_now(), self.match_id),
        )
        self.conn.commit()

    def read(self, after: int = -1) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute(
            "SELECT seq, type, player, at, payload FROM events "
                "WHERE match_id = ? AND seq > ? ORDER BY seq ASC",
                (self.match_id, after),
            ).fetchall()
        events = []
        for seq, event_type, player, at, payload in rows:
            event: dict[str, Any] = {
                "seq": seq,
                "matchId": self.match_id,
                "type": event_type,
                "at": at,
                "payload": json.loads(payload),
            }
            if player is not None:
                event["player"] = player
            events.append(event)
        return events

    @property
    def next_seq(self) -> int:
        return self._next

    def seqs(self) -> list[int]:
        return [row[0] for row in self.conn.execute(
            "SELECT seq FROM events WHERE match_id = ? ORDER BY seq ASC", (self.match_id,)
        )]

    def assert_contiguous(self) -> None:
        seqs = self.seqs()
        if seqs != list(range(len(seqs))):
            raise EventLogError(f"事件序号不连续：{seqs}")

    def close(self) -> None:
        self.conn.close()


def read_match(path: str | Path, match_id: str, after: int = -1) -> list[dict[str, Any]]:
    """不依赖进程内注册表，直接从日志读某一场的事件。

    赛季批量跑出来的比赛没有 MatchHandle，观战与战报仍要能读。
    """
    log = EventLog.open(path, match_id)
    try:
        return log.read(after)
    finally:
        log.close()


def has_match(path: str | Path, match_id: str) -> bool:
    """比赛是否已登记（不等于已有事件：worker 可能还没领取）。"""
    conn = sqlite3.connect(str(path))
    try:
        exists = conn.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone()
        return exists is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def replay(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """把事件流折叠成视图状态。用来验证「回放 = 直播」。"""
    view: dict[str, Any] = {
        "turns": [],
        "narration": [],
        "winner": None,
        "endReason": "",
        "cues": [],
        "remaining": None,
    }
    for event in events:
        payload = event["payload"]
        kind = event["type"]
        if kind == "action.applied":
            view["turns"].append(
                {"player": event.get("player"), "action": payload["action"], "effects": payload["effects"]}
            )
        elif kind == "state.changed":
            view["remaining"] = payload["publicState"].get("remainingPublic")
        elif kind == "narration.line":
            view["narration"].append(payload["text"])
        elif kind == "match.ended":
            view["winner"] = payload["winner"]
            view["endReason"] = payload["reason"]
        elif kind == "director.cue":
            view["cues"].append(payload["shot"])
    return view
