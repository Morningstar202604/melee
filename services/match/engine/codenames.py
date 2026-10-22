"""代号（Codenames）· 4 人团队版。

为什么自研而不是接 TextArena：
它的 Codenames 环境依赖 NLTK 的 `words` 语料，本机下载不可用（无外网，
且语料缺失时环境直接构造失败）。规则本身很简单，自己写反而更可控，
也能精确控制"哪些信息对谁可见"——这正是本赛制要验证的东西。

规则（4 人）：
- 两个队，红队 {0,2}，蓝队 {1,3}。
- 每队一名队长（红队 0、蓝队 1），只有队长看得到每个词的颜色。
- 25 个词，其中 9 红 / 8 蓝 / 7 中立 / 1 刺客。
- 队长每回合说「线索词 + 数量」，例如 `hint:海洋 2`。
- 队员每回合猜一个词，例如 `pick:灯塔`；猜中己方颜色可继续猜，
  猜错（对方/中立/刺客）立即换手。
- 先翻出全部己方颜色者胜；翻到刺客立即判负。

行动格式保持极简：`hint:<词> <数字>` 或 `pick:<词>`。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

from .base import AppliedEffect, Move, MoveResult, PlayerId

# 25 个词。选常见名词，便于模型给出有意义的线索。
WORDS: list[str] = [
    "灯塔", "海洋", "沙漠", "森林", "雪山", "火山", "冰川", "草原", "峡谷", "瀑布",
    "太阳", "月亮", "星星", "云朵", "彩虹", "雷雨", "晨雾", "极光", "流星", "潮汐",
    "书页", "油画", "琴弦", "陶罐", "木门", "铁锁", "玻璃", "丝绸", "铜镜", "石碑",
]

_CN = {"red": "红队", "blue": "蓝队"}

TEAM_RED = "red"
TEAM_BLUE = "blue"
TEAM_NEUTRAL = "neutral"
TEAM_ASSASSIN = "assassin"

GRID_SIZE = 25
LAYOUT = {TEAM_RED: 9, TEAM_BLUE: 8, TEAM_NEUTRAL: 7, TEAM_ASSASSIN: 1}


@dataclass
class TeamView:
    team: str
    captain: PlayerId
    agent: PlayerId


def _teams(players: list[PlayerId]) -> list[TeamView]:
    if len(players) != 4:
        raise ValueError("codenames 需要 4 名选手")
    return [
        TeamView(team=TEAM_RED, captain=players[0], agent=players[2]),
        TeamView(team=TEAM_BLUE, captain=players[1], agent=players[3]),
    ]


def _team_of(players: list[PlayerId], player: PlayerId) -> str | None:
    for view in _teams(players):
        if player in (view.captain, view.agent):
            return view.team
    return None


def _is_captain(players: list[PlayerId], player: PlayerId) -> bool:
    return any(view.captain == player for view in _teams(players))


class Codenames:
    game_id = "codenames"
    player_range = (4, 4)

    def init(self, seed: int, players: list[PlayerId], config: dict[str, Any]) -> dict[str, Any]:
        _teams(players)  # 校验人数
        rng = random.Random(seed)
        pool = list(WORDS)
        rng.shuffle(pool)
        board = pool[:GRID_SIZE]

        colors: list[str] = (
            [TEAM_RED] * LAYOUT[TEAM_RED]
            + [TEAM_BLUE] * LAYOUT[TEAM_BLUE]
            + [TEAM_NEUTRAL] * LAYOUT[TEAM_NEUTRAL]
            + [TEAM_ASSASSIN] * LAYOUT[TEAM_ASSASSIN]
        )
        rng.shuffle(colors)

        return {
            "seed": seed,
            "players": list(players),
            "words": board,
            "colors": colors,
            "revealed": [False] * GRID_SIZE,
            "turn": TEAM_RED,
            "phase": "hint",          # hint | guess
            "hint": None,             # {"word": str, "count": int}
            "remainingGuesses": 0,
            "finished": False,
            "winner": None,
            "endReason": "",
            "turnIndex": 0,
            "history": [],
        }

    # ---- 视图裁剪 ----

    def _public_board(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "word": state["words"][i],
                "revealed": state["revealed"][i],
                # 未翻开的词不暴露颜色；翻开的才公开
                "color": state["colors"][i] if state["revealed"][i] else None,
            }
            for i in range(GRID_SIZE)
        ]

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "board": self._public_board(state),
            "turn": state["turn"],
            "phase": state["phase"],
            "hint": state["hint"],
            # 观众需要知道现在轮到哪一队、是队长给线索还是队员猜词，
            # 否则棋盘再漂亮也读不出"这一步在等谁"。
            "currentPlayer": self.current_player(state),
            "remaining": {
                TEAM_RED: sum(
                    1 for i in range(GRID_SIZE)
                    if state["colors"][i] == TEAM_RED and not state["revealed"][i]
                ),
                TEAM_BLUE: sum(
                    1 for i in range(GRID_SIZE)
                    if state["colors"][i] == TEAM_BLUE and not state["revealed"][i]
                ),
            },
            "finished": state["finished"],
            "winner": state["winner"],
            "endReason": state["endReason"],
            "turnIndex": state["turnIndex"],
            "history": list(state.get("history") or []),
        }

    def observe(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any]:
        players = state["players"]
        team = _team_of(players, player)
        if team is None:
            raise KeyError(f"unknown player {player}")

        captain = _is_captain(players, player)
        my_turn = self.current_player(state) == player

        payload: dict[str, Any] = {
            "you": player,
            "team": team,
            "isCaptain": captain,
            "isYourTurn": my_turn,
            "turn": state["turn"],
            "phase": state["phase"],
            "words": list(state["words"]),
            "revealed": list(state["revealed"]),
            "publicBoard": self._public_board(state),
            "hint": state["hint"],
            "remaining": self.public_state(state)["remaining"],
        }

        # 只有队长能看到完整配色（这是本赛制的隐藏信息核心）
        if captain:
            payload["secretColors"] = list(state["colors"])

        return payload

    def current_player(self, state: dict[str, Any]) -> PlayerId | None:
        if state["finished"]:
            return None
        players = state["players"]
        for view in _teams(players):
            if view.team != state["turn"]:
                continue
            return view.captain if state["phase"] == "hint" else view.agent
        return None

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]:
        if state["phase"] == "hint":
            return ["hint:<线索词> <数量>"]
        # 已翻开的词不能再选：把 25 个词全给出去时，模型会反复挑已翻开的，
        # 连犯两次规就弃权，观众看到的是"AI 突然认输"。
        return [
            f"pick:{word}"
            for word, revealed in zip(state["words"], state["revealed"])
            if not revealed
        ]

    # ---- 动作 ----

    def apply(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        if state["finished"]:
            return MoveResult(legal=False, reason="对局已结束", retryable=False)

        current = self.current_player(state)
        if current != player:
            return MoveResult(legal=False, reason=f"现在轮到 {current}", retryable=False)

        if move.kind == "forfeit":
            players = state["players"]
            team = _team_of(players, player)
            next_state = copy.deepcopy(state)
            next_state["finished"] = True
            next_state["winner"] = TEAM_BLUE if team == TEAM_RED else TEAM_RED
            next_state["endReason"] = "弃权"
            return MoveResult(
                legal=True,
                next_state=next_state,
                effects=[AppliedEffect(kind="forfeit", detail={"by": player, "team": team})],
                public_state=self.public_state(next_state),
            )

        if state["phase"] == "hint":
            return self._apply_hint(state, player, move)
        return self._apply_pick(state, player, move)

    def _apply_hint(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        raw = (move.value or "").strip()
        if raw.lower().startswith("hint:"):
            raw = raw[5:].strip()

        parts = raw.rsplit(" ", 1)
        if len(parts) != 2:
            return MoveResult(legal=False, reason="线索格式应为「词 数量」，例如 hint:海洋 2", retryable=True)

        clue, count_text = parts[0].strip(), parts[1].strip()
        if not clue or " " in clue:
            return MoveResult(legal=False, reason="线索只能是一个词", retryable=True)
        try:
            count = int(count_text)
        except ValueError:
            return MoveResult(legal=False, reason="数量必须是整数", retryable=True)
        if not 1 <= count <= 9:
            return MoveResult(legal=False, reason="数量应在 1–9 之间", retryable=True)
        if clue in state["words"]:
            return MoveResult(legal=False, reason="线索不能是场上的词", retryable=True)

        next_state = copy.deepcopy(state)
        next_state["hint"] = {"word": clue, "count": count, "by": player}
        next_state["history"] = (state.get("history") or []) + [
            {"by": player, "kind": "hint", "value": f"{clue} {count}"}
        ]
        next_state["phase"] = "guess"
        next_state["remainingGuesses"] = count + 1  # 允许多猜一次
        next_state["turnIndex"] += 1

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=[AppliedEffect(kind="hint", detail={"word": clue, "count": count})],
            public_state=self.public_state(next_state),
        )

    def _apply_pick(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        raw = (move.value or "").strip()
        if raw.lower().startswith("pick:"):
            raw = raw[5:].strip()

        if raw not in state["words"]:
            return MoveResult(legal=False, reason="场上没有这个词", retryable=True)

        index = state["words"].index(raw)
        if state["revealed"][index]:
            return MoveResult(legal=False, reason="这个词已经翻开", retryable=True)

        next_state = copy.deepcopy(state)
        next_state["revealed"][index] = True
        next_state["turnIndex"] += 1
        next_state["history"] = (state.get("history") or []) + [
            {"by": player, "kind": "pick", "value": raw, "color": next_state["colors"][index]}
        ]
        color = next_state["colors"][index]
        team = state["turn"]

        effects = [
            AppliedEffect(
                kind="reveal",
                detail={"word": raw, "color": color, "team": team},
            )
        ]

        winner: str | None = None
        reason = ""

        if color == TEAM_ASSASSIN:
            winner = TEAM_BLUE if team == TEAM_RED else TEAM_RED
            reason = f"翻到刺客「{raw}」，{_CN[team]}立即判负"
        elif color == team:
            left = sum(
                1
                for i in range(GRID_SIZE)
                if next_state["colors"][i] == team and not next_state["revealed"][i]
            )
            if left == 0:
                winner = team
                reason = f"{_CN[team]}翻完全部己方词"
            else:
                next_state["remainingGuesses"] -= 1
                if next_state["remainingGuesses"] <= 0:
                    next_state["turn"] = TEAM_BLUE if team == TEAM_RED else TEAM_RED
                    next_state["phase"] = "hint"
                    next_state["hint"] = None
        else:
            # 猜错：换手，回到对方的线索阶段
            next_state["turn"] = TEAM_BLUE if team == TEAM_RED else TEAM_RED
            next_state["phase"] = "hint"
            next_state["hint"] = None

        if winner is not None:
            next_state["finished"] = True
            next_state["winner"] = winner
            next_state["endReason"] = reason

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=effects,
            public_state=self.public_state(next_state),
        )
