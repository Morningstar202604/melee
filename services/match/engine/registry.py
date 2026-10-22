"""赛制注册表：一处决定"有哪些游戏可玩"。

两个来源：
- 内置：自己写的确定性引擎（目前是 twenty-questions）。
- 适配：TextArena 的确定性环境（没装 textarena 时不注册，服务照常启动）。

API 层只认这个注册表，不直接 import 具体引擎。
"""

from __future__ import annotations

from typing import Any, Callable

from .base import Game
from .codenames import Codenames
from .twenty_questions import OPTIONS as TQ_OPTIONS
from .twenty_questions import TwentyQuestions
from . import textarena_adapter


_REGISTRY: dict[str, Callable[[], Game]] = {
    "twenty-questions": TwentyQuestions,
    "codenames": Codenames,
}


def _register_textarena() -> None:
    if not textarena_adapter.is_available():
        return
    for game_id in textarena_adapter.SUPPORTED:
        _REGISTRY[game_id] = lambda gid=game_id: textarena_adapter.build(gid)


_register_textarena()


def available() -> list[str]:
    return sorted(_REGISTRY)


def get(game_id: str) -> Game:
    if game_id not in _REGISTRY:
        raise KeyError(game_id)
    return _REGISTRY[game_id]()


def player_range(game_id: str) -> tuple[int, int]:
    """该赛制允许的人数区间。

    内置赛制默认 2 人；TextArena 的从它的 SUPPORTED 表读。
    """
    if game_id in textarena_adapter.SUPPORTED:
        return tuple(textarena_adapter.SUPPORTED[game_id]["players"])  # type: ignore[return-value]
    game = _REGISTRY.get(game_id)
    if game is not None and hasattr(game, "player_range"):
        return tuple(game.player_range)  # type: ignore[return-value]
    return (2, 2)


def catalog() -> list[dict[str, Any]]:
    """给前端与 API 的赛制清单。"""
    out: list[dict[str, Any]] = []
    for game_id in available():
        if game_id in textarena_adapter.SUPPORTED:
            spec = textarena_adapter.SUPPORTED[game_id]
            out.append(
                {
                    "gameId": game_id,
                    "label": spec["label"],
                    "players": list(spec["players"]),
                    "source": "textarena",
                }
            )
        elif game_id == "codenames":
            out.append(
                {
                    "gameId": game_id,
                    "label": "代号",
                    "players": [4, 4],
                    "source": "builtin",
                }
            )
        else:
            out.append(
                {
                    "gameId": game_id,
                    "label": "二十问",
                    "players": [2, 2],
                    "source": "builtin",
                    "maxQuestions": TQ_OPTIONS["maxQuestions"],
                }
            )
    return out
