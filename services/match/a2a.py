"""A2A（Agent2Agent）发现支持：发布 Agent Card。

A2A 规范把 agent 的自我描述固定在 /.well-known/agent-card.json。
melee 的"agent 能力"全部经由 HTTP API + SSE 表达：外部 agent 通过
HTTPAgent 协议参赛，通过 REST/SSE 读流。这张卡把这些入口以生态标准
的形式声明出去，让 A2A 客户端能自动发现 melee 并按能力接入。

不实现 A2A 的 JSON-RPC 消息语义：melee 不是 agent，它是赛场。
声明之外的能力请求应走 HTTPAgent 与 REST——卡片里写清楚这一点，
比假装兼容更有价值。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .messages import LANGS

router = APIRouter()


APP_VERSION = "1.8.0"

_CARD_SKILLS = [
    {
        "id": "run-match",
        "name": "Run a match",
        "description": (
            "Create a match (scripted or BYOK real models) and get a matchId; "
            "deterministic engines reproduce the same match for the same seed."
        ),
        "tags": ["ai-battle", "match", "deterministic"],
    },
    {
        "id": "follow-events",
        "name": "Follow the event stream",
        "resumable": True,
        "description": (
            "REST cursor or SSE for the append-only event stream; "
            "replay and live view share one reducer."
        ),
        "tags": ["streaming", "sse", "events"],
    },
    {
        "id": "audited-recap",
        "name": "Audited recap",
        "description": (
            "One-line result, per-player stats and highlights where every number "
            "is counted from the event stream; mismatches are reported, not hidden."
        ),
        "tags": ["recap", "fact-check"],
    },
    {
        "id": "seasons",
        "name": "Season standings",
        "description": "Round-robin seasons with scheduled starts, standings and rivalries.",
        "tags": ["season", "standings"],
    },
]

_LANG_TAG = {"en": "en", "zh": "zh-CN"}


def agent_card(base_url: str) -> dict[str, Any]:
    """构造 Agent Card；版本与语言清单是运行时事实，不写死在字符串里。"""
    return {
        "name": "melee",
        "description": (
            "Self-hosted broadcast studio for AI matches: one append-only event "
            "stream derives live view, replay, directing, recaps and highlights."
        ),
        "version": APP_VERSION,
        "protocolVersion": "0.3.0",
        "url": base_url.rstrip("/"),
        "provider": {
            "organization": "melee",
            "url": "https://gitee.com/badhope/melee",
        },
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json", "text/event-stream"],
        "skills": _CARD_SKILLS,
        "additionalFields": {
            "openapi": base_url.rstrip("/") + "/openapi.json",
            "sseTemplate": base_url.rstrip("/") + "/matches/{matchId}/stream",
            "eventsTemplate": base_url + "/matches/{matchId}/events?after={seq}",
            "httpAgentProtocol": base_url + "/matches/{matchId}/agent/act",
            "languages": list(LANGS),
            "note": (
                "melee is an arena, not an agent: use REST/SSE for data and the "
                "HTTPAgent protocol to compete. A2A JSON-RPC is not spoken here."
            ),
        },
    }


def _request_base_url(request) -> str:
    """代理链友好：优先 X-Forwarded-Proto/Host，其次请求基址。"""
    proto = request.headers.get("x-forwarded-proto")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if proto and host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


@router.get("/.well-known/agent-card.json", include_in_schema=False)
async def well_known_agent_card(request: Request) -> dict[str, Any]:
    """A2A 发现端点。公开（发现先于鉴权；卡内不含机密）。"""
    return agent_card(_request_base_url(request))
