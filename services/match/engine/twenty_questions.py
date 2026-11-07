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
# 内容双语：同一批 16 个物品，两种语言各一套词。英文界面里问的是
# "category=Building"，不能再出现「建筑」——那是"界面翻了、内容没翻"的
# 半成品本地化（代号棋盘词已按同一原则修过）。
# 两种语言的行必须一一对应：物品数、属性划分完全一致，公平性语义不随语言改变。
_CATALOG_BILINGUAL: list[dict[str, dict[str, str]]] = [
    {"name": {"en": "Lighthouse", "zh": "灯塔"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "Seaside", "zh": "海边"}, "function": {"en": "Navigation", "zh": "导航"}, "material": {"en": "Stone", "zh": "石"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Beacon", "zh": "航标"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Open sea", "zh": "海面"}, "function": {"en": "Navigation", "zh": "导航"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Small", "zh": "小型"}},
    {"name": {"en": "Streetlight", "zh": "路灯"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Street", "zh": "街道"}, "function": {"en": "Lighting", "zh": "照明"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Medium", "zh": "中型"}},
    {"name": {"en": "Bell tower", "zh": "钟楼"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "City", "zh": "城市"}, "function": {"en": "Timekeeping", "zh": "报时"}, "material": {"en": "Stone", "zh": "石"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Signal lamp", "zh": "信号灯"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Street", "zh": "街道"}, "function": {"en": "Navigation", "zh": "导航"}, "material": {"en": "Plastic", "zh": "塑料"}, "scale": {"en": "Small", "zh": "小型"}},
    {"name": {"en": "Water tower", "zh": "水塔"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "City", "zh": "城市"}, "function": {"en": "Water storage", "zh": "储水"}, "material": {"en": "Concrete", "zh": "混凝土"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Windmill", "zh": "风车"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "Field", "zh": "旷野"}, "function": {"en": "Power generation", "zh": "发电"}, "material": {"en": "Wood", "zh": "木"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Watchtower", "zh": "瞭望塔"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "Mountain", "zh": "山地"}, "function": {"en": "Observation", "zh": "观察"}, "material": {"en": "Wood", "zh": "木"}, "scale": {"en": "Medium", "zh": "中型"}},
    {"name": {"en": "Chimney", "zh": "烟囱"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "City", "zh": "城市"}, "function": {"en": "Smoke exhaust", "zh": "排烟"}, "material": {"en": "Brick", "zh": "砖"}, "scale": {"en": "Medium", "zh": "中型"}},
    {"name": {"en": "Antenna", "zh": "天线"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "City", "zh": "城市"}, "function": {"en": "Communication", "zh": "通信"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Small", "zh": "小型"}},
    {"name": {"en": "Bridge pier", "zh": "桥墩"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "River", "zh": "河上"}, "function": {"en": "Support", "zh": "支撑"}, "material": {"en": "Concrete", "zh": "混凝土"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Dock", "zh": "码头"}, "category": {"en": "Building", "zh": "建筑"}, "place": {"en": "Seaside", "zh": "海边"}, "function": {"en": "Mooring", "zh": "停泊"}, "material": {"en": "Concrete", "zh": "混凝土"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Floodgate", "zh": "闸门"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "River", "zh": "河上"}, "function": {"en": "Flow control", "zh": "控水"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Medium", "zh": "中型"}},
    {"name": {"en": "Satellite", "zh": "卫星"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Space", "zh": "太空"}, "function": {"en": "Communication", "zh": "通信"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Large", "zh": "大型"}},
    {"name": {"en": "Fiber cable", "zh": "光缆"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Open sea", "zh": "海面"}, "function": {"en": "Communication", "zh": "通信"}, "material": {"en": "Plastic", "zh": "塑料"}, "scale": {"en": "Small", "zh": "小型"}},
    {"name": {"en": "Tower crane", "zh": "塔吊"}, "category": {"en": "Device", "zh": "装置"}, "place": {"en": "Worksite", "zh": "工地"}, "function": {"en": "Lifting", "zh": "起重"}, "material": {"en": "Metal", "zh": "金属"}, "scale": {"en": "Large", "zh": "大型"}},
]

_LANGS = ("en", "zh")


def catalog_for(lang: str = "zh") -> list[dict[str, str]]:
    """按语言取扁平词表。未知语言回退中文（与历史行为一致）。"""
    code = lang if lang in _LANGS else ("en" if str(lang).lower().startswith("en") else "zh")
    return [
        {field: values.get(code, values["zh"]) for field, values in row.items()}
        for row in _CATALOG_BILINGUAL
    ]


def aspects_for(lang: str = "zh") -> dict[str, list[str]]:
    """按语言推导属性取值表（init 后按 state.lang 使用）。"""
    rows = catalog_for(lang)
    return {
        "category": sorted({row["category"] for row in rows}),
        "place": sorted({row["place"] for row in rows}),
        "function": sorted({row["function"] for row in rows}),
        "material": sorted({row["material"] for row in rows}),
        "scale": sorted({row["scale"] for row in rows}),
    }


# 中文词表：历史事件、老测试与赛季脚本选手都基于它，保持原名可用。
CATALOG: list[dict[str, str]] = catalog_for("zh")
ASPECTS: dict[str, list[str]] = aspects_for("zh")

OPTIONS: dict[str, Any] = {
    "maxQuestions": 21,
    "catalogSize": len(CATALOG),
}


@dataclass
class _Ask:
    pass


def _matches(row: dict[str, str], aspect: str, value: str) -> bool:
    return row.get(aspect) == value


def _lang_of(state: dict[str, Any]) -> str:
    """本局语言。老事件里没有 lang：那些对局本来就是中文词表，回退 zh。"""
    lang = str(state.get("lang") or "zh")
    return "en" if lang.lower().startswith("en") else "zh"


def _candidates(state: dict[str, Any], asked: list[dict[str, str]] | None = None) -> list[str]:
    rows = catalog_for(_lang_of(state))[: state.get("catalogSize", len(CATALOG))]
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
        # 语言必须进 state：词表按语言选取，不记的话重放会用另一套词表
        # 重建目标与属性，整场对不上（codenames 的 lang 泄漏是同类事故）。
        lang = str(config.get("lang") or "zh")
        catalog = catalog_for(lang)[: config.get("catalogSize", len(CATALOG))]
        target = rng.choice(catalog)
        # 公平性一：先手由 seed 决定，不由传入顺序决定。
        # 固定 A 先手会让先手方在整站统计里结构性占优。
        # 公平性二：回合预算取人数的整数倍。21 手会让 A 问 11 次、B 问 10 次。
        first_index = seed % len(players)
        budget = (config.get("maxQuestions", OPTIONS["maxQuestions"]) // len(players)) * len(players)
        return {
            "seed": seed,
            "players": list(players),
            "lang": "en" if lang.lower().startswith("en") else "zh",
            "firstIndex": first_index,
            "target": target["name"],
            "history": [],
            "turnIndex": 0,
            "maxQuestions": max(len(players), budget),
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
        # candidates 是公开规则（赛制的 16 个候选物），必须给全——
        # 旧观察只给属性维度，legal_moves 却发 `guess:<candidate-name>` 占位符，
        # 模型永远不可能合法猜中；唯一的"赢家"是偷用 CATALOG 的脚本选手。
        return {
            "you": player,
            "opponent": next(p for p in state["players"] if p != player),
            "turnIndex": state["turnIndex"],
            "maxQuestions": state["maxQuestions"],
            "isYourTurn": self.current_player(state) == player,
            "history": [dict(item) for item in state["history"]],
            "yourRemaining": len(_candidates(state)),
            # 必须深拷贝：直接返回全局 ASPECTS 时，选手往列表里 append
            # 就能改掉整台服务器的规则表，而且跨对局持续（实测复现）。
            "aspects": copy.deepcopy(aspects_for(_lang_of(state))),
            "candidates": [
                row["name"] for row in catalog_for(_lang_of(state))[: state.get("catalogSize", len(CATALOG))]
            ],
        }

    def current_player(self, state: dict[str, Any]) -> PlayerId | None:
        if state["finished"]:
            return None
        return state["players"][(state["firstIndex"] + state["turnIndex"]) % len(state["players"])]

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]:
        moves = [
            f"ask:{aspect}={value}"
            for aspect, values in aspects_for(_lang_of(state)).items()
            for value in values
        ]
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
        aspects = aspects_for(_lang_of(next_state))
        if aspect not in aspects:
            return MoveResult(legal=False, reason=f"未知属性 {aspect}", retryable=True)
        if value not in aspects[aspect]:
            return MoveResult(legal=False, reason=f"属性 {aspect} 没有取值 {value}", retryable=True)

        effects: list[AppliedEffect] = []
        before = len(_candidates(next_state))
        target_row = next(row for row in catalog_for(_lang_of(next_state)) if row["name"] == next_state["target"])
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
            # 问满上限一律平局：获胜只能靠猜中。
            # 旧规则"最后一问恰好收敛则提问者获胜"把胜利固定给了偶数预算下的
            # 后手方——又是一个结构性偏袒，且它奖励的是提问次数而不是推理。
            next_state["winner"] = None
            next_state["endReason"] = "问满上限，无人猜中"
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
