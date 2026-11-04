from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from ..engine.base import Move

ThoughtSink = Callable[[str], None]

_API_KEY_SHAPE = re.compile(
    r"(?i)\b(?:sk|rk|pk|xai|AIza|ghp|github_pat|glpat|pplx|gsk)[-_][A-Za-z0-9][A-Za-z0-9._-]{3,}\b"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_URL_USERINFO = re.compile(r"(?i)(https?://)[^/\s?#]+@")
_URL_QUERY = re.compile(r"([?&])[^#\s&=]+=[^#\s&]*")
_URL_FRAGMENT = re.compile(r"#[^\s]+")
_SECRET_FIELD = re.compile(
    r"(?i)((?:[\"'])(?:api[_-]?key|authorization|secret|password|token|credential)(?:[\"'])\s*:\s*)"
    r"(?:[\"'][^\"']*[\"']|[^,}\s]+)"
)


def redact_sensitive_text(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact credentials from text that may be persisted or shown to a caller."""
    value = str(text or "")
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 4 and secret:
            value = value.replace(secret, "***")
    value = _SECRET_FIELD.sub(r"\1\"***\"", value)
    value = _BEARER.sub("Bearer ***", value)
    value = _URL_USERINFO.sub(r"\1***@", value)
    value = _URL_QUERY.sub(r"\1***", value)
    value = _URL_FRAGMENT.sub("#***", value)
    return _API_KEY_SHAPE.sub("***", value)


@dataclass
class AgentOutput:
    """选手这一手的完整交代。

    raw 会原样进 action.proposed 的 payload，用于复盘"它到底说了什么"，
    包括解析失败时的原始输出——那本身就是翻车现场的一部分。

    elapsed_ms / usage 只填真实观测到的值；None 表示"不知道"，不参与统计。
    """

    move: Move
    raw: str
    elapsed_ms: int | None = None
    usage: dict[str, Any] | None = None


class Agent(Protocol):
    name: str

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput: ...
