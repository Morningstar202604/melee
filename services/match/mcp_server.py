"""melee 的 MCP（Model Context Protocol）服务器。

为什么接 MCP：这是当前 agent 生态事实上的工具接入协议——Claude Desktop、
Cursor、自研 agent 都能零胶水地调用这里暴露的工具。melee 的定位是
"AI 对战的转播台"，被 agent 调用是最自然的使用方式之一。

实现选型：官方 python-sdk（mcp 1.x，内置 FastMCP）。不用第三方 fork，
因为官方 SDK 才是规范实现，升级路径可预期。

传输：stdio（IDE/桌面 agent 的标准接法）。所有工具都走 HTTP 调一个
已运行的 melee 服务（MELEE_API，默认本机 8099），保证单一写入方是服务进程，
MCP 服务器本身无状态、可随时重启。

配置：
    MELEE_API     服务地址（默认 http://127.0.0.1:8099）
    MELEE_TOKEN   Bearer token（token 模式部署时必填）

接入示例（Claude Desktop / 任何 MCP 客户端）：
    melee mcp        # 由 exe/CLI 启动 stdio 服务器
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP


def _api_base() -> str:
    return os.environ.get("MELEE_API", "http://127.0.0.1:8099").rstrip("/")


def _token() -> str:
    return os.environ.get("MELEE_TOKEN", "")


def _call(path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 60.0) -> dict:
    headers = {"Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    from .outbound import check_url_components

    check_url_components(_api_base())
    request = urllib.request.Request(
        _api_base() + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - scheme 已由上方 check_url_components 校验
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"melee API {exc.code}: {detail}") from None


def build_server() -> FastMCP:
    mcp = FastMCP(
        "melee",
        instructions=(
            "Tools for the melee broadcast studio: run AI matches, follow their "
            "event streams, read audited recaps and season standings. Every match "
            "is deterministic for a given seed; recaps carry a fact-check that "
            "verifies every number against the event stream."
        ),
    )

    @mcp.tool()
    def list_games() -> dict:
        """List registered game types with player counts and engine source."""
        return _call("/games")

    @mcp.tool()
    def site_stats() -> dict:
        """Site-wide totals: matches, events, per-game counts, current viewers."""
        return _call("/stats")

    @mcp.tool()
    def browse_archive(
        limit: int = 12,
        game: str = "",
        status: str = "",
        q: str = "",
    ) -> dict:
        """Browse the match library. status: live | ended; q searches players or match id."""
        from urllib.parse import urlencode

        params = {"limit": str(max(1, min(limit, 100))), "offset": "0"}
        if game:
            params["game"] = game
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        return _call("/archive?" + urlencode(params))

    @mcp.tool()
    def create_match(game: str, players: list[str], seed: int | None = None) -> dict:
        """Create a match. players: 2-16 player names depending on the game.

        Same seed reproduces the same match for deterministic engines.
        Returns the matchId used by every other tool.
        """
        body = {"game": game, "players": [{"id": name} for name in players]}
        if seed is not None:
            body["seed"] = seed
        return _call("/matches", method="POST", body=body, timeout=90.0)

    @mcp.tool()
    def read_events(match_id: str, after: int = -1) -> dict:
        """Read the event stream from `after` (inclusive cursor). Set after=-1 for the full stream."""
        return _call(f"/matches/{match_id}/events?after={after}")

    @mcp.tool()
    def match_recap(match_id: str, lang: str = "en") -> dict:
        """Audited recap: one-line result, per-player stats, highlights, fact-check problems."""
        return _call(f"/matches/{match_id}/recap?lang={lang}")

    @mcp.tool()
    def season_standings(season_id: str) -> dict:
        """Season standings, fixtures and rivalry records."""
        return _call(f"/seasons/{season_id}")

    return mcp


def main() -> int:
    server = build_server()
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
