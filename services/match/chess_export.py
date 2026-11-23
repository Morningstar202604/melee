"""国际象棋的标准出口：把 melee 事件流写成 PGN。

为什么自己拼而不引库：``python-chess`` 是 GPL-3.0，进运行时会把整个服务的
许可证拖下水；它只留在开发依赖里，负责在测试中把导出的 PGN 读回来。
运行时这里只做一件事——把事件流里已经是 SAN 的着法，按 PGN 的词法排成
标准棋谱。合法性在导入阶段由数据源和（开发环境里的）python-chess 校验。
"""

from __future__ import annotations

import re
from typing import Any

from .version import APP_VERSION

_COMMENT_LIMIT = 800
_WRAP = 80
_UCI = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")


def pgn_from_export(document: dict[str, Any]) -> str:
    """把 ``melee.export/1`` 转成 PGN；不是国际象棋或着法不是 SAN 时抛 ValueError。

    只读公开事件：调用方已经通过 ``portable.export_match`` 做可见性裁剪。
    live 场次也可以导出，Result 写 ``*``；这比编造一个终局结果诚实。
    """
    if document.get("game") != "chess":
        raise ValueError("PGN export is only available for chess")
    events = document.get("events") or []
    if not events:
        raise ValueError("match has no events")
    created = events[0].get("payload") or {}
    config = {**(document.get("config") or {}), **(created.get("config") or {})}
    players = list(document.get("players") or created.get("players") or [])
    if len(players) < 2:
        raise ValueError("chess export needs two players")

    ended = next((event for event in reversed(events) if event.get("type") == "match.ended"), None)
    winner = (ended or {}).get("payload", {}).get("winner") if ended else None
    if winner in players:
        result = "1-0" if winner == players[0] else "0-1"
    elif ended:
        result = "1/2-1/2"
    else:
        result = "*"

    headers: list[tuple[str, str]] = [
        ("Event", str(config.get("sourceLabel") or "melee imported chess")),
        ("Site", str(config.get("sourceUrl") or "melee")),
        ("Date", "????.??.??"),  # Kaggle 没有可信绝对时间，不伪造日期
        ("Round", "?"),
        ("White", str(players[0])),
        ("Black", str(players[1])),
        ("Result", result),
        ("Annotator", f"melee {APP_VERSION}"),
    ]
    for key, header in (
        ("source", "Source"),
        ("episodeId", "GameId"),
        ("license", "License"),
    ):
        if config.get(key):
            headers.append((header, str(config[key])))
    if ended:
        final_state = (ended.get("payload") or {}).get("finalState") or {}
        reason = final_state.get("sourceReason") or (ended.get("payload") or {}).get("reason")
        if reason:
            headers.append(("Termination", str(reason)))

    tokens: list[str] = []
    ply = 0
    last_thought = ""
    for event in events:
        event_type = event.get("type")
        payload = event.get("payload") or {}
        if event_type == "thought.delta":
            last_thought = str(payload.get("text") or "")
            continue
        if event_type != "action.applied":
            continue
        action = payload.get("action") or {}
        san = action.get("value") if isinstance(action, dict) else action
        if not isinstance(san, str) or not san.strip():
            raise ValueError("chess action is missing SAN")
        san = san.strip()
        if _UCI.match(san) or san.isdigit():
            raise ValueError(
            f"chess action {san!r} is not SAN; import the source's SAN/actionString "
            "instead of a numeric or UCI move"
            )
        if ply % 2 == 0:
            tokens.append(f"{ply // 2 + 1}.")
        tokens.append(san)
        if last_thought:
            tokens.append("{" + _comment(last_thought) + "}")
            last_thought = ""
        ply += 1

    tokens.append(result)
    body = "\n".join(_wrap(tokens))
    header_text = "\n".join(f'[{name} "{_header(value)}"]' for name, value in headers)
    return header_text + "\n\n" + body + "\n"


def _header(value: str) -> str:
    return " ".join(value.split()).replace("\\", "\\\\").replace('"', '\\"')


def _comment(text: str) -> str:
    """PGN comment 用花括号包住；未转义的 } 会提前结束注释。"""
    clean = " ".join(str(text).split())
    return clean.replace("{", "(").replace("}", ")")[:_COMMENT_LIMIT]


def _wrap(tokens: list[str], width: int = _WRAP) -> list[str]:
    lines: list[str] = []
    current = ""
    for token in tokens:
        if current and len(current) + 1 + len(token) > width:
            lines.append(current)
            current = token
        else:
            current = f"{current} {token}" if current else token
    if current:
        lines.append(current)
    return lines
