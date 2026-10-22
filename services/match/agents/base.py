from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..engine.base import Move

ThoughtSink = Callable[[str], None]


@dataclass
class AgentOutput:
    """选手这一手的完整交代。

    raw 会原样进 action.proposed 的 payload，用于复盘"它到底说了什么"，
    包括解析失败时的原始输出——那本身就是翻车现场的一部分。
    """

    move: Move
    raw: str


class Agent(Protocol):
    name: str

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput: ...
