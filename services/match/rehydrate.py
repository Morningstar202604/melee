"""从事件日志重建比赛状态。

为什么需要：比赛状态原本只在创建它的那个进程里。多副本时，
连到另一副本的观众只能读事件，读不到"现在轮到谁"这类实时状态。

重建的做法是重放：match.created 给出种子与选手，之后的
action.applied 逐条重跑引擎。因为引擎是确定性的、apply 是纯函数，
重放的终态与直播时的状态逐字段相同。
"""

from __future__ import annotations

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

    created = next((e for e in events if e["type"] == "match.created"), None)
    if created is None:
        raise RehydrateError(f"{match_id} 缺少 match.created，无法确定种子与选手")

    payload = created["payload"]
    game_id = payload.get("game") or "twenty-questions"
    try:
        game = game_registry.get(game_id)
    except KeyError:
        raise RehydrateError(f"日志里的赛制已不存在：{game_id}") from None

    state = game.init(
        seed=payload["seed"],
        players=list(payload["players"]),
        config=payload.get("config") or {},
    )

    for event in events:
        if event["type"] != "action.applied":
            continue
        action = event["payload"]["action"]
        move = Move(kind=action["kind"], value=action["value"])
        result = game.apply(state, event.get("player"), move)
        if not result.legal or result.next_state is None:
            # 历史上被拒绝的动作不会出现在 action.applied 里；
            # 真出现说明日志与引擎不一致，宁可报错也不要静默跳过。
            raise RehydrateError(
                f"第 {event['seq']} 条动作无法重放：{result.reason}"
            )
        state = result.next_state

    return Rehydrated(match_id=match_id, game=game, state=state, events=events)


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
