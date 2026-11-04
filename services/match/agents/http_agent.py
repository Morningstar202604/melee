"""把任意 HTTP 端点包成一名选手。

为什么需要这一层：
平台此前只能跑"内置脚本选手"和"OpenAI 兼容模型"两类 AI。任何自己写了
agent 框架（LangGraph / CrewAI / 自研循环 / 本地推理服务）的团队都无法参赛，
"把 AI 评进去"这件事就只剩两种官方姿势——这不叫开放评测。

协议（POST，JSON）：

    请求  {"you": "A", "opponent": ["B"], "isYourTurn": true,
           "observations": ["..."], "legalMoves": ["[4]", "[5]"],
           "gameState": {...}, "turnIndex": 3}
    响应  {"move": "[4]"}                     ← 推荐
       或 {"kind": "guess", "value": "[4]"}  ← 兼容 LLM 的格式

安全边界（与 BYOK 完全一致）：
- 出站地址过 `outbound.check_base_url`：拒绝回环/私网/云元数据；
- 密钥只随本次请求，不进事件、不进日志；
- 响应体截断，错误脱敏。

公平性边界（这是本模块最容易出错的地方）：
- **只转发 observe() 已经裁剪过的观察**，绝不把引擎内部 state 发出去；
- 不转发任何其他选手的私有事件；
- 超时/重试与 LLM 选手同一套参数，不因"外部 AI"就放宽。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..engine.base import Move
from .base import AgentOutput, ThoughtSink


@dataclass
class HTTPAgentConfig:
    endpoint: str
    # 鉴权头：可给 Bearer token，也可给任意自定义头（值为密钥时会脱敏）。
    api_key: str = field(default="", repr=False)
    timeout_s: float = 30.0
    max_retries: int = 1
    retry_backoff_s: float = 0.5
    # 响应体上限：防止外部端点返回巨量文本把事件日志撑爆。
    max_response_chars: int = 4000
    # 指定响应格式；留空则两种都接受。
    response_format: str = "auto"


class HTTPAgent:
    """用 HTTP 调用外部 AI。协议见模块 docstring。

    与 LLMAgent 的差异只在"协议怎么谈"，安全与公平约束完全一致：
    同样的超时、同样的重试、同样的观察裁剪。
    """

    name = "http"

    def __init__(
        self,
        name: str,
        config: HTTPAgentConfig,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.name = name
        self.config = config
        self._client = client or httpx.Client(timeout=config.timeout_s)
        self.requests_made = 0
        self.failures = 0
        # 本手 deadline（毫秒）：runner 在设置 turnTimeoutMs 时通过
        # set_deadline 注入；未设置时 payload 不带 deadlineMs 字段。
        self._deadline_ms: int | None = None

    def set_deadline(self, deadline_ms: int | None) -> None:
        self._deadline_ms = deadline_ms

    def __repr__(self) -> str:  # 防止密钥出现在日志与异常里
        return f"HTTPAgent(name={self.name!r}, endpoint={self.config.endpoint!r})"

    def build_payload(self, observation: dict[str, Any], legal: list[str]) -> dict[str, Any]:
        """只转发 observe() 的结果。

        `observation` 是引擎按 visibility 裁剪过的，直接透传即可；
        这里**不碰** runner 持有的 state，也就无从泄露。
        """
        payload = {
            "you": observation.get("you"),
            "opponent": observation.get("opponent"),
            "isYourTurn": observation.get("isYourTurn", True),
            "observations": observation.get("observations") or [],
            "legalMoves": [str(item) for item in (legal or [])],
            "turnIndex": observation.get("turnIndex"),
            "phase": observation.get("phase"),
        }
        # 平台强制的回合墙钟：外部 agent 可据此做多步规划的时间预算。
        if self._deadline_ms is not None:
            payload["deadlineMs"] = self._deadline_ms
        # 棋类/扑克的局面：已按 hidden 表裁剪，可以给。
        if observation.get("gameState") is not None:
            payload["gameState"] = observation["gameState"]
        # 二十问的属性维度是公开规则，给出去才能提问。
        if observation.get("aspects") is not None:
            payload["aspects"] = observation["aspects"]
        if observation.get("candidates") is not None:
            payload["candidates"] = observation["candidates"]
        return payload

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        payload = self.build_payload(observation, legal)
        started = time.monotonic()
        body, failure = self._post(payload)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if failure is not None:
            self.failures += 1
            return AgentOutput(move=Move(kind="ask", value=""), raw=failure, elapsed_ms=elapsed_ms)

        move, thought = _parse_response(body, legal, self.config.max_response_chars)
        if on_thought is not None and thought:
            on_thought(thought)
        return AgentOutput(
            move=move,
            raw=json.dumps(body, ensure_ascii=False)[: self.config.max_response_chars],
            elapsed_ms=elapsed_ms,
            usage={"requests": 1},
        )

    def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        attempts = max(0, self.config.max_retries) + 1
        last = "[transport-error] 未知错误"
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        for attempt in range(attempts):
            self.requests_made += 1
            try:
                response = self._client.post(self.config.endpoint, json=payload, headers=headers)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    return None, f"[bad-response] 期望 JSON 对象，收到 {type(body).__name__}"
                return body, None
            except httpx.HTTPStatusError as exc:
                detail = _redact(exc.response.text[:300], self.config.api_key)
                last = f"[http-{exc.response.status_code}] {detail}"
                retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt == attempts - 1:
                    return None, last
            except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一次可记录的犯规
                last = f"[transport-error] {_redact(str(exc), self.config.api_key)}"
                if attempt == attempts - 1:
                    return None, last
            time.sleep(self.config.retry_backoff_s * (attempt + 1))
        return None, last


def _parse_response(
    body: dict[str, Any], legal: list[str], max_chars: int
) -> tuple[Move, str]:
    """把外部 AI 的响应解析成动作。

    两种格式都接受：
    - `{"move": "[4]"}`                    ← 推荐，最简
    - `{"kind": "guess", "value": "[4]"}`  ← 与 LLM 选手一致

    解析不出来就返回明显非法的动作，交给 runner 记犯规——
    绝不替外部 AI 猜意图，否则"公平"就无从谈起。
    """
    thought = body.get("thought")
    thought_text = thought[:max_chars] if isinstance(thought, str) else ""

    raw_move = body.get("move")
    if isinstance(raw_move, str) and raw_move.strip():
        return Move(kind="guess", value=raw_move.strip()[:max_chars]), thought_text

    kind = body.get("kind")
    value = body.get("value")
    if isinstance(kind, str) and isinstance(value, str) and kind in ("ask", "guess", "forfeit"):
        return Move(kind=kind, value=value.strip()[:max_chars]), thought_text

    # 兜底：如果正文恰好是某个合法动作，原样接受（与 LLM 选手同一条规则）。
    text = str(body.get("action") or body.get("text") or "").strip()
    candidates = {str(item).strip() for item in legal}
    if text and text in candidates:
        return Move(kind="guess", value=text), thought_text

    return Move(kind="ask", value=""), thought_text


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text
