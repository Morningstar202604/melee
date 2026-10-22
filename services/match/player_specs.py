"""选手工厂：把请求里的选手声明变成可执行的 agent。

抽出来是因为 API 与 worker 都要用：API 建场、worker 执行任务。
放在这里可以避免 worker 反过来 import api（会形成循环）。
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .agents.llm import LLMAgent, LLMConfig
from .agents.scripted import BoardAgent, CodenamesAgent, GreedyAgent, ScriptedAgent
from .engine.base import Move
from .engine.twenty_questions import CATALOG


class ModelSpec(BaseModel):
    """BYOK：Key 只随本次请求进来，只活在这次比赛的客户端里。"""

    baseUrl: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    apiKey: str = Field(min_length=1, max_length=500, repr=False)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    maxTokens: int = Field(default=400, ge=64, le=4000)
    jsonMode: bool = True

    @field_validator("baseUrl")
    @classmethod
    def _reject_internal_endpoint(cls, value: str) -> str:
        from .outbound import OutboundAddressError, check_base_url

        try:
            return check_base_url(value)
        except OutboundAddressError as exc:
            raise ValueError(str(exc)) from None


class PlayerSpec(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    # 脚本是演示用的，一条最多 200 步、每步 200 字符，防止把队列当存储用。
    script: list[str] = Field(default_factory=list, max_length=200)
    model: ModelSpec | None = None
    delayMs: int = Field(default=0, ge=0, le=10_000, description="每一步之间的间隔，仅演示用")

    @field_validator("script")
    @classmethod
    def _limit_script_length(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 200:
                raise ValueError("每条脚本动作最多 200 字符")
        return values


class PacedAgent:
    """给任意选手加一层节奏控制，纯粹为了让观战有呼吸。"""

    def __init__(self, inner: Any, delay_ms: int) -> None:
        self._inner = inner
        self._delay = delay_ms
        self.name = getattr(inner, "name", "paced")

    def act(self, observation, legal, on_thought=None):
        if self._delay:
            time.sleep(self._delay / 1000)
        return self._inner.act(observation, legal, on_thought)


def build_agents(specs: list[PlayerSpec], game_id: str = "twenty-questions") -> dict[str, Any]:
    """按赛制挑默认选手。

    二十问用属性表贪心；棋类用"从观察里找可用动作"的 BoardAgent。
    """
    default_factory = GreedyAgent if game_id == "twenty-questions" else None
    agents: dict[str, Any] = {}
    for spec in specs:
        if spec.model is not None:
            agents[spec.id] = LLMAgent(
                spec.id,
                LLMConfig(
                    base_url=spec.model.baseUrl,
                    api_key=spec.model.apiKey,
                    model=spec.model.model,
                    temperature=spec.model.temperature,
                    max_tokens=spec.model.maxTokens,
                    json_mode=spec.model.jsonMode,
                ),
            )
        elif spec.script:
            moves = []
            for raw in spec.script:
                kind, _, value = raw.partition(":")
                moves.append(Move(kind=kind, value=value))
            agents[spec.id] = ScriptedAgent(spec.id, moves)
        elif default_factory is GreedyAgent:
            agents[spec.id] = GreedyAgent(spec.id, CATALOG)
        elif game_id == "codenames":
            agents[spec.id] = CodenamesAgent(spec.id)
        else:
            agents[spec.id] = BoardAgent(spec.id, game_id)

        if spec.delayMs:
            agents[spec.id] = PacedAgent(agents[spec.id], spec.delayMs)
    return agents


def build_agents_from_specs(specs: list[PlayerSpec]) -> dict[str, Any]:
    """别名，供 worker 使用；语义与 build_agents 完全一致。"""
    return build_agents(specs)
