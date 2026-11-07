"""Agent harness 面：MCP 工具与 A2A 卡片，全部对着真实服务验证。"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.request

import pytest


@pytest.fixture(scope="module")
def live_api():
    import socket
    import tempfile
    from pathlib import Path

    import uvicorn

    from services.match.api import create_app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    db_path = Path(tempfile.mkdtemp(prefix="harness-")) / "h.db"
    app = create_app(db_path=db_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as response:  # nosec B310 - loopback fixture
                if response.status == 200:
                    break
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    else:
        raise RuntimeError("harness fixture service not ready")
    yield base
    server.should_exit = True
    thread.join(timeout=10)


def _mcp_call(tool_name: str, arguments: dict, live_api: str) -> str:
    """直接调 FastMCP 工具函数：工具本身走 HTTP 到 live_api；返回首个文本内容。"""
    from services.match import mcp_server

    os.environ["MELEE_API"] = live_api
    server = mcp_server.build_server()

    async def _call():
        result = await server.call_tool(tool_name, arguments)
        return result

    contents = asyncio.run(_call())
    return contents[0].text


def test_mcp_tools_cover_the_product_surface(live_api):
    from services.match import mcp_server

    os.environ["MELEE_API"] = live_api
    server = mcp_server.build_server()
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert {
        "list_games", "site_stats", "browse_archive", "create_match",
        "read_events", "match_recap", "season_standings",
    } <= tools


def test_mcp_list_games_returns_catalogue(live_api):
    text = _mcp_call("list_games", {}, live_api)
    assert "twenty-questions" in text


def test_mcp_create_and_read_back(live_api):
    text = _mcp_call(
        "create_match",
        {"game": "tic-tac-toe", "players": ["M1", "M2"], "seed": 5},
        live_api,
    )
    assert "matchId" in text
    match_id = json.loads(text)["matchId"]

    deadline = time.time() + 60
    ended = False
    while time.time() < deadline:
        events_text = _mcp_call("read_events", {"match_id": match_id}, live_api)
        if "match.ended" in events_text:
            ended = True
            break
        time.sleep(1)
    assert ended

    recap_text = _mcp_call("match_recap", {"match_id": match_id, "lang": "en"}, live_api)
    assert "wins" in recap_text


def test_a2a_agent_card_shape(live_api):
    with urllib.request.urlopen(live_api + "/.well-known/agent-card.json", timeout=20) as response:  # nosec B310 - loopback fixture
        card = json.loads(response.read().decode())
    assert card["name"] == "melee"
    assert card["capabilities"]["streaming"] is True
    skill_ids = {skill["id"] for skill in card["skills"]}
    assert {"run-match", "follow-events", "audited-recap", "seasons"} <= skill_ids
    assert card["additionalFields"]["languages"] == ["en", "zh"]


def test_a2a_card_honours_forwarded_headers(live_api):
    """反代链路的基址还原：X-Forwarded-Proto/Host 优先于请求基址。"""
    import urllib.request as _u

    request = _u.Request(live_api + "/.well-known/agent-card.json")
    request.add_header("X-Forwarded-Proto", "https")
    request.add_header("X-Forwarded-Host", "arena.example.com")
    with _u.urlopen(request, timeout=20) as response:  # nosec B310 - loopback fixture
        card = json.loads(response.read().decode())
    assert card["url"] == "https://arena.example.com"


def test_mcp_tool_surfaces_api_errors(live_api):
    """工具内的 API 错误以 ToolError 抛给调用方（FastMCP 契约），不静默吞掉。"""
    import pytest as _pt
    from mcp.server.fastmcp.tools.base import ToolError

    os.environ["MELEE_API"] = live_api
    with _pt.raises(ToolError) as excinfo:
        _mcp_call("match_recap", {"match_id": "m_missing"}, live_api)
    assert "404" in str(excinfo.value) and "melee API" in str(excinfo.value)
