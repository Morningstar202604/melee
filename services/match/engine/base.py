from __future__ import annotations

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
