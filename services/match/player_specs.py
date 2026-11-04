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
    """BYOK：Key 只随本次请求进来，只活在这次比赛的客户端里。

    全部新字段可选、默认值等于旧行为：不填的选手与升级前跑法完全一致。
    systemPrompt 只替换人格/策略段，「合法动作 + JSON 输出格式」约束段仍由
    平台拼接，防止选手把自己提示成格式犯规。
    """

    baseUrl: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    # 支持 {env:VAR} 引用：worker 解封后、build_agents 前解析成真实值。
    apiKey: str = Field(min_length=1, max_length=500, repr=False)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    maxTokens: int = Field(default=400, ge=64, le=32000)
    # 单场 token 预算（prompt+completion 累加）；超了整场以"预算用尽"结束。
    maxTotalTokens: int = Field(default=60_000, ge=1000, le=2_000_000)
    jsonMode: bool = True
    # ---- 采样参数（与 temperature 同公平地位：每选手自配，不注入隐藏信息）----
    systemPrompt: str = Field(default="", max_length=4000)
    topP: float | None = Field(default=None, ge=0.0, le=1.0)
    seed: int | None = None
    # stop 序列：≤4 条，每条 ≤64 字符。
    stop: list[str] = Field(default_factory=list, max_length=4)
    frequencyPenalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    presencePenalty: float | None = Field(default=None, ge=-2.0, le=2.0)
    # stream=true 时把思考增量喂给 on_thought（观众能看到"正在想"）。
    stream: bool = False
    # 记忆选项：把本选手自己的历史 (action, thought) 拼进 user prompt。
    # 只回放自己的，绝不带对手私有信息。
    includeHistory: bool = False
    # 原生协议：openai（默认，兼容端点）/ anthropic / gemini。
    protocol: str = Field(default="openai")

    @field_validator("baseUrl")
    @classmethod
    def _reject_internal_endpoint(cls, value: str) -> str:
        from .outbound import OutboundAddressError, check_base_url

        try:
            return check_base_url(value)
        except OutboundAddressError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("stop")
    @classmethod
    def _limit_stop_items(cls, values: list[str]) -> list[str]:
        for value in values:
            if len(value) > 64:
                raise ValueError("每条 stop 序列最多 64 字符")
        return values

    @field_validator("protocol")
    @classmethod
    def _check_protocol(cls, value: str) -> str:
        if value not in ("openai", "anthropic", "gemini"):
            raise ValueError("protocol 只支持 openai / anthropic / gemini")
        return value


class HTTPAgentSpec(BaseModel):
    """外部 AI：任意 HTTP 端点都能参赛（LangGraph / CrewAI / 自研服务）。

    与 BYOK 走同一套出站校验：拒绝回环、私网与云元数据地址。
    """

    endpoint: str = Field(min_length=1, max_length=500)
    # 支持 {env:VAR} 引用：worker 解封后、build_agents 前解析成真实值。
    apiKey: str = Field(default="", max_length=500, repr=False)
    timeoutS: float = Field(default=30.0, ge=1.0, le=600.0)
    maxRetries: int = Field(default=1, ge=0, le=3)

    @field_validator("endpoint")
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
    # 外部 AI：给了它就用 HTTPAgent，不用内置选手。
    http: HTTPAgentSpec | None = None
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

    def set_deadline(self, deadline_ms: int | None) -> None:
        setter = getattr(self._inner, "set_deadline", None)
        if callable(setter):
            setter(deadline_ms)

    def record_history(self, action_text: str, thought: str = "") -> None:
        recorder = getattr(self._inner, "record_history", None)
        if callable(recorder):
            recorder(action_text, thought)


class ApiKeyMissing(ValueError):
    """`{env:VAR}` 引用的环境变量不存在或为空。"""

    def __init__(self, player_id: str, variable: str) -> None:
        self.player_id = player_id
        self.variable = variable
        super().__init__(f"选手 {player_id} 的 apiKey 引用了环境变量 {variable}，但该变量不存在或为空")


def resolve_env_references(specs: list[PlayerSpec]) -> list[PlayerSpec]:
    """把 apiKey 里的 `{env:VAR}` 引用解成真实值（worker 侧，解封之后）。

    环境变量缺失时**不猜值不回退**：抛 ApiKeyMissing，让 worker 把该选手
    按明确原因记成弃权，而不是拿着字面量 `{env:VAR}` 去打上游。
    非引用形式的值原样通过；解引用后的 ModelSpec 不再携带变量名。
    """
    import os
    import re

    pattern = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")
    resolved: list[PlayerSpec] = []
    for spec in specs:
        for block_key in ("model", "http"):
            block = getattr(spec, block_key)
            if block is None:
                continue
            match = pattern.match(block.apiKey or "")
            if not match:
                continue
            var = match.group(1)
            value = os.environ.get(var)
            if value is None or not value.strip():
                raise ApiKeyMissing(spec.id, var)
            block.apiKey = value
        resolved.append(spec)
    return resolved


def build_agents(
    specs: list[PlayerSpec], game_id: str = "twenty-questions", lang: str = "zh"
) -> dict[str, Any]:
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
                    max_total_tokens=spec.model.maxTotalTokens,
                    json_mode=spec.model.jsonMode,
                    system_prompt=spec.model.systemPrompt,
                    top_p=spec.model.topP,
                    seed=spec.model.seed,
                    stop=spec.model.stop,
                    frequency_penalty=spec.model.frequencyPenalty,
                    presence_penalty=spec.model.presencePenalty,
                    stream=spec.model.stream,
                    include_history=spec.model.includeHistory,
                    protocol=spec.model.protocol,
                ),
            )
        elif spec.http is not None:
            from .agents.http_agent import HTTPAgent, HTTPAgentConfig

            agents[spec.id] = HTTPAgent(
                spec.id,
                HTTPAgentConfig(
                    endpoint=spec.http.endpoint,
                    api_key=spec.http.apiKey,
                    timeout_s=spec.http.timeoutS,
                    max_retries=spec.http.maxRetries,
                ),
            )
        elif spec.script:
            moves = []
            for raw in spec.script:
                kind, _, value = raw.partition(":")
                moves.append(Move(kind=kind, value=value))
            agents[spec.id] = ScriptedAgent(spec.id, moves)
        elif default_factory is GreedyAgent:
            agents[spec.id] = GreedyAgent(spec.id, CATALOG, lang=lang)
        elif game_id == "codenames":
            agents[spec.id] = CodenamesAgent(spec.id, lang=lang)
        else:
            agents[spec.id] = BoardAgent(spec.id, game_id, lang=lang)

        if spec.delayMs:
            agents[spec.id] = PacedAgent(agents[spec.id], spec.delayMs)
    return agents


def build_agents_from_specs(specs: list[PlayerSpec]) -> dict[str, Any]:
    """别名，供 worker 使用；语义与 build_agents 完全一致。"""
    return build_agents(specs)
