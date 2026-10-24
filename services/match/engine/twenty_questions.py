"""二十问 · 双人属性版。

规则被刻意压到最小，只为让"提问收窄"这件事可被观测：

- 有一组候选物，每个物有若干布尔/枚举属性。
- 两名选手轮流问一个"属性 = 值"的是非题。
- 引擎按属性表直接回答是/否。没有任何 LLM 参与判定。
- 任何时候都可以直接猜；猜错立即判负。
- 问满 max_questions 仍未猜中，双方都算未中，胜负按"排除信息量"判定。

隐藏信息只有一条：target。它不会出现在 observe() 的返回值里。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

from .base import AppliedEffect, Move, MoveResult, PlayerId

# 属性表：每个维度都是互斥枚举，问"属性=值"时可直接回答。
CATALOG: list[dict[str, str]] = [
    {"name": "灯塔", "category": "建筑", "place": "海边", "function": "导航", "material": "石", "scale": "大型"},
    {"name": "航标", "category": "装置", "place": "海面", "function": "导航", "material": "金属", "scale": "小型"},
    {"name": "路灯", "category": "装置", "place": "街道", "function": "照明", "material": "金属", "scale": "中型"},
    {"name": "钟楼", "category": "建筑", "place": "城市", "function": "报时", "material": "石", "scale": "大型"},
    {"name": "信号灯", "category": "装置", "place": "街道", "function": "导航", "material": "塑料", "scale": "小型"},
    {"name": "水塔", "category": "建筑", "place": "城市", "function": "储水", "material": "混凝土", "scale": "大型"},
    {"name": "风车", "category": "建筑", "place": "旷野", "function": "发电", "material": "木", "scale": "大型"},
    {"name": "瞭望塔", "category": "建筑", "place": "山地", "function": "观察", "material": "木", "scale": "中型"},
    {"name": "烟囱", "category": "建筑", "place": "城市", "function": "排烟", "material": "砖", "scale": "中型"},
    {"name": "天线", "category": "装置", "place": "城市", "function": "通信", "material": "金属", "scale": "小型"},
    {"name": "桥墩", "category": "建筑", "place": "河上", "function": "支撑", "material": "混凝土", "scale": "大型"},
    {"name": "码头", "category": "建筑", "place": "海边", "function": "停泊", "material": "混凝土", "scale": "大型"},
    {"name": "闸门", "category": "装置", "place": "河上", "function": "控水", "material": "金属", "scale": "中型"},
    {"name": "卫星", "category": "装置", "place": "太空", "function": "通信", "material": "金属", "scale": "大型"},
    {"name": "光缆", "category": "装置", "place": "海面", "function": "通信", "material": "塑料", "scale": "小型"},
    {"name": "塔吊", "category": "装置", "place": "工地", "function": "起重", "material": "金属", "scale": "大型"},
]

ASPECTS: dict[str, list[str]] = {
    "category": sorted({row["category"] for row in CATALOG}),
    "place": sorted({row["place"] for row in CATALOG}),
    "function": sorted({row["function"] for row in CATALOG}),
    "material": sorted({row["material"] for row in CATALOG}),
    "scale": sorted({row["scale"] for row in CATALOG}),
}

OPTIONS: dict[str, Any] = {
    "maxQuestions": 21,
    "catalogSize": len(CATALOG),
}


@dataclass
class _Ask:
    pass


def _matches(row: dict[str, str], aspect: str, value: str) -> bool:
    return row.get(aspect) == value


def _candidates(state: dict[str, Any], asked: list[dict[str, str]] | None = None) -> list[str]:
    rows = CATALOG[: state.get("catalogSize", len(CATALOG))]
    history = state["history"] if asked is None else asked
    kept = []
    for row in rows:
        ok = True
        for item in history:
            if item["answer"] == "yes" and not _matches(row, item["aspect"], item["value"]):
                ok = False
                break
            if item["answer"] == "no" and _matches(row, item["aspect"], item["value"]):
                ok = False
                break
        if ok:
            kept.append(row["name"])
    return kept


class TwentyQuestions:
    game_id = "twenty-questions"

    def init(self, seed: int, players: list[PlayerId], config: dict[str, Any]) -> dict[str, Any]:
        if len(players) != 2:
            raise ValueError("twenty-questions 需要两名选手")
        rng = random.Random(seed)
        catalog = CATALOG[: config.get("catalogSize", len(CATALOG))]
        target = rng.choice(catalog)
        return {
            "seed": seed,
            "players": list(players),
            "target": target["name"],
            "history": [],
            "turnIndex": 0,
            "maxQuestions": config.get("maxQuestions", OPTIONS["maxQuestions"]),
            "catalogSize": config.get("catalogSize", len(CATALOG)),
            "finished": False,
            "winner": None,
            "endReason": "",
        }

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        """公开状态永远只包含提问记录。

        guess 记录里带着答案本身，不能进公共面——否则一次错误猜测之后，
        后续事件的 publicState 会把正确答案一起广播出去。
        """
        history = [
            dict(item) for item in state["history"] if item["kind"] == "ask"
        ]
        return {
            "turnIndex": state["turnIndex"],
            "maxQuestions": state["maxQuestions"],
            "history": history,
            "remainingPublic": len(_candidates(state)),
            "finished": state["finished"],
            "winner": state["winner"],
            "endReason": state["endReason"],
        }

    def observe(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any]:
        if player not in state["players"]:
            raise KeyError(f"unknown player {player}")
        # 关键：target 绝不进入观察。
        return {
            "you": player,
            "opponent": next(p for p in state["players"] if p != player),
            "turnIndex": state["turnIndex"],
            "maxQuestions": state["maxQuestions"],
            "isYourTurn": self.current_player(state) == player,
            "history": [dict(item) for item in state["history"]],
            "yourRemaining": len(_candidates(state)),
            "aspects": ASPECTS,
        }

    def current_player(self, state: dict[str, Any]) -> PlayerId | None:
        if state["finished"]:
            return None
        return state["players"][state["turnIndex"] % 2]

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]:
        moves = [f"ask:{aspect}={value}" for aspect, values in ASPECTS.items() for value in values]
        moves.append("guess:<candidate-name>")
        return moves

    def apply(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        if state["finished"]:
            return MoveResult(legal=False, reason="对局已结束", retryable=False)

        current = self.current_player(state)
        if current != player:
            return MoveResult(legal=False, reason=f"现在轮到 {current}", retryable=False)

        next_state = copy.deepcopy(state)
        handler = {
            "ask": self._apply_ask,
            "guess": self._apply_guess,
            "forfeit": self._apply_forfeit,
        }.get(move.kind)
        if handler is None:
            return MoveResult(legal=False, reason=f"未知动作类型 {move.kind}", retryable=True)
        return handler(next_state, player, move)

    def _apply_ask(self, next_state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        aspect, _, value = move.value.partition("=")
        if aspect not in ASPECTS:
            return MoveResult(legal=False, reason=f"未知属性 {aspect}", retryable=True)
        if value not in ASPECTS[aspect]:
            return MoveResult(legal=False, reason=f"属性 {aspect} 没有取值 {value}", retryable=True)

        effects: list[AppliedEffect] = []
        before = len(_candidates(next_state))
        target_row = next(row for row in CATALOG if row["name"] == next_state["target"])
        answer = "yes" if _matches(target_row, aspect, value) else "no"
        next_state["history"].append(
            {"by": player, "kind": "ask", "aspect": aspect, "value": value, "answer": answer}
        )
        next_state["turnIndex"] += 1

        after = len(_candidates(next_state))
        effects.append(
            AppliedEffect(
                kind="answer",
                detail={"aspect": aspect, "value": value, "answer": answer, "narrowed": before - after},
            )
        )

        if next_state["turnIndex"] >= next_state["maxQuestions"]:
            next_state["finished"] = True
            remaining = _candidates(next_state)
            next_state["winner"] = player if len(remaining) == 1 else None
            next_state["endReason"] = (
                "问满上限，最后一问收敛到唯一答案" if len(remaining) == 1 else "问满上限，仍未收敛"
            )
            effects.append(AppliedEffect(kind="end", detail={"reason": next_state["endReason"]}))

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=effects,
            public_state=self.public_state(next_state),
        )

    def _apply_guess(self, next_state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        guess = move.value.strip()
        correct = guess == next_state["target"]
        next_state["history"].append(
            {"by": player, "kind": "guess", "value": guess, "answer": "win" if correct else "wrong"}
        )
        next_state["finished"] = True
        next_state["winner"] = player if correct else next(p for p in next_state["players"] if p != player)
        next_state["endReason"] = "猜中" if correct else "猜错，直接判负"
        effects = [
            AppliedEffect(kind="guess", detail={"value": guess, "correct": correct}),
            AppliedEffect(kind="end", detail={"reason": next_state["endReason"]}),
        ]
        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=effects,
            public_state=self.public_state(next_state),
        )

    def _apply_forfeit(self, next_state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        next_state["finished"] = True
        next_state["winner"] = next(p for p in next_state["players"] if p != player)
        next_state["endReason"] = "弃权"
        effects = [
            AppliedEffect(kind="forfeit", detail={"by": player}),
            AppliedEffect(kind="end", detail={"reason": "弃权"}),
        ]
        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=effects,
            public_state=self.public_state(next_state),
        )
