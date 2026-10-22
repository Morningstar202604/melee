"""TextArena 适配器：把上游的确定性游戏接成我们的 `Game` 协议。

为什么值得接：我们只有 1 个赛制，TextArena 有 110 个游戏环境。
其中一批是**纯确定性**的（TicTacToe / ConnectFour / Othello / Nim / Mastermind
/ GuessWho / Blackjack 等），判定由代码完成，不需要 LLM 主持，正好对上
"判定必须可复现"这条硬约束。

设计要点（都是实测踩出来的，不是照抄文档）：

1. **合法性只能靠状态比对判断**。实测：非法动作时 `env.step` 不抛异常、
   不返回错误、`error_count` 也不变，只是静默忽略并把棋盘留在原状。
   所以适配器在调用前后各深拷贝一次 `game_state` 与当前玩家，两者都没变
   即判为非法。
2. **state 是可变对象**。上游的 `step` 就地修改，这与我们的"纯函数"约定冲突。
   适配器每次 `apply` 都 `deepcopy` 一份新 state 存进去，调用方拿到的旧 state
   永远不会被改。
3. **观察是 (message, type) 元组列表**，直接拼成文本给模型看即可。
4. **textarena 是可选依赖**。没装就整个模块不可用，服务照常启动；
   装了才注册这些赛制。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from .base import AppliedEffect, Move, MoveResult, PlayerId

try:  # 可选依赖：没装 textarena 时本模块仍可导入，只是 is_available() 为 False
    import textarena as ta

    _TEXTARENA_IMPORTED = True
    _IMPORT_ERROR: str | None = None
except Exception as exc:  # noqa: BLE001 - 任何导入失败都视为不可用
    ta = None  # type: ignore[assignment]
    _TEXTARENA_IMPORTED = False
    _IMPORT_ERROR = str(exc)


def is_available() -> bool:
    return _TEXTARENA_IMPORTED


def import_error() -> str | None:
    return _IMPORT_ERROR


# 首批接入：确定性、动作简单、观赏性够。
# key 是我们的 game_id，value 是 TextArena 的 env_id。
# 只收"重置时不需要 LLM 主持人"的环境。这条是实测出来的，不是看文档判断的：
#   Mastermind → 单人游戏（num_players 必须 1），不能当双人对局
#   GuessWho   → 重置时要 OPENROUTER_API_KEY（内部起 LLM 主持人），违反确定性
# 想加新赛制，先跑 `python -m services.match.engine.textarena_probe <env_id>` 验证。
SUPPORTED: dict[str, dict[str, Any]] = {
    "tic-tac-toe": {
        "env": "TicTacToe-v0",
        "label": "井字棋",
        "players": (2, 2),
        "actionHint": "一个格子编号，例如 [4]",
    },
    "connect-four": {
        "env": "ConnectFour-v0",
        "label": "四子棋",
        "players": (2, 2),
        "actionHint": "一列编号，例如 [3]",
    },
    "othello": {
        "env": "Othello-v0",
        "label": "黑白棋",
        "players": (2, 2),
        "actionHint": "行列坐标，例如 [2,3]",
    },
    "nim": {
        "env": "Nim-v0",
        "label": "取石子",
        "players": (2, 2),
        "actionHint": "堆号与数量，例如 [1 2]",
    },
}


@dataclass
class _Snapshot:
    game_state: dict[str, Any]
    current_player: int | None
    done: bool
    rewards: dict[int, int]


def _snapshot(env: Any) -> _Snapshot:
    """抓一份可用于比较与判定的快照。

    实测：上游没有 `state.outcome`，终局结果在 `state.rewards`
    （`{player_id: 1|-1}`，胜者为 1，败者为 -1，平局全 0）。
    """
    state = env.state
    rewards = getattr(state, "rewards", None) or {}
    return _Snapshot(
        game_state=copy.deepcopy(getattr(state, "game_state", {})),
        current_player=getattr(state, "current_player_id", None),
        done=bool(getattr(state, "done", False)),
        rewards={int(k): int(v) for k, v in rewards.items()} if isinstance(rewards, dict) else {},
    )


def _observations(env: Any) -> list[str]:
    """把上游的 (message, type) 列表拼成纯文本。"""
    raw = env.state.get_current_player_observation()
    if isinstance(raw, str):
        return [raw]
    parts: list[str] = []
    for item in raw or []:
        # 实测结构是 (from_id, message, observation_type) 三元组；
        # 早期版本取 item[0] 会拿到 -1，导致模型看到的是 "-1"。
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            parts.append(str(item[1]))
        elif isinstance(item, (list, tuple)) and len(item) == 1:
            parts.append(str(item[0]))
        else:
            parts.append(str(item))
    return parts


_BRACKET = re.compile(r"\[[^\[\]]*\]")
_MOVE_HINT = re.compile(r"(?:Available Moves|Valid moves|Valid targets|Available):\s*(.+)")


def _parse_available(observations: list[str]) -> list[str]:
    """从观察里解析可用动作。

    实测各赛制写法不同：井字棋/黑白棋/取石子带 `Available Moves: '[1]'`，
    四子棋只给棋盘。只看最后一条观察——历史里的动作已经不可用了。
    """
    latest = [str(item) for item in observations[-1:]]
    found: list[str] = []
    for text in latest:
        for match in _MOVE_HINT.finditer(text):
            for item in _BRACKET.finditer(match.group(1)):
                value = item.group(0)
                if value not in found:
                    found.append(value)
    if found:
        return found
    for text in latest:
        for item in _BRACKET.finditer(text):
            value = item.group(0)
            if value not in found and re.fullmatch(r"\[[0-9\s,\.-]+\]", value):
                found.append(value)
    return found


def _columns_from_board(observations: list[str], columns: int = 7) -> list[str]:
    """四子棋：最上面一行的空位就是可落子列。"""
    rows: list[list[str]] = []
    for text in observations:
        for line in str(text).splitlines():
            cells = re.findall(r"[.XO]", line)
            if len(cells) == columns:
                rows.append(cells)
    if not rows:
        return []
    top = rows[0]
    return [f"[col {i}]" for i, cell in enumerate(top) if cell == "."]


def _nim_moves(game_state: dict[str, Any]) -> list[str]:
    """取石子：每堆取 1 个。数量为 0 的堆跳过。"""
    piles = game_state.get("piles") or []
    return [f"[{index} 1]" for index, count in enumerate(piles) if int(count or 0) > 0]


def normalize_action(value: str) -> str | None:
    """把模型输出归一成上游要的 `[...]` 形式。

    每个赛制的动作语法不同（井字棋 `[4]`、黑白棋 `[2,3]`、取石子 `[1 2]`），
    所以这里**不做语义解析**，只做形式归一：
    - 已经带方括号 → 原样返回
    - 纯数字/坐标/空格分隔 → 套上括号
    真正的合法性由上游判定，不由我们猜。
    """
    text = (value or "").strip()
    if not text:
        return None
    # 已经带括号：原样交给上游。各赛制语法差异很大（[4] / [col 1] / [2,3]），
    # 这里不做语义判断，合法性由上游决定。
    if _BRACKET.fullmatch(text):
        return text
    if re.fullmatch(r"[0-9\s,\.-]+", text):
        return f"[{text}]"
    return None


class TextArenaGame:
    """一个 TextArena 环境的 `Game` 适配。

    每个实例绑定一个 env_id；registry 里按 game_id 建实例。
    """

    def __init__(self, game_id: str, env_id: str, label: str, player_range: tuple[int, int]) -> None:
        if not _TEXTARENA_IMPORTED:
            raise RuntimeError(f"未安装 textarena：{_IMPORT_ERROR}")
        self.game_id = game_id
        self.env_id = env_id
        self.label = label
        self.player_range = player_range

    # ---- Game 协议 ----

    def init(self, seed: int, players: list[PlayerId], config: dict[str, Any]) -> dict[str, Any]:
        low, high = self.player_range
        if not (low <= len(players) <= high):
            raise ValueError(f"{self.label} 需要 {low}–{high} 名选手，收到 {len(players)}")

        env = ta.make(env_id=self.env_id)
        env.reset(num_players=len(players), seed=seed)
        snap = _snapshot(env)

        return {
            "seed": seed,
            "players": list(players),
            "env": env,
            "gameState": snap.game_state,
            "finished": snap.done,
            "winner": None,
            "endReason": "",
            "turnIndex": 0,
            "history": [],
        }

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "gameState": copy.deepcopy(state["gameState"]),
            "finished": state["finished"],
            "winner": state["winner"],
            "endReason": state["endReason"],
            # 棋类没有"提问数"，但前端需要"第几步"，所以统一给 turnIndex
            "turnIndex": state.get("turnIndex", 0),
            "history": list(state.get("history") or []),
        }

    def observe(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any]:
        """按当前局面重建一个玩家视角。

        上游的观察依赖"当前轮到谁"，所以这里用一个临时 env 重放历史太重；
        改为直接读 env 并取观察——但只有轮到的选手能拿到自己那份。
        非当前玩家拿到的是局面摘要（不给对手的私密信息）。
        """
        env = state["env"]
        players = state["players"]
        if player not in players:
            raise KeyError(f"unknown player {player}")

        index = players.index(player)
        current = getattr(env.state, "current_player_id", None)

        if current == index:
            return {
                "you": player,
                "opponent": [p for p in players if p != player],
                "isYourTurn": True,
                "observations": _observations(env),
                "gameState": copy.deepcopy(state["gameState"]),
            }

        return {
            "you": player,
            "opponent": [p for p in players if p != player],
            "isYourTurn": False,
            "observations": [],
            "gameState": copy.deepcopy(state["gameState"]),
        }

    def current_player(self, state: dict[str, Any]) -> PlayerId | None:
        if state["finished"]:
            return None
        env = state["env"]
        current = getattr(env.state, "current_player_id", None)
        if current is None:
            return None
        players = state["players"]
        if 0 <= current < len(players):
            return players[current]
        return None

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]:
        """尽量枚举真实合法动作，枚举不出才退回格式提示。

        只给格式提示时，模型会挑已被占用的格子/列，两次犯规就弃权。
        上游观察里通常带 `Available Moves:` / `Valid moves:`，可直接解析；
        四子棋只给棋盘，就按最上面一行的空位推导；取石子给的是每堆数量，
        按"从某堆拿 1 个"枚举（更复杂的拿法仍由上游判定）。
        """
        current = self.current_player(state)
        if current != player:
            return []
        env = state.get("env")
        observations = _observations(env) if env is not None else []
        moves = _parse_available(observations)
        if not moves and self.game_id == "connect-four":
            moves = _columns_from_board(observations)
        if not moves and self.game_id == "nim":
            moves = _nim_moves(state.get("gameState") or {})
        if moves:
            return moves
        hint = SUPPORTED.get(self.game_id, {}).get("actionHint")
        return [hint] if hint else []

    def apply(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        if state["finished"]:
            return MoveResult(legal=False, reason="对局已结束", retryable=False)

        current = self.current_player(state)
        if current != player:
            return MoveResult(legal=False, reason=f"现在轮到 {current}", retryable=False)

        if move.kind == "forfeit":
            loser_index = state["players"].index(player)
            next_state = {**state, "finished": True, "winner": state["players"][1 - loser_index] if len(state["players"]) == 2 else None, "endReason": "弃权"}
            return MoveResult(
                legal=True,
                next_state=next_state,
                effects=[AppliedEffect(kind="forfeit", detail={"by": player})],
                public_state=self.public_state(next_state),
            )

        action = normalize_action(move.value)
        if action is None:
            hint = SUPPORTED.get(self.game_id, {}).get("actionHint", "例如 [4]")
            return MoveResult(legal=False, reason=f"动作格式不对：{hint}", retryable=True)

        # 关键：在克隆体上推进。
        # env 是可变对象，直接在原实例上 step 会让调用方持有的旧 state 一起变化，
        # 违反"apply 纯函数"。deepcopy 一个 env 是可行的（实测通过）。
        env = copy.deepcopy(state["env"])
        before = _snapshot(env)

        try:
            env.step(action)
        except Exception as exc:  # noqa: BLE001 - 上游可能抛，统一转成非法动作
            return MoveResult(legal=False, reason=f"上游拒绝：{exc}", retryable=True)

        after = _snapshot(env)

        # 判非法的唯一可靠依据：局面和当前玩家都没动（上游会静默忽略非法动作）。
        if after.game_state == before.game_state and after.current_player == before.current_player:
            return MoveResult(legal=False, reason="这个位置不合法", retryable=True)

        finished = after.done
        winner = None
        end_reason = ""
        if finished:
            winners = [pid for pid, reward in after.rewards.items() if reward > 0]
            end_reason = "对局结束"
            if len(winners) == 1 and 0 <= winners[0] < len(state["players"]):
                winner = state["players"][winners[0]]
                end_reason = f"{winner} 获胜"
            elif after.rewards and all(reward == 0 for reward in after.rewards.values()):
                end_reason = "平局"

        next_state = {
            **state,
            "env": env,
            "gameState": after.game_state,
            "finished": finished,
            "winner": winner if finished else None,
            "endReason": end_reason,
            "turnIndex": state.get("turnIndex", 0) + 1,
            "history": (state.get("history") or [])
            + [{"by": player, "kind": "move", "value": action}],
        }

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=[AppliedEffect(kind="action", detail={"action": action})],
            public_state=self.public_state(next_state),
        )


def build(game_id: str) -> TextArenaGame:
    spec = SUPPORTED[game_id]
    return TextArenaGame(game_id, spec["env"], spec["label"], spec["players"])


def catalog() -> list[dict[str, Any]]:
    """给 API 用的赛制目录。"""
    if not is_available():
        return []
    return [
        {"gameId": gid, "label": spec["label"], "players": list(spec["players"]), "source": "textarena"}
        for gid, spec in SUPPORTED.items()
    ]
