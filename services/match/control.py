"""人工导播：把导播员的判断写进同一条事件流。

为什么必须走事件而不是"直接改前端"：
- 回放要能重现人工切镜，否则直播和回放会不一致（这是 M2 的验收条件）。
- 事件日志是唯一真相，人工操作也必须留下痕迹。

并发写入的处理：worker 线程可能正在往同一场写事件。
`EventLog` 的锁只在实例内有效，所以这里用「重开连接 + 主键冲突重试」，
冲突时重新读一次 max(seq) 再写。SQLite 的主键约束会兜底。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .eventlog import EventLog, read_match

SHOTS = ("wide", "close", "thought", "reveal", "slowmo")


class CueError(ValueError):
    pass


def push_cue(
    db_path: str | Path,
    match_id: str,
    *,
    shot: str,
    target: str | None = None,
    duration_ms: int = 1200,
    note: str = "",
    attempts: int = 5,
) -> dict[str, Any]:
    """追加一条人工 cue。返回写入的事件。"""
    if shot not in SHOTS:
        raise CueError(f"未知镜头：{shot}（可用：{', '.join(SHOTS)}）")
    if duration_ms < 0:
        raise CueError("时长必须非负")

    events = read_match(db_path, match_id)
    if not events:
        raise CueError(f"没有这场比赛：{match_id}")
    if any(event["type"] == "match.ended" for event in events):
        raise CueError("比赛已结束，不能再切镜")

    trigger = f"人工：{note}" if note else "人工"

    for _attempt in range(attempts):
        log = EventLog.open(db_path, match_id)
        try:
            return log.append(
                "director.cue",
                {
                    "shot": shot,
                    "target": target,
                    "durationMs": duration_ms,
                    "trigger": trigger[:200],
                },
            )
        except sqlite3.IntegrityError:
            # 有并发写入抢先占了这个 seq，重开连接后序号会自动前移。
            continue
        finally:
            log.close()

    raise CueError("写入 cue 失败：并发冲突重试次数已用尽")


def recent_cues(db_path: str | Path, match_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """按时间倒序列出这场比赛的人工 cue。"""
    cues = [
        event
        for event in read_match(db_path, match_id)
        if event["type"] == "director.cue" and str(event["payload"].get("trigger", "")).startswith("人工")
    ]
    return list(reversed(cues))[:limit]


def live_matches(db_path: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    """列出还没结束的比赛，供导播台选台。

    先用 matches 表筛掉已结束的（ended_at 非空），再只对候选做重放。
    之前对每一场（含已结束的）都完整重放一遍，实测 50 场要 4.5 秒。
    """
    import sqlite3

    from .rehydrate import rehydrate, summarize

    conn = sqlite3.connect(str(db_path), timeout=5)
    try:
        rows = conn.execute(
            "SELECT match_id FROM matches WHERE ended_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (max(1, limit * 4),),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    out = []
    for (match_id,) in rows:
        try:
            summary = summarize(rehydrate(db_path, match_id))
        except Exception:  # noqa: BLE001 - 坏日志不该拖垮整张列表
            continue
        if not summary["finished"]:
            out.append(summary)
            if len(out) >= limit:
                break
    return out


def auto_cue_summary(db_path: str | Path, match_id: str) -> dict[str, int]:
    """统计自动与人工各切了多少次，给导播台做对比。"""
    counts = {"auto": 0, "manual": 0}
    for event in read_match(db_path, match_id):
        if event["type"] != "director.cue":
            continue
        if str(event["payload"].get("trigger", "")).startswith("人工"):
            counts["manual"] += 1
        else:
            counts["auto"] += 1
    return counts
