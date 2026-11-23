from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

PlayerId = str


@dataclass(frozen=True)
class Move:
    kind: Literal["ask", "guess", "forfeit"]
    value: str


@dataclass
class AppliedEffect:
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class MoveResult:
    legal: bool
    next_state: dict[str, Any] | None = None
    reason: str = ""
    retryable: bool = False
    effects: list[AppliedEffect] = field(default_factory=list)
    public_state: dict[str, Any] = field(default_factory=dict)


def legal_result(
    public_state: Callable[[dict[str, Any]], dict[str, Any]],
    next_state: dict[str, Any],
    effects: list[AppliedEffect],
) -> MoveResult:
    """合法落子结果：三个引擎都要带 next_state / effects / 裁剪后的公开态。

    public_state 传 self.public_state（绑定方法），既保留各赛制的裁剪逻辑，
    又不必在每个 apply 分支重复四行构造。
    """
    return MoveResult(
        legal=True,
        next_state=next_state,
        effects=effects,
        public_state=public_state(next_state),
    )


def ensure_turn(
    state: dict[str, Any],
    player: PlayerId,
    current: PlayerId | None,
) -> MoveResult | None:
    """三个引擎共用的回合前置守卫：已结束、或没轮到该玩家时返回拒绝结果。

    以前 twenty_questions / codenames / textarena_adapter 各抄一遍同样的
    两段判断，错误文案与 retryable 口径逐渐分叉。返回 None 表示可以继续。
    """
    if state.get("finished"):
        return MoveResult(legal=False, reason="对局已结束", retryable=False)
    if current != player:
        return MoveResult(legal=False, reason=f"现在轮到 {current}", retryable=False)
    return None


class Game(Protocol):
    """确定性规则引擎。apply 不读时钟、不联网、不用随机数。"""

    game_id: str

    def init(self, seed: int, players: list[PlayerId], config: dict[str, Any]) -> dict[str, Any]: ...

    def observe(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any]:
        """裁剪出该选手可见的观察。隐藏信息绝不能出现在返回值里。"""
        ...

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]: ...

    def current_player(self, state: dict[str, Any]) -> PlayerId | None: ...

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]: ...

    def apply(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult: ...

    def thought_visibility(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any] | None:
        """该选手的内心戏能否公开。

        默认 None = 公开。隐藏信息赛制（如代号队长看得到配色）必须返回
        `{"players": [player]}`，否则队长的推理会连秘密配色一起播给观众。
        """
        ...
