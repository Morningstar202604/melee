"""把 OpenAI 兼容端点包成一名选手。

三条纪律（与 melee 旧仓的安全模型一脉相承）：
1. Key 只存在于本次构造的客户端里，不进日志、不进事件、不进提示词。
2. 输出必须被解析成一个合法动作；解析不出来就当作犯规，绝不替模型猜。
3. 失败要留下原始输出，翻车本身也是内容。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from ..engine.base import Move
from .base import AgentOutput, ThoughtSink, redact_sensitive_text

ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["kind", "value"],
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["ask", "guess"]},
        "value": {"type": "string"},
        "thought": {"type": "string"},
    },
}


@dataclass
class LLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    temperature: float = 0.7
    max_tokens: int = 400
    timeout_s: float = 60.0
    json_mode: bool = True
    stream: bool = False
    thought_chunk_chars: int = 24
    # 上游 429 / 5xx / 网络抖动不该立刻算一次犯规：有限次退避重试，
    # 仍失败才落成 action.rejected。上限固定，避免坏端点把一局拖死。
    max_retries: int = 2
    retry_backoff_s: float = 0.8
    # 单场 token 预算：所有请求的 prompt+completion 累加，超了就不再发请求。
    # 免费额度会被慢速耗尽，没有预算时一场比赛能悄悄烧掉几万 token。
    max_total_tokens: int = 60_000
    # ---- 可选能力面（默认值=旧行为；全部是"选手自带配置"）----
    # 自定义人格/策略提示词。约束段（合法动作 + JSON 输出格式）永远由平台
    # 拼接保留，防止选手把自己提示成格式犯规。
    system_prompt: str = ""
    # OpenAI 兼容采样参数；None 表示不下发该字段（端点用自己默认值）。
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] = field(default_factory=list)
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    # 记忆选项：把本选手自己的历史 (action, thought) 拼进 user prompt。
    include_history: bool = False
    # 原生协议：openai（默认）/ anthropic / gemini。
    protocol: str = "openai"


class LLMBudgetExceeded(RuntimeError):
    """单场 token 预算已用尽。runner 收到它会把比赛判成预算终止，而不是普通犯规。"""


class LLMAgent:
    name = "llm"

    def __init__(self, name: str, config: LLMConfig, *, client: httpx.Client | None = None) -> None:
        self.name = name
        self.config = config
        self._client = client or httpx.Client(timeout=config.timeout_s)
        self.tokens_used = 0
        self.requests_made = 0
        self._requests_at_turn_start = 0
        # includeHistory：本选手自己的 (action, thought) 回放，绝不收对手的。
        self._history: list[tuple[str, str]] = []

    def record_history(self, action_text: str, thought: str = "") -> None:
        """跑完一手后由 runner 调用：只记录该选手自己的动作与思考。"""
        if not self.config.include_history:
            return
        if action_text:
            self._history.append((action_text, thought or ""))

    def _history_text(self) -> str:
        lines = []
        for index, (action, thought) in enumerate(self._history, start=1):
            line = str(index) + ". " + action
            if thought:
                line += "（当时的想法：" + thought + "）"
            lines.append(line)
        return "\n".join(lines) if lines else ""

    def _with_history(self, user: str) -> str:
        """includeHistory 开启时把本选手历史拼进 user prompt。

        只回放 (action, thought) 对：不含对手私有信息，不含隐藏字段——
        观察本身仍走 observe() 裁剪，公平性不变。
        """
        if not self.config.include_history or not self._history:
            return user
        return user + "\n\n你在这局里此前的动作记录：\n" + self._history_text()

    def __repr__(self) -> str:  # 防止 Key 出现在日志与异常里
        return f"LLMAgent(name={self.name!r}, model={self.config.model!r}, base_url={self.config.base_url!r})"

    def build_messages(self, observation: dict[str, Any], legal: list[str]) -> list[dict[str, str]]:
        # 按赛制分派：不同赛制的观察结构完全不同。
        # （实测：代号赛制没有 aspects 字段，硬套二十问的提示词会 KeyError。）
        if "secretColors" in observation or observation.get("phase") in {"hint", "guess"}:
            system, user = self._codenames_messages(observation)
        elif "aspects" in observation:
            system, user = self._twenty_questions_messages(observation)
        else:
            system, user = self._board_messages(observation, legal)
        system = _apply_system_prompt(system, self.config.system_prompt)
        user = self._with_history(user)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _twenty_questions_messages(self, observation: dict[str, Any]) -> tuple[str, str]:
        aspects = observation["aspects"]
        options = "\n".join("- " + a + ": " + ", ".join(v) for a, v in aspects.items())
        # 候选名单是公开规则，必须给全：不给名字，模型永远不可能合法猜中，
        # "猜错判负"就成了只有脚本选手才可能触发的规则。
        candidates = observation.get("candidates") or []
        candidate_text = "、".join(str(name) for name in candidates) or "（未知）"
        history = observation.get("history") or []
        lines = []
        for index, item in enumerate(history, start=1):
            if item.get("kind") == "ask":
                mark = "是" if item.get("answer") == "yes" else "否"
                lines.append(str(index) + ". " + str(item.get("by")) + " 问 " + str(item.get("aspect")) + "=" + str(item.get("value")) + " → " + mark)
            else:
                lines.append(str(index) + ". " + str(item.get("by")) + " 报答案 " + str(item.get("value")) + " → " + str(item.get("answer")))
        history_text = "\n".join(lines) if lines else "（还没有人提问）"

        system = (
            "你在玩一局双人「二十问」。有一个秘密物件，你们轮流提问缩小范围。\n"
            "规则：只能问下面列出的属性取值是否成立；也可以直接报答案，但报错立即判负。\n"
            "可选的答案（只能从中选一个报）：\n" + candidate_text + "\n\n"
            "可用属性：\n" + options + "\n\n"
            "只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块：\n"
            '{"kind":"ask","value":"place=海边","thought":"为什么问这个"}\n'
            '或 {"kind":"guess","value":"灯塔","thought":"为什么现在报"}'
        )
        user = (
            "你是 " + str(observation.get("you")) + "，对手是 " + str(observation.get("opponent")) + "。\n"
            "当前第 " + str(observation.get("turnIndex", 0) + 1) + " 手，上限 " + str(observation.get("maxQuestions")) + " 手。\n"
            "已知提问记录：\n" + history_text + "\n"
            "你估计还剩 " + str(observation.get("yourRemaining")) + " 个候选。"
        )
        return system, user

    def _codenames_messages(self, observation: dict[str, Any]) -> tuple[str, str]:
        team = {"red": "红队", "blue": "蓝队"}.get(observation.get("team"), "?")
        board_text = _board_text(observation.get("publicBoard") or [])
        if observation.get("phase") == "hint":
            return _captain_prompt(observation, team, board_text)
        return _operative_prompt(observation, team, board_text)

    def _board_messages(self, observation: dict[str, Any], legal: list[str]) -> tuple[str, str]:
        lines = observation.get("observations") or []
        text = "\n".join(str(x) for x in lines) or "（没有观察）"
        # 合法动作必须明确列出来：只给"格式提示"时，模型会挑已被占用的格子，
        # 两次犯规就弃权。这里把可选项原样贴进提示词，并禁止越界。
        options = "\n".join("- " + str(item) for item in legal) if legal else "（按观察里的 Available Moves 选择）"
        system = (
            "你在玩一局棋类对局。只能从下面列出的可行动作里选一个，原样填进 value：\n"
            + options
            + "\n只输出 JSON，不要解释：\n"
            '{"kind":"guess","value":"[4]","thought":"为什么走这步"}'
        )
        user = "你是 " + str(observation.get("you")) + "，对手是 " + str(observation.get("opponent")) + "。\n" + text
        return system, user


    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        messages = self.build_messages(observation, legal)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        # 可选采样参数：不填不下发，端点走它自己的默认值。
        if self.config.top_p is not None:
            payload["top_p"] = self.config.top_p
        if self.config.seed is not None:
            payload["seed"] = self.config.seed
        if self.config.stop:
            payload["stop"] = list(self.config.stop)
        if self.config.frequency_penalty is not None:
            payload["frequency_penalty"] = self.config.frequency_penalty
        if self.config.presence_penalty is not None:
            payload["presence_penalty"] = self.config.presence_penalty
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        # 记下这一手开始前的请求数，用来把重试成本算进这一手。
        self._requests_at_turn_start = self.requests_made
        started = time.monotonic()
        if self.config.protocol != "openai":
            # anthropic / gemini 走原生协议；流式是 OpenAI 兼容层的能力，
            # 其他协议按非流式一次拿全（配置里的 stream 在这里被忽略）。
            body, failure = self._post_native(messages, started)
            if failure is not None:
                return AgentOutput(
                    move=Move(kind="ask", value=""),
                    raw=redact_sensitive_text(failure, [self.config.api_key]),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            content, thought, usage = _native_content(self.config.protocol, body)
            if on_thought is not None and thought:
                on_thought(thought)
            move = parse_action(content, legal)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return AgentOutput(
                move=move,
                raw=redact_sensitive_text(content, [self.config.api_key]) if content else f"[empty-content] elapsed={elapsed_ms}ms",
                elapsed_ms=elapsed_ms,
                usage=usage,
            )
        if self.config.stream and on_thought is not None:
            return self._act_streaming(payload, on_thought, legal, started)
        body, failure = self._post_with_retry(payload)
        if failure is not None:
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=redact_sensitive_text(failure, [self.config.api_key]),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        content = _extract_content(body)
        thought = _extract_thought(body, content)
        if on_thought is not None and thought:
            on_thought(thought)

        move = parse_action(content, legal)
        raw = redact_sensitive_text(content, [self.config.api_key]) if content else f"[empty-content] elapsed={elapsed_ms}ms"
        return AgentOutput(move=move, raw=raw, elapsed_ms=elapsed_ms, usage=self._usage_snapshot(body))


    def _ensure_budget(self) -> None:
        if self.config.max_total_tokens <= 0:
            return
        if self.tokens_used >= self.config.max_total_tokens:
            raise LLMBudgetExceeded(
                f"本场 token 预算已用尽（{self.tokens_used}/{self.config.max_total_tokens}）"
            )

    def _account(self, payload: dict[str, Any], body: dict[str, Any]) -> None:
        """按上游 usage 记账；没有 usage 就按提示词长度 + max_tokens 估一个上限。"""
        self.requests_made += 1
        usage = body.get("usage") or {}
        total = usage.get("total_tokens")
        if isinstance(total, (int, float)) and total > 0:
            self.tokens_used += int(total)
            return
        # 兼容不返回 usage 的端点：宁可高估，也不要让预算形同虚设。
        prompt_chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages") or [])
        self.tokens_used += self.config.max_tokens + max(0, prompt_chars // 3)

    def _usage_snapshot(self, body: dict[str, Any]) -> dict[str, Any]:
        """这一手真实花掉的量。上游没报 usage 时只回请求数，不编 token 数。"""
        out: dict[str, Any] = {"requests": 1}
        usage = body.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                out[key] = int(value)
        # 上一手到这一手之间可能发生过重试：把它算进这一手的成本。
        out["requests"] = max(1, self.requests_made - self._requests_at_turn_start)
        out["tokensUsedTotal"] = self.tokens_used
        return out

    def _post_with_retry(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """发请求并做有限次退避重试；返回（响应体，失败描述）。"""
        attempts = max(0, self.config.max_retries) + 1
        last = "[transport-error] 未知错误"
        for attempt in range(attempts):
            # 预算检查放在每次尝试之前：重试也要计入，否则 429 风暴会让
            # 实际消耗远超上限。
            self._ensure_budget()
            try:
                response = self._client.post(
                    f"{self.config.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                self._account(payload, body)
                return body, None
            except httpx.HTTPStatusError as exc:
                # 上游错误体常带凭据回显，必须先脱敏再落事件。
                detail = _redact(exc.response.text[:500], self.config.api_key)
                last = f"[http-{exc.response.status_code}] {detail}"
                retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt == attempts - 1:
                    return None, last
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一次可记录的犯规  # aqg: top-level boundary
                last = f"[transport-error] {_redact(str(exc), self.config.api_key)}"
                if attempt == attempts - 1:
                    return None, last
            time.sleep(self.config.retry_backoff_s * (attempt + 1))
        return None, last

    def _post_native(self, messages: list[dict[str, str]], started: float) -> tuple[dict[str, Any] | None, str | None]:
        """anthropic / gemini 原生协议：一次非流式请求，复用同一套预算与重试。

        采样参数按各家语义映射：
        - anthropic: max_tokens 必填；temperature/top_p/stop_sequences 同义；
          penalty 与 seed 无对应字段，不下发（不假装支持）。
        - gemini: generationConfig 里 maxOutputTokens/temperature/topP/stopSeeds/
          candidateCount；seed 用 candidateCount 的近义字段不存在，不下发。
        """
        attempts = max(0, self.config.max_retries) + 1
        last = "[transport-error] 未知错误"
        for attempt in range(attempts):
            self._ensure_budget()
            try:
                if self.config.protocol == "anthropic":
                    url = f"{self.config.base_url.rstrip('/')}/messages"
                    headers = {
                        "x-api-key": self.config.api_key,
                        "anthropic-version": "2023-06-01",
                    }
                    body_req = _anthropic_request(self.config, messages)
                else:
                    url = f"{self.config.base_url.rstrip('/')}/models/{self.config.model}:generateContent"
                    headers = {"x-goog-api-key": self.config.api_key}
                    body_req = _gemini_request(self.config, messages)
                response = self._client.post(url, headers=headers, json=body_req)
                response.raise_for_status()
                body = response.json()
                self._account_native(body_req, body)
                return body, None
            except httpx.HTTPStatusError as exc:
                detail = _redact(exc.response.text[:500], self.config.api_key)
                last = f"[http-{exc.response.status_code}] {detail}"
                retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                if not retryable or attempt == attempts - 1:
                    return None, last
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一次可记录的犯规
                last = f"[transport-error] {_redact(str(exc), self.config.api_key)}"
                if attempt == attempts - 1:
                    return None, last
            time.sleep(self.config.retry_backoff_s * (attempt + 1))
        return None, last

    def _account_native(self, request_body: dict[str, Any], body: dict[str, Any]) -> None:
        """原生协议的记账：anthropic 报 input/output_tokens，gemini 报
        promptTokenCount/candidatesTokenCount；都没有就按提示词长度估算。"""
        self.requests_made += 1
        if self.config.protocol == "anthropic":
            usage = body.get("usage") or {}
            total = _int_or_none(usage.get("input_tokens")) + _int_or_none(usage.get("output_tokens"))
            if total > 0:
                self.tokens_used += total
                return
        else:
            usage = body.get("usageMetadata") or {}
            total = _int_or_none(usage.get("promptTokenCount")) + _int_or_none(usage.get("candidatesTokenCount"))
            if total > 0:
                self.tokens_used += total
                return
        messages = request_body.get("messages") or request_body.get("contents") or []
        prompt_chars = sum(
            len(str(part)) for m in messages for part in (m.get("content"), m.get("parts")) if part
        )
        self.tokens_used += self.config.max_tokens + max(0, prompt_chars // 3)

    def _act_streaming(
        self,
        payload: dict[str, Any],
        on_thought: ThoughtSink,
        legal: list[str],
        started: float,
    ) -> AgentOutput:
        """流式消费：思考增量按块推给调用方，正文最后统一解析。

        这样观众能看见"它正在想"，而不是等整段结束后一次性炸出来。
        分块按字符数切，不按网络分片——网络分片大小不可控，切出来会闪。
        """
        payload = {**payload, "stream": True}
        content: list[str] = []

        try:
            self._ensure_budget()
            with self._client.stream(
                "POST",
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                buffer = self._consume_stream(response.iter_lines(), content, on_thought)
        except httpx.HTTPStatusError as exc:
            detail = _redact(exc.response.text[:500], self.config.api_key)
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=redact_sensitive_text(f"[http-{exc.response.status_code}] {detail}", [self.config.api_key]),
            )
        # aqg: top-level boundary
        except Exception as exc:  # noqa: BLE001
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=redact_sensitive_text(
                    f"[stream-error] {_redact(str(exc), self.config.api_key)}",
                    [self.config.api_key],
                ),
            )

        if buffer:
            on_thought(buffer)

        # 流式响应没有统一 usage：按提示词长度 + max_tokens 的上限记账。
        self.requests_made += 1
        prompt_chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages") or [])
        self.tokens_used += self.config.max_tokens + max(0, prompt_chars // 3)

        joined = "".join(content)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return AgentOutput(
            move=parse_action(joined, legal),
            raw=redact_sensitive_text(joined, [self.config.api_key]) if joined else f"[empty-stream] elapsed={elapsed_ms}ms",
            elapsed_ms=elapsed_ms,
            usage={"requests": 1, "tokensUsedTotal": self.tokens_used},
        )

    def _consume_stream(self, lines: Iterable[str], content: list[str], on_thought: ThoughtSink) -> str:
        """消费 SSE 行，返回还没凑够一块的思考尾巴。"""
        buffer = ""
        for line in lines:
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            reasoning, text = _delta_of(data)
            if reasoning:
                buffer = self._flush_reasoning(buffer + reasoning, on_thought)
            if text:
                content.append(text)
        return buffer

    def _flush_reasoning(self, buffer: str, on_thought: ThoughtSink) -> str:
        chunk = self.config.thought_chunk_chars
        while len(buffer) >= chunk:
            on_thought(buffer[:chunk])
            buffer = buffer[chunk:]
        return buffer


_ENV_REF = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")


def resolve_env_ref(value: str) -> tuple[str | None, str | None]:
    """解析 `{env:VAR}` 形式的凭据引用。

    返回（真实值, None）或（None, 缺失的变量名）。非引用原样返回；
    引用变量不存在时不猜值、不回退——由调用方决定怎么失败。
    """
    match = _ENV_REF.match(value or "")
    if not match:
        return value, None
    import os

    var = match.group(1)
    resolved = os.environ.get(var)
    if resolved is None or not resolved.strip():
        return None, var
    return resolved, None


def _int_or_none(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _anthropic_request(config: "LLMConfig", messages: list[dict[str, str]]) -> dict[str, Any]:
    """OpenAI messages → Anthropic /v1/messages 请求体。"""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    turns = [
        {"role": m["role"], "content": m["content"]}
        for m in messages
        if m["role"] in ("user", "assistant")
    ]
    body: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "messages": turns or [{"role": "user", "content": ""}],
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    body["temperature"] = config.temperature
    if config.top_p is not None:
        body["top_p"] = config.top_p
    if config.stop:
        body["stop_sequences"] = list(config.stop)
    return body


def _gemini_request(config: "LLMConfig", messages: list[dict[str, str]]) -> dict[str, Any]:
    """OpenAI messages → Gemini :generateContent 请求体。"""
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
        if m["role"] in ("user", "assistant")
    ]
    generation: dict[str, Any] = {
        "temperature": config.temperature,
        "maxOutputTokens": config.max_tokens,
    }
    if config.top_p is not None:
        generation["topP"] = config.top_p
    if config.stop:
        generation["stopSequences"] = list(config.stop)
    body: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    body["generationConfig"] = generation
    return body


def _native_content(protocol: str, body: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """从原生协议响应里取（正文, 思考, 用量快照）。解析不出来就返回空。"""
    usage: dict[str, Any] = {"requests": 1}
    if protocol == "anthropic":
        blocks = body.get("content") or []
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        thinking = "".join(
            block.get("thinking", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "thinking"
        )
        block_usage = body.get("usage") or {}
        for key, source in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            value = block_usage.get(source)
            if isinstance(value, (int, float)) and value >= 0:
                usage[key] = int(value)
        if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return text, thinking.strip(), usage
    # gemini
    candidates = body.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    thought = "".join(
        part.get("text", "") for part in parts if isinstance(part, dict) and part.get("thought")
    )
    meta = body.get("usageMetadata") or {}
    for key, source in (("prompt_tokens", "promptTokenCount"), ("completion_tokens", "candidatesTokenCount")):
        value = meta.get(source)
        if isinstance(value, (int, float)) and value >= 0:
            usage[key] = int(value)
    if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    return text, thought.strip(), usage


def _apply_system_prompt(default_system: str, custom: str) -> str:
    """systemPrompt 只替换人格/策略段，输出格式约束必须保留。

    公平性不变量：约束段（"只输出 JSON"）保证自定义提示词的选手与
    默认提示词的选手面对同一条格式规则；观察内容一个字都不动。
    """
    constraint_marker = "只输出"
    if not custom or not custom.strip():
        return default_system
    # 约束段 = 默认 system 里从最后一个"只输出"起的格式要求。
    index = default_system.rfind(constraint_marker)
    constraint = default_system[index:] if index != -1 else (
        "只输出一个 JSON 对象，不要任何解释文字、不要 Markdown 代码块。"
    )
    return custom.strip() + "\n\n" + constraint


def _delta_of(data: str) -> tuple[str, str]:
    """从一行 SSE 数据里取出 (思考增量, 正文增量)。"""
    try:
        piece = json.loads(data)
    except json.JSONDecodeError:
        return "", ""
    delta = (piece.get("choices") or [{}])[0].get("delta") or {}
    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
    return reasoning, delta.get("content") or ""


def _board_text(public: list[dict[str, Any]]) -> str:
    lines = []
    for i, row in enumerate(public):
        mark = "已翻开" if row.get("revealed") else "未翻开"
        color = row.get("color")
        color_text = "（" + str(color) + "）" if color else ""
        lines.append(str(i + 1) + ". " + str(row.get("word")) + " [" + mark + "]" + color_text)
    return "\n".join(lines)


def _captain_prompt(
    observation: dict[str, Any], team: str, board_text: str
) -> tuple[str, str]:
    words = observation.get("words") or []
    secrets = observation.get("secretColors") or []
    mine = [words[i] for i in range(min(len(words), len(secrets))) if secrets[i] == observation.get("team")]
    system = (
        "你是" + team + "的队长，只有你看得到每个词的颜色。\n"
        "你的队伍需要猜出这些词：" + ", ".join(mine) + "\n\n"
        "请给出一条线索 + 数量，格式：hint:<线索词> <数量>\n"
        "规则：线索不能是场上的词，数量是这条线索关联几个己方词（1-9）。\n"
        "只输出 JSON，不要解释：\n"
        '{"kind":"guess","value":"hint:海洋 2","thought":"为什么这样提示"}'
    )
    user = "场上 25 个词：\n" + board_text + "\n\n你是" + team + "队长，给出线索。"
    return system, user


def _operative_prompt(
    observation: dict[str, Any], team: str, board_text: str
) -> tuple[str, str]:
    hint = observation.get("hint") or {}
    system = (
        "你是" + team + "的队员，看不到词的颜色。\n"
        "请在场上未翻开的词里挑一个最符合队长线索的，格式：pick:<词>\n"
        "只输出 JSON，不要解释：\n"
        '{"kind":"guess","value":"pick:灯塔","thought":"为什么选它"}'
    )
    user = "队长线索：「" + str(hint.get("word")) + "」，指向 " + str(hint.get("count")) + " 个词。\n\n场上：\n" + board_text
    return system, user


def _extract_content(body: dict[str, Any]) -> str:
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        return ""
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # 部分兼容端点返回分段内容
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _extract_thought(body: dict[str, Any], content: str) -> str:
    """优先取推理字段；没有就退回 JSON 里的 thought。"""
    try:
        message = body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        message = {}
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    parsed = _try_json(content)
    if isinstance(parsed, dict):
        thought = parsed.get("thought")
        if isinstance(thought, str):
            return thought.strip()
    return ""


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _try_json(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = _JSON_BLOCK.search(text)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return None


def parse_action(content: str, legal: list[str]) -> Move:
    """把模型输出解析成一个动作。

    解析失败时返回一个明显非法的动作，交给 runner 记成 action.rejected。
    这里绝不猜模型"大概想说什么"——那会让犯规消失，也会让比赛失去可复现性。

    唯一的例外：正文去掉空白后**恰好等于某个合法动作**（例如棋类只回了 `[4]`）。
    这不是猜——字符串与 legal_moves 提供的候选逐字节相同，模型已经把动作说清楚了，
    只是没包进 JSON。真实模型联调时这是最常见的一种"格式犯规"。
    """
    text = (content or "").strip()
    if text:
        candidates = {str(item).strip() for item in legal}
        if text in candidates:
            return Move(kind="guess", value=text)
        # 也可能"动作独占第一行、后面是解释"：第一行与合法动作逐字符相同才接受，
        # 仍然不做任何语义猜测。
        first_line = text.splitlines()[0].strip() if text.splitlines() else ""
        if first_line in candidates:
            return Move(kind="guess", value=first_line)

    parsed = _try_json(content)
    if not isinstance(parsed, dict):
        return Move(kind="ask", value="")

    kind = parsed.get("kind")
    value = parsed.get("value")
    if not isinstance(kind, str) or not isinstance(value, str):
        return Move(kind="ask", value="")
    if kind not in ("ask", "guess"):
        return Move(kind="ask", value="")

    value = value.strip()
    if kind == "ask":
        aspect, _, raw_value = value.partition("=")
        value = f"{aspect.strip()}={raw_value.strip()}"
    return Move(kind=kind, value=value)


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "***")
    return text
