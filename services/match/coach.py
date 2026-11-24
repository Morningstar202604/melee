"""教练席：观众/教练在直播中给某个 AI 下发一条策略指令。

设计边界：

- **指令是事件，不是直接改引擎状态**。教练只能影响选手下一手的决策，
  不能替选手落子、不能读取隐藏信息、不能改变规则。
- **只投递一次**。取走后标记 delivered，避免同一条指令在多个回合重复放大。
- **公开可见**。事件留在日志里，回放能看到"谁在什么时候给了什么建议"，
  这是"观赛视角透明化"的一部分，也避免暗箱操作。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .db import open_db
from .eventlog import EventLog, has_match, read_match

MAX_INSTRUCTION_CHARS = 280
MAX_PENDING_PER_TAKE = 3


class CoachError(ValueError):
    """指令不合法（空、过长、目标不在本场）。"""


@dataclass(frozen=True)
class CoachCommand:
    command_id: int
    match_id: str
    target: str
    instruction: str


def validate_instruction(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        raise CoachError("教练指令不能为空")
    if len(text) > MAX_INSTRUCTION_CHARS:
        raise CoachError(f"教练指令最多 {MAX_INSTRUCTION_CHARS} 个字符")
    return text


def _ensure_table(db_path: str | Path) -> None:
    """迁移已建表；这里只做兜底，给手工构造的测试库用。"""
    with open_db(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS coach_messages ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " match_id TEXT NOT NULL, target TEXT NOT NULL,"
            " instruction TEXT NOT NULL, created_at TEXT NOT NULL,"
            " delivered_at TEXT)"
        )
        conn.commit()


def enqueue(db_path: str | Path, match_id: str, target: str, instruction: str) -> CoachCommand:
    text = validate_instruction(instruction)
    _ensure_table(db_path)
    from .clock import utc_now_iso

    with open_db(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO coach_messages (match_id, target, instruction, created_at) VALUES (?,?,?,?)",
            (match_id, target, text, utc_now_iso()),
        )
        conn.commit()
        return CoachCommand(int(cur.lastrowid), match_id, target, text)


def _take(db_path: str | Path, match_id: str, target: str) -> list[CoachCommand]:
    """原子取走该选手最多 3 条未投递指令。"""
    _ensure_table(db_path)
    from .clock import utc_now_iso

    with open_db(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                "SELECT id, instruction FROM coach_messages "
                "WHERE match_id = ? AND target = ? AND delivered_at IS NULL "
                "ORDER BY id ASC LIMIT ?",
                (match_id, target, MAX_PENDING_PER_TAKE),
            ).fetchall()
            if not rows:
                conn.rollback()
                return []
            now = utc_now_iso()
            conn.executemany(
                "UPDATE coach_messages SET delivered_at = ? WHERE id = ?",
                [(now, row[0]) for row in rows],
            )
            conn.commit()
            return [CoachCommand(int(row[0]), match_id, target, str(row[1])) for row in rows]
        except Exception:
            conn.rollback()
            raise


class CoachBook:
    """一场比赛的教练指令簿；runner 每手取一次。"""

    def __init__(self, db_path: str | Path, match_id: str) -> None:
        self.db_path = db_path
        self.match_id = match_id

    def take(self, player: str) -> list[CoachCommand]:
        return _take(self.db_path, self.match_id, player)


def live_players(db_path: str | Path, match_id: str) -> list[str]:
    """从比赛行取选手；比赛不存在或已结束时由路由层判断。"""
    if not has_match(db_path, match_id):
        return []
    with open_db(db_path, rows=True) as conn:
        row = conn.execute("SELECT players FROM matches WHERE match_id = ?", (match_id,)).fetchone()
    if row is None:
        return []
    import json

    try:
        return [str(item) for item in json.loads(row["players"] or "[]")]
    except (TypeError, ValueError):
        return []


def is_live(db_path: str | Path, match_id: str) -> bool:
    """有 match.created 且没有 match.ended，才算直播中。"""
    events = read_match(db_path, match_id)
    if not any(event.get("type") == "match.created" for event in events):
        return False
    return not any(event.get("type") == "match.ended" for event in events)


def log_queued(db_path: str | Path, command: CoachCommand) -> dict:
    """落一条公开的 coach.message（queued）。"""
    log = EventLog.open(db_path, command.match_id)
    try:
        return log.append(
            "coach.message",
            {
                "commandId": command.command_id,
                "target": command.target,
                "instruction": command.instruction,
                "status": "queued",
            },
            player=command.target,
        )
    finally:
        log.close()


def log_delivered(log: EventLog, command: CoachCommand) -> None:
    """runner 取走指令时落 delivered；与 queued 同 commandId，前端合并。"""
    log.append(
        "coach.message",
        {
            "commandId": command.command_id,
            "target": command.target,
            "instruction": command.instruction,
            "status": "delivered",
        },
        player=command.target,
    )
