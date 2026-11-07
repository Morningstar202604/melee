"""从事件日志重建比赛状态。

为什么需要：比赛状态原本只在创建它的那个进程里。多副本时，
连到另一副本的观众只能读事件，读不到"现在轮到谁"这类实时状态。

重建的做法是重放：种子与选手来自 `match.created`，之后的
`action.applied` 逐条重跑引擎。因为引擎是确定性的、apply 是纯函数，
重放的终态与直播时的状态逐字段相同。

**种子为什么可能不在事件里**：默认 secure 模式有意不把种子写进公开事件
（种子能重建全部隐藏信息）。此时种子从 `matches` 表读——那是服务端列，
不出现在任何 API 响应里。历史事件里带 seed 的（replay 模式与老录像）
优先用事件里的值，保证老录像仍能按当时抽的签重放。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine import registry as game_registry
from .engine.base import Game, Move
from .eventlog import read_match


@dataclass
class Rehydrated:
    match_id: str
    game: Game
    state: dict[str, Any]
    events: list[dict[str, Any]]

    @property
    def finished(self) -> bool:
        return bool(self.state.get("finished"))

    def public_state(self) -> dict[str, Any]:
        return self.game.public_state(self.state)


class RehydrateError(ValueError):
    pass


def rehydrate(db_path: str | Path, match_id: str) -> Rehydrated:
    """把一场比赛从日志重建成可继续查询的状态。"""
    events = read_match(db_path, match_id)
    if not events:
        raise RehydrateError(f"日志里没有这场比赛：{match_id}")

    game, state = _initial_state(events, match_id, db_path)
    for event in events:
        if event["type"] == "action.applied":
            state = _replay_action(game, state, event)

    return Rehydrated(match_id=match_id, game=game, state=state, events=events)


def _seed_from_row(db_path: str | Path, match_id: str) -> int | None:
    """从 `matches` 表取服务端保存的种子（不对外暴露的那一列）。

    只在这一列存在时才用；库被裁剪或行被删就当没有，好让报错走正常路径。
    """
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT seed FROM matches WHERE match_id = ?", (match_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return int(row[0]) if row and row[0] is not None else None


def _initial_state(events: list[dict[str, Any]], match_id: str, db_path: str | Path) -> tuple[Any, dict[str, Any]]:
    created = next((e for e in events if e["type"] == "match.created"), None)
    if created is None:
        raise RehydrateError(f"{match_id} 缺少 match.created，无法确定种子与选手")

    payload = created["payload"]
    game_id = payload.get("game") or "twenty-questions"
    try:
        game = game_registry.get(game_id)
    except KeyError:
        raise RehydrateError(f"日志里的赛制已不存在：{game_id}") from None

    # 事件里有 seed 就优先用（replay 模式与历史录像）；secure 模式只在服务端
    # 的 matches 表里有，取不到才是真的没法重放。
    seed = payload.get("seed")
    if seed is None:
        seed = _seed_from_row(db_path, match_id)
    if seed is None:
        raise RehydrateError(
            f"{match_id} 既没有可公开的种子，也没有服务端种子记录，无法重放"
        )

    state = game.init(
        seed=seed,
        players=list(payload["players"]),
        config=payload.get("config") or {},
    )
    return game, state


def _replay_action(game: Any, state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    action = event["payload"]["action"]
    result = game.apply(state, event.get("player"), Move(kind=action["kind"], value=action["value"]))
    if not result.legal or result.next_state is None:
        # 历史上被拒绝的动作不会出现在 action.applied 里；
        # 真出现说明日志与引擎不一致，宁可报错也不要静默跳过。
        raise RehydrateError(f"第 {event['seq']} 条动作无法重放：{result.reason}")
    return result.next_state


def known_matches(db_path: str | Path) -> list[str]:
    """列出日志里出现过的所有 matchId，供跨副本发现。"""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT DISTINCT match_id FROM events ORDER BY match_id").fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def summarize(rehydrated: Rehydrated) -> dict[str, Any]:
    """给观战端的最小状态快照。"""
    public = rehydrated.public_state()
    current = rehydrated.game.current_player(rehydrated.state)
    summary = {
        "matchId": rehydrated.match_id,
        "game": getattr(rehydrated.game, "game_id", "unknown"),
        "players": list(rehydrated.state["players"]),
        "finished": rehydrated.finished,
        "currentPlayer": current,
        "winner": public.get("winner"),
        "events": len(rehydrated.events),
        "lastSeq": rehydrated.events[-1]["seq"] if rehydrated.events else -1,
    }
    # 二十问专有字段：只在存在时输出，避免误导（棋类没有"剩余候选"）
    for key in ("remainingPublic", "turnIndex", "maxQuestions"):
        if key in public:
            summary[key.replace("Public", "")] = public[key]
    return summary
