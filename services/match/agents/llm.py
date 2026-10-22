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
from typing import Any

import httpx

from ..engine.base import Move
from .base import AgentOutput, ThoughtSink

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


class LLMAgent:
    name = "llm"

    def __init__(self, name: str, config: LLMConfig, *, client: httpx.Client | None = None) -> None:
        self.name = name
        self.config = config
        self._client = client or httpx.Client(timeout=config.timeout_s)

    def __repr__(self) -> str:  # 防止 Key 出现在日志与异常里
        return f"LLMAgent(name={self.name!r}, model={self.config.model!r}, base_url={self.config.base_url!r})"

    def build_messages(self, observation: dict[str, Any], legal: list[str]) -> list[dict[str, str]]:
        # 按赛制分派：不同赛制的观察结构完全不同。
        # （实测：代号赛制没有 aspects 字段，硬套二十问的提示词会 KeyError。）
        if "secretColors" in observation or observation.get("phase") in {"hint", "guess"}:
            return self._codenames_messages(observation)
        if "aspects" in observation:
            return self._twenty_questions_messages(observation)
        return self._board_messages(observation, legal)

    def _twenty_questions_messages(self, observation: dict[str, Any]) -> list[dict[str, str]]:
        aspects = observation["aspects"]
        options = "\n".join("- " + a + ": " + ", ".join(v) for a, v in aspects.items())
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
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _codenames_messages(self, observation: dict[str, Any]) -> list[dict[str, str]]:
        team = {"red": "红队", "blue": "蓝队"}.get(observation.get("team"), "?")
        phase = observation.get("phase")
        words = observation.get("words") or []
        public = observation.get("publicBoard") or []

        board_lines = []
        for i, row in enumerate(public):
            mark = "已翻开" if row.get("revealed") else "未翻开"
            color = row.get("color")
            color_text = "（" + str(color) + "）" if color else ""
            board_lines.append(str(i + 1) + ". " + str(row.get("word")) + " [" + mark + "]" + color_text)
        board_text = "\n".join(board_lines)

        if phase == "hint":
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
        else:
            hint = observation.get("hint") or {}
            system = (
                "你是" + team + "的队员，看不到词的颜色。\n"
                "请在场上未翻开的词里挑一个最符合队长线索的，格式：pick:<词>\n"
                "只输出 JSON，不要解释：\n"
                '{"kind":"guess","value":"pick:灯塔","thought":"为什么选它"}'
            )
            user = "队长线索：「" + str(hint.get("word")) + "」，指向 " + str(hint.get("count")) + " 个词。\n\n场上：\n" + board_text
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _board_messages(self, observation: dict[str, Any], legal: list[str]) -> list[dict[str, str]]:
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
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        if self.config.stream and on_thought is not None:
            return self._act_streaming(payload, on_thought, legal, started)
        try:
            response = self._client.post(
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            # 上游错误体常带凭据回显，必须先脱敏再落事件。
            detail = _redact(exc.response.text[:500], self.config.api_key)
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=f"[http-{exc.response.status_code}] {detail}",
            )
        except Exception as exc:  # noqa: BLE001 - 任何失败都要变成一次可记录的犯规
            message = _redact(str(exc), self.config.api_key)
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=f"[transport-error] {message}",
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        content = _extract_content(body)
        thought = _extract_thought(body, content)
        if on_thought is not None and thought:
            on_thought(thought)

        move = parse_action(content, legal)
        raw = content if content else f"[empty-content] elapsed={elapsed_ms}ms"
        return AgentOutput(move=move, raw=raw)


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
        buffer = ""

        try:
            with self._client.stream(
                "POST",
                f"{self.config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        piece = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = (piece.get("choices") or [{}])[0].get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning:
                        buffer += reasoning
                        while len(buffer) >= self.config.thought_chunk_chars:
                            on_thought(buffer[: self.config.thought_chunk_chars])
                            buffer = buffer[self.config.thought_chunk_chars :]
                    text = delta.get("content") or ""
                    if text:
                        content.append(text)
        except httpx.HTTPStatusError as exc:
            detail = _redact(exc.response.text[:500], self.config.api_key)
            return AgentOutput(move=Move(kind="ask", value=""), raw=f"[http-{exc.response.status_code}] {detail}")
        except Exception as exc:  # noqa: BLE001
            return AgentOutput(
                move=Move(kind="ask", value=""),
                raw=f"[stream-error] {_redact(str(exc), self.config.api_key)}",
            )

        if buffer:
            on_thought(buffer)

        joined = "".join(content)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return AgentOutput(
            move=parse_action(joined, legal),
            raw=joined or f"[empty-stream] elapsed={elapsed_ms}ms",
        )


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
    """
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
