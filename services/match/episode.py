"""外部对局导入：把别的 arena 打过的棋局，变成 melee 能重放的事件流。

Why this exists: melee 的强项不是自己生产所有棋局，而是把任何有明确状态和
动作的对局做成一场节目。Kaggle Game Arena、TextArena 这类项目已经产出了
大量 trajectory；它们缺的是导播、回放、战报和高光。这里定义一个最小归一化
层，不重跑引擎，只把外部事实投影成 melee 的事件契约。

支持的输入：
- ``melee.episode/1``：本模块定义的通用格式，适合任何 runner 导出；
- ``kaggle``：Kaggle Game Arena 的 episode JSON（``configuration`` + ``info`` + ``steps``）；
- ``textarena``：TextArena 捕获的 turns/log 快照。

导入后的场次是只读回放：不会调用引擎、不会重新判定胜负。外部事实就是事实。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .clock import utc_now_iso
from .messages import format_message
from .portable import import_match

EPISODE_FORMAT = "melee.episode/1"
SUPPORTED_FORMATS = ("auto", "kaggle", "textarena", "episode", "melee")

# 外部棋盘可能很大，但单场导入仍要守住 portable 的上限。
# 30k 步 × 6 条事件会在内存里堆到二十万条，单场就能把进程拖死；
# 真实 Kaggle 最长一局 109 手，10k 已经给出两个数量级的余量。
MAX_STEPS = 10_000
MAX_ACTION_TEXT = 2_000
MAX_THOUGHT = 2_000
MAX_NARRATION = 240
MAX_EPISODE_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 60_000

_CHESS_PIECES = {
    "r": "♜",
    "n": "♞",
    "b": "♝",
    "q": "♛",
    "k": "♚",
    "p": "♟",
    "R": "♖",
    "N": "♘",
    "B": "♗",
    "Q": "♕",
    "K": "♔",
    "P": "♙",
}


@dataclass
class EpisodeStep:
    player: str
    action: str
    board: list[list[str]] | None = None
    text: str = ""
    thought: str = ""
    at: str | None = None
    duration_sec: float | None = None
    move_from: str = ""
    move_to: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Episode:
    source: str
    game: str
    players: list[str]
    seed: int
    steps: list[EpisodeStep]
    winner: str | None = None
    reason: str = "对局结束"
    final_board: list[list[str]] | None = None
    episode_id: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        return sum(max(0.0, float(step.duration_sec or 0.0)) for step in self.steps)


def parse_episode(document: Any, fmt: str = "auto") -> Episode:
    """解析外部文档；失败时抛 ValueError，消息直接说明缺什么。"""
    if not isinstance(document, dict):
        raise ValueError("episode must be a JSON object")
    fmt = _normalize_format(fmt, document)
    if fmt == "kaggle":
        episode = _parse_kaggle(document)
    elif fmt == "textarena":
        episode = _parse_textarena(document)
    else:
        episode = _parse_generic(document)
    return _finalize(episode)


def import_episode(
    db_path: str | Path,
    document: Any,
    *,
    fmt: str = "auto",
    new_match_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    episode: Episode | None = None,
) -> dict[str, Any]:
    """解析外部对局并写成只读回放场次；返回导入结果与来源元信息。

    `metadata` 是数据集级事实（许可证、原始链接、展示名），不属于单场
    episode 文件；由调用方传入后并入 config，观战页就能给出处。
    """
    if episode is None:
        episode = parse_episode(document, fmt)
    if metadata:
        episode.config = {**episode.config, **metadata}
    result = import_match(db_path, build_export(episode), new_match_id=new_match_id)
    return {
        **result,
        "source": episode.source,
        "game": episode.game,
        "players": episode.players,
        "winner": episode.winner,
        "episodeId": episode.episode_id,
        "steps": len(episode.steps),
        "durationSec": round(episode.duration_sec, 3),
    }


def build_export(episode: Episode, *, finished: bool = True, live: bool = False) -> dict[str, Any]:
    """把一个 Episode 投影成 ``melee.export/1``，交给 portable 落库。

    事件序列固定为：created → started → 每一手（turn/action/state/narration）→ ended。
    不用 runner，也不调用任何引擎；导入端只负责搬运事实。
    """
    now = utc_now_iso()
    end_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
    total_duration = episode.duration_sec
    # 真实 Kaggle 只有每一步的思考耗时，没有绝对时钟。以导入时刻为终点
    # 倒推开场，事件时间轴就能反映这场棋真实下了多久；没有耗时则退回
    # 导入时刻，保持旧行为。
    game_start = end_dt - timedelta(seconds=total_duration) if total_duration > 0 else end_dt
    cursor = game_start
    events: list[dict[str, Any]] = []

    def add(event_type: str, payload: dict[str, Any], player: str | None = None, at: str | None = None) -> int:
        seq = len(events)
        event: dict[str, Any] = {
            "seq": seq,
            "type": event_type,
            "at": at or now,
            "payload": payload,
        }
        if player is not None:
            event["player"] = player
        events.append(event)
        return seq

    # 引擎的 init 会用 seed 决定席位轮转。外部对局的第一手是谁已经写在
    # steps[0] 里，这里把 seed 调成"让第一手坐 0 号位"，保证即便有人用
    # 引擎重放，也不会在第一手就报"现在轮到别人"。原始 seed 留在 config。
    first_player = episode.steps[0].player if episode.steps else (episode.players[0] if episode.players else "")
    engine_seed = episode.players.index(first_player) if first_player in episode.players else 0
    config = {
        **episode.config,
        "source": episode.source,
        "episodeId": episode.episode_id,
        "sourceSeed": episode.seed,
        "imported": True,
        "importedAt": now,
        "durationSec": round(total_duration, 3),
        **({"live": True} if live else {}),
    }
    add(
        "match.created",
        {
            "game": episode.game,
            "players": episode.players,
            "seed": engine_seed,
            "config": config,
        },
        at=game_start.isoformat(),
    )
    add("match.started", {"firstPlayer": first_player}, at=game_start.isoformat())

    history: list[dict[str, Any]] = []
    last_board = episode.final_board
    for index, step in enumerate(episode.steps):
        action = _truncate(step.action, MAX_ACTION_TEXT)
        next_player = episode.players[(index + 1) % len(episode.players)]
        if step.at:
            step_at = step.at
        elif total_duration > 0:
            cursor += timedelta(seconds=max(0.0, float(step.duration_sec or 0.0)))
            step_at = cursor.isoformat()
        else:
            step_at = now
        add("turn.started", {}, player=step.player, at=step_at)
        if step.thought:
            add(
                "thought.delta",
                {"text": _truncate(step.thought, MAX_ACTION_TEXT)},
                player=step.player,
                at=step_at,
            )
        proposed_seq = add(
            "action.proposed",
            {"action": {"kind": "guess", "value": action}, "raw": action},
            player=step.player,
            at=step_at,
        )
        add(
            "action.applied",
            {
                "action": {"kind": "guess", "value": action},
                "effects": [{"kind": "action", "by": step.player, "action": action}],
            },
            player=step.player,
            at=step_at,
        )
        history.append({"by": step.player, "kind": "move", "value": action})
        public_state: dict[str, Any] = {
            "turnIndex": index + 1,
            "history": list(history),
            "currentPlayer": next_player,
        }
        if step.board:
            public_state["gameState"] = {"board": step.board}
            if step.move_from and step.move_to:
                public_state["gameState"]["lastMove"] = {
                    "from": step.move_from,
                    "to": step.move_to,
                }
            last_board = step.board
        elif step.text:
            public_state["gameState"] = {"text": _truncate(step.text, 2000)}
        add(
            "state.changed",
            {"publicState": public_state, "deltas": [{"kind": "action", "by": step.player}]},
            at=step_at,
        )
        # 导入场次也要有镜头语言，否则导播台永远是"未切镜"。
        # 只固定开场一镜；"倒数第二手/最后一手"会随新增步数移动，
        # 实时快照若也这样切，已写入的 cue 位置会和新快照对不上。
        if not live and index == 0:
            _add_imported_cue(add, "wide", step.player, step_at, "cue.imported_start", {})
        elif not live and index == len(episode.steps) - 2:
            _add_imported_cue(
                add, "close", step.player, step_at, "cue.imported_move", {"step": index + 1}
            )
        elif not live and index == len(episode.steps) - 1:
            _add_imported_cue(add, "slowmo", step.player, step_at, "cue.last_move", {})
        narration = _step_narration(step, action)
        add(
            "narration.line",
            {
                "text": narration["text"],
                "tone": "neutral",
                "refs": [proposed_seq],
                "msg": narration["msg"],
                "params": narration["params"],
            },
            at=step_at,
        )

    if len(events) > MAX_EVENTS:
        raise ValueError(f"episode would produce too many events (>{MAX_EVENTS})")

    if not finished:
        return {
            "format": "melee.export/1",
            "game": episode.game,
            "seed": episode.seed,
            "players": episode.players,
            "config": config,
            "createdAt": events[0]["at"],
            "endedAt": None,
            "live": True,
            "events": events,
        }

    final_state: dict[str, Any] = {
        "finished": True,
        "winner": episode.winner,
        # 外部原因可能是任意自然语言；放在 finalState 里保留原文，
        # 对外 reason 用统一键，避免英文界面出现中文句子。
        "sourceReason": episode.reason,
    }
    if last_board:
        final_state["gameState"] = {"board": last_board}
        last_step = episode.steps[-1] if episode.steps else None
        if last_step and last_step.move_from and last_step.move_to:
            final_state["gameState"]["lastMove"] = {
                "from": last_step.move_from,
                "to": last_step.move_to,
            }
    add(
        "match.ended",
        {
            "winner": episode.winner,
            "reason": "对局结束",
            "finalState": final_state,
        },
        at=now,
    )

    return {
        "format": "melee.export/1",
        "game": episode.game,
        "seed": episode.seed,
        "players": episode.players,
        "config": config,
        "createdAt": events[0]["at"],
        "endedAt": now,
        "events": events,
    }


def _add_imported_cue(add, shot: str, target: str, at: str | None, trigger_key: str, params: dict[str, Any]) -> None:
    trigger = format_message(trigger_key, params, lang="en")
    add(
        "director.cue",
        {
            "shot": shot,
            "target": target,
            "durationMs": 3000,
            "trigger": _truncate(trigger, 200),
            "triggerKey": trigger_key,
            "triggerParams": params,
        },
        at=at,
    )


def is_imported(events: list[dict[str, Any]]) -> bool:
    created = next((event for event in events if event.get("type") == "match.created"), None)
    if created is None:
        return False
    config = _as_dict(created.get("payload", {}).get("config"))
    return bool(config.get("imported"))


def summarize_imported(match_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """外部回放的状态快照：只读事件，不重跑引擎。

    外部比赛可能用了 melee 没有的规则（国际象棋、围棋），或者虽然同名但
    动作语义不同；强行用引擎重放会在第一手就失败。这里以 state.changed
    的最后一个公开快照为准，事实与录像一致。
    """
    created = next((event for event in events if event.get("type") == "match.created"), None)
    if created is None:
        raise ValueError(f"{match_id} 缺少 match.created")
    payload = created.get("payload", {})
    public_state: dict[str, Any] = {}
    ended: dict[str, Any] = {}
    for event in events:
        if event.get("type") == "state.changed":
            public_state = _as_dict(event.get("payload", {}).get("publicState"))
        elif event.get("type") == "match.ended":
            ended = _as_dict(event.get("payload"))
    winner = ended.get("winner")
    if winner is None:
        winner = public_state.get("winner")
    return {
        "matchId": match_id,
        "game": payload.get("game") or "unknown",
        "players": list(payload.get("players") or []),
        "finished": bool(ended),
        "winner": winner,
        "currentPlayer": None if ended else public_state.get("currentPlayer"),
        "events": len(events),
        "lastSeq": events[-1]["seq"] if events else -1,
        "turnIndex": public_state.get("turnIndex", len([e for e in events if e.get("type") == "action.applied"])),
        "gameState": public_state.get("gameState") if isinstance(public_state.get("gameState"), dict) else None,
        "source": "import",
    }


def _step_narration(step: EpisodeStep, action: str) -> dict[str, Any]:
    """外部棋局没有解说时给一条可本地化的最小旁白。

    存 ``msg`` + ``params`` 而不是只存中文：英文界面请求同一场时，
    ``localize_payload`` 会按语言重渲染，而不是把中文解说硬塞给英文观众。
    """
    if step.text:
        text = _truncate(step.text, MAX_NARRATION)
        return {"text": text, "msg": "", "params": {}}
    params = {"player": step.player, "action": _truncate(action, 120)}
    return {
        "text": _truncate(format_message("narr.action.now", params, lang="en"), MAX_NARRATION),
        "msg": "narr.action.now",
        "params": params,
    }


def _parse_generic(document: dict[str, Any]) -> Episode:
    steps_raw = document.get("steps", document.get("turns"))
    players = _players_from(document.get("players"))
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("episode needs a non-empty steps/turns list")
    if len(steps_raw) > MAX_STEPS:
        raise ValueError(f"episode has too many steps (>{MAX_STEPS})")

    steps = [_step_from_generic(item, index, players) for index, item in enumerate(steps_raw)]
    # 没有显式 finalBoard 时，最后一手带的 board 就是终局。
    final_board = _board_from_value(document.get("finalBoard") or document.get("final_board"))
    if final_board is None:
        final_board = next((s.board for s in reversed(steps) if s.board), None)
    return Episode(
        source=str(document.get("source") or "external"),
        game=_normalize_game(document.get("game") or ""),
        players=players,
        seed=_as_int(document.get("seed"), 0),
        steps=steps,
        winner=_winner_from(document, players, steps),
        reason=str(document.get("reason") or "对局结束"),
        final_board=final_board,
        episode_id=str(document.get("episodeId") or document.get("id") or ""),
        config=_as_dict(document.get("config")),
    )


def _parse_kaggle(document: dict[str, Any]) -> Episode:
    # Kaggle 数据集文件外面还包了一层 ``root``。API / notebook 里常见的是
    # 裸的 episode，下载下来的 JSON 则是 ``{"root": {...}}``；两种都收。
    root = _as_dict(document.get("root"))
    if root:
        document = root
    config = _as_dict(document.get("configuration") or document.get("config"))
    info = _as_dict(document.get("info"))
    game_raw = (
        config.get("openSpielGameName")
        or document.get("game")
        or info.get("game")
        or ""
    )
    if not game_raw:
        game_string = str(config.get("openSpielGameString") or "")
        game_raw = game_string.split("(", 1)[0]
    game = _normalize_game(game_raw)
    if not game:
        raise ValueError("kaggle episode missing configuration.openSpielGameName")

    players = _players_from(
        info.get("TeamNames")
        or info.get("Agents")
        or document.get("players")
        or config.get("players")
    )
    seed = _as_int(config.get("seed") or document.get("seed"), 0)

    steps: list[EpisodeStep] = []
    raw_steps = document.get("steps")
    state_history = info.get("stateHistory") or document.get("stateHistory")
    if isinstance(raw_steps, list) and raw_steps:
        if len(raw_steps) > MAX_STEPS:
            raise ValueError(f"episode has too many steps (>{MAX_STEPS})")
        for group in raw_steps:
            entries = group if isinstance(group, list) else [group]
            # 真实 Kaggle 的每组 step 里有一份"本方视角"的观察和一份"行动方"
            # 的 action；setup 行的 action 只有 submission=-1，不能当棋步。
            # 只收 actionString / actionApplied 非空的条目。
            entry = next((item for item in entries if _kaggle_action(item)), None)
            if entry is None:
                continue
            steps.append(_step_from_kaggle(entry, len(steps), players))
    if not steps:
        history = info.get("actionHistory") or document.get("actionHistory")
        if not isinstance(history, list) or not history:
            raise ValueError("kaggle episode needs readable steps or info.actionHistory")
        if len(history) > MAX_STEPS:
            raise ValueError(f"episode has too many steps (>{MAX_STEPS})")
        for index, item in enumerate(history):
            steps.append(
                EpisodeStep(
                    player=players[index % len(players)],
                    action=_action_text(item),
                )
            )
    if not steps:
        raise ValueError("kaggle episode has no actions")

    # stateHistory 是"动作发生后的局面"：第一项是初始局面，之后每一项
    # 对应一手。长度正好是动作数 + 1 时才整体套用，避免 newer/older
    # 数据集字段错位；否则退回每条 step 自带的 observation。
    if isinstance(state_history, list) and len(state_history) == len(steps) + 1:
        for index, step in enumerate(steps):
            board = _board_from_value(state_history[index + 1])
            if board:
                step.board = board
            # actionHistory 只有 open_spiel 数值 ID 时，靠前后两个 FEN
            # 反推 SAN 与起止格。python-chess 是可选依赖：没装就保持原值，
            # 不把导入失败变成环境问题。
            if game == "chess":
                move = _chess_move_between(
                    state_history[index], state_history[index + 1]
                )
                if move:
                    san, from_square, to_square = move
                    step.move_from, step.move_to = from_square, to_square
                    if _is_numeric_action(step.action):
                        step.action = san
    durations = info.get("moveDurations") or document.get("moveDurations")
    if isinstance(durations, list) and len(durations) == len(steps):
        for step, duration in zip(steps, durations, strict=False):
            try:
                step.duration_sec = max(0.0, float(duration))
            except (TypeError, ValueError):
                step.duration_sec = None
    final_board = next((s.board for s in reversed(steps) if s.board), None)
    return Episode(
        source="kaggle-game-arena",
        game=game,
        players=players,
        seed=seed,
        steps=steps,
        winner=_kaggle_winner(document, raw_steps, players, steps)
        or _winner_from_rewards(document.get("rewards"), players),
        reason=str(
            document.get("reason")
            or (
                f"Kaggle Game Arena · {document.get('name') or game}"
                if document.get("name") or game
                else "对局结束"
            )
        ),
        final_board=final_board,
        episode_id=str(info.get("EpisodeId") or document.get("id") or ""),
        config={
            "sourceGame": str(game_raw),
            "openSpielGameString": config.get("openSpielGameString", ""),
            "episodeSteps": config.get("episodeSteps"),
            "seed": seed,
        },
    )


def _parse_textarena(document: dict[str, Any]) -> Episode:
    state = _as_dict(document.get("state"))
    players = _players_from(
        document.get("players")
        or state.get("players")
        or state.get("role_mapping")
        or ["Player 0", "Player 1"]
    )
    game = _normalize_game(document.get("game") or document.get("envId") or state.get("env_id") or "")
    if not game:
        raise ValueError("textarena episode missing game/envId")

    raw_steps = document.get("steps") or document.get("turns")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(
            "textarena episode needs steps/turns; a bare state.logs snapshot "
            "does not contain action boundaries"
        )
    if len(raw_steps) > MAX_STEPS:
        raise ValueError(f"episode has too many steps (>{MAX_STEPS})")

    steps = [_step_from_generic(item, index, players) for index, item in enumerate(raw_steps)]
    final_board = _board_from_value(document.get("finalBoard") or document.get("final_board"))
    if final_board is None:
        final_board = _board_from_value(state.get("game_state") or state.get("gameState"))
    if final_board is None:
        final_board = next((s.board for s in reversed(steps) if s.board), None)

    return Episode(
        source="textarena",
        game=game,
        players=players,
        seed=_as_int(document.get("seed") or state.get("seed"), 0),
        steps=steps,
        winner=_winner_from(document, players, steps)
        or _winner_from_rewards(state.get("rewards"), players),
        reason=str(document.get("reason") or "对局结束"),
        final_board=final_board,
        episode_id=str(document.get("episodeId") or document.get("id") or ""),
        config={
            "sourceGame": game,
            "envId": document.get("envId") or state.get("env_id"),
            "turn": state.get("turn"),
        },
    )


def _step_from_generic(item: Any, index: int, players: list[str]) -> EpisodeStep:
    if not isinstance(item, dict):
        raise ValueError(f"step[{index}] must be an object")
    action = _action_text(item.get("action") or item.get("move"))
    if not action:
        raise ValueError(f"step[{index}] missing action/move")
    player = _resolve_player(
        item.get("player", item.get("seat", item.get("by"))), players, index
    )
    board = _board_from_value(item.get("board") or item.get("state") or item.get("observation"))
    text = str(item.get("text") or item.get("comment") or "")
    thought = str(item.get("thought") or item.get("reasoning") or "")
    return EpisodeStep(
        player=player,
        action=action,
        board=board,
        text=text,
        thought=thought,
        at=_as_iso(item.get("at") or item.get("timestamp")),
        raw=item,
    )


def _step_from_kaggle(entry: dict[str, Any], index: int, players: list[str]) -> EpisodeStep:
    action = _kaggle_action(entry)
    if not action:
        raise ValueError(f"kaggle step[{index}] missing action")
    entry_info = _as_dict(entry.get("info"))
    observation_value = entry.get("observation") or entry.get("state") or entry_info.get("observation")
    observation = _as_dict(observation_value)
    # 行动方不是"数组里的第几个"：真实 Kaggle 把对手的观察和行动方的
    # action 放在同一组，且席位可能被 seed 旋转；observation.playerId
    # 才是这一手真正落子的 agent。没有它才退回 info.player / 条目序号。
    player = _resolve_player(
        observation.get("playerId")
        if observation.get("playerId") is not None
        else entry.get("player", entry.get("seat", entry_info.get("player"))),
        players,
        index,
    )
    board = _board_from_value(observation_value) or _board_from_value(entry.get("board"))
    thought = _kaggle_thought(entry)
    text = _text_from_value(observation)
    return EpisodeStep(
        player=player,
        action=action,
        board=board,
        text=text,
        thought=thought,
        at=_as_iso(entry.get("at") or entry.get("timestamp")),
        raw=entry,
    )


def _kaggle_action(entry: Any) -> str:
    """取真实 Kaggle 的一手可读动作，跳过 setup 的 submission=-1。

    ``actionString`` 是 Kaggle 包装层给的 SAN（e4、Nf3）；``actionApplied``
    是 open_spiel 的数值 ID，仅在可读字符串缺失时兜底。没有这两者就不是
    一手棋，不能把 ``{"submission": -1}`` 当动作。
    """
    if not isinstance(entry, dict):
        return ""
    action = _as_dict(entry.get("action"))
    for key in ("actionString", "action_string"):
        value = action.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in ("null", "none", "-1"):
            return value.strip()
    info = _as_dict(entry.get("info"))
    for key in ("actionAppliedToString", "actionSubmittedToString", "actionString"):
        value = info.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() not in ("null", "none", "-1"):
            return value.strip()
    applied = info.get("actionApplied")
    if applied not in (None, "", -1, "-1"):
        return _action_text(applied)
    # 兼容手写的合成样例：action 直接是 "e2e4" 或 {"value": ...}。
    raw = entry.get("action")
    if isinstance(raw, str) and raw.strip() and raw.strip() != "-1":
        return raw.strip()
    if isinstance(raw, (int, float)) and int(raw) != -1:
        return str(raw)
    return ""


def _kaggle_thought(entry: dict[str, Any]) -> str:
    """从 generate_returns 里抽主回答，保留模型当时的判断，最多 2k 字。"""
    info = _as_dict(entry.get("info"))
    for key in ("reasoning", "thought", "thoughts"):
        value = entry.get(key) or info.get(key)
        if isinstance(value, str) and value.strip():
            return _truncate(value.strip(), MAX_THOUGHT)
    returns = _as_dict(entry.get("action")).get("generate_returns")
    if isinstance(returns, list):
        for item in returns:
            if not isinstance(item, str) or not item.strip():
                continue
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError:
                continue
            payload = _as_dict(parsed)
            for key in ("main_response_and_thoughts", "main_response", "response"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return _truncate(value.strip(), MAX_THOUGHT)
    return ""


def _is_numeric_action(value: str) -> bool:
    return bool(value) and value.strip().lstrip("-").isdigit()


def _chess_move_between(previous: Any, following: Any) -> tuple[str, str, str] | None:
    """从两张 FEN 还原 SAN 与起止格。

    Kaggle 的 actionHistory 是 open_spiel 的整数动作；没有 actionString
    时旧实现只能把 ``2426`` 原样显示给观众。只要 stateHistory 完整，
    就可以用 python-chess 逐个尝试合法着法，找出与下一张 FEN 相同的那一手，
    这比安装 pyspiel 轻，也不需要猜 open_spiel 的动作编号表。
    """
    if not isinstance(previous, str) or not isinstance(following, str):
        return None
    if not (_looks_like_fen(previous) and _looks_like_fen(following)):
        return None
    try:
        import chess  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        board = chess.Board(previous)
        target = chess.Board(following)
    except ValueError:
        return None
    if board.turn == target.turn or board.board_fen() == target.board_fen():
        return None
    for move in board.legal_moves:
        san = board.san(move)
        board.push(move)
        matched = (
            board.board_fen() == target.board_fen()
            and board.turn == target.turn
            and board.castling_xfen() == target.castling_xfen()
        )
        uci = move.uci()
        board.pop()
        if matched:
            return san, uci[:2], uci[2:4]
    return None


def _winner_from(document: dict[str, Any], players: list[str], steps: list[EpisodeStep]) -> str | None:
    for key in ("winner", "winningPlayer", "winning_player"):
        value = document.get(key)
        if isinstance(value, str) and value in players:
            return value
        if isinstance(value, int) and 0 <= value < len(players):
            return players[value]
    result = _as_dict(document.get("result"))
    for key in ("winner", "winningPlayer"):
        value = result.get(key)
        if isinstance(value, str) and value in players:
            return value
        if isinstance(value, int) and 0 <= value < len(players):
            return players[value]
    for step in reversed(steps):
        value = step.raw.get("winner")
        if isinstance(value, str) and value in players:
            return value
        if isinstance(value, int) and 0 <= value < len(players):
            return players[value]
    return None


def _kaggle_winner(
    document: dict[str, Any],
    raw_steps: Any,
    players: list[str],
    steps: list[EpisodeStep],
) -> str | None:
    explicit = _winner_from(document, players, steps)
    if explicit:
        return explicit
    info = _as_dict(document.get("info"))
    for key in ("winner", "Winner", "winningPlayer"):
        value = info.get(key)
        if isinstance(value, str) and value in players:
            return value
        if isinstance(value, int) and 0 <= value < len(players):
            return players[value]
    # Kaggle 的终局通常在最后一组 step 里给 reward：1=胜，-1=负，0=和。
    if isinstance(raw_steps, list) and raw_steps:
        last = raw_steps[-1]
        entries = last if isinstance(last, list) else [last]
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            reward = _as_int(entry.get("reward"), 0)
            if reward == 1:
                return _resolve_player(
                    entry.get("player", entry.get("seat", entry.get("info", {}).get("player"))),
                    players,
                    index,
                )
    return None


def _winner_from_rewards(rewards: Any, players: list[str]) -> str | None:
    if isinstance(rewards, dict):
        for key in sorted(rewards, key=lambda item: str(item)):
            value = _as_int(rewards.get(key), 0)
            if value == 1:
                return _resolve_player(key, players, 0)
    if isinstance(rewards, list):
        for index, value in enumerate(rewards):
            if _as_int(value, 0) == 1:
                return _resolve_player(index, players, index)
    return None


def _finalize(episode: Episode) -> Episode:
    if not episode.game:
        raise ValueError("episode missing game")
    if not episode.steps:
        raise ValueError("episode has no steps")
    if not episode.players or len(episode.players) < 2:
        raise ValueError("episode needs at least two players")
    if episode.winner is not None and episode.winner not in episode.players:
        raise ValueError(f"winner {episode.winner!r} is not one of the players")
    if len(episode.players) != len(set(episode.players)):
        raise ValueError("players must be unique")
    if not episode.reason:
        episode.reason = "对局结束"
    return episode


def _normalize_format(fmt: str, document: dict[str, Any]) -> str:
    fmt = (fmt or "auto").strip().lower()
    if fmt in ("melee", "episode"):
        fmt = "episode"
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported episode format: {fmt!r}")
    if fmt != "auto":
        return fmt
    explicit = str(document.get("format") or "").lower()
    if explicit == EPISODE_FORMAT or explicit.startswith("melee.episode"):
        return "episode"
    if "kaggle" in explicit:
        return "kaggle"
    if "textarena" in explicit:
        return "textarena"
    root = _as_dict(document.get("root"))
    candidate = root or document
    if "configuration" in candidate and ("info" in candidate or "steps" in candidate):
        return "kaggle"
    if "turns" in document:
        return "textarena"
    if "steps" in document:
        return "episode"
    raise ValueError(
        "cannot detect episode format; pass format=kaggle|textarena|episode"
    )


def _players_from(value: Any) -> list[str]:
    names: list[str] = []
    if isinstance(value, dict):
        value = [value[key] for key in sorted(value, key=lambda item: str(item))]
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                names.append(item.strip())
            elif isinstance(item, dict):
                name = item.get("Name") or item.get("name") or item.get("id")
                if name:
                    names.append(str(name))
    return _unique(names)


def _unique(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        base = name
        suffix = 2
        while name in seen:
            name = f"{base} #{suffix}"
            suffix += 1
        seen.add(name)
        out.append(name)
    return out


def _resolve_player(value: Any, players: list[str], index: int) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("Name") or value.get("id")
    if isinstance(value, str):
        if value in players:
            return value
        if value.isdigit():
            value = int(value)
        else:
            match = re.search(r"(\d+)$", value)
            if match:
                value = int(match.group(1))
            else:
                return players[index % len(players)]
    if isinstance(value, int) and players:
        return players[value % len(players)]
    return players[index % len(players)]


def _action_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _action_text(value.get("value") or value.get("action") or value.get("move"))
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _has_action(entry: Any) -> bool:
    return isinstance(entry, dict) and bool(
        entry.get("action") not in (None, "", [])
        or _as_dict(entry.get("info")).get("action") not in (None, "", [])
    )


def _board_from_value(value: Any) -> list[list[str]] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("board", "grid", "gameState", "game_state", "state", "position"):
            if key in value:
                board = _board_from_value(value[key])
                if board is not None:
                    return board
        fen = value.get("fen") or value.get("FEN")
        if isinstance(fen, str):
            return _board_from_fen(fen)
        return None
    if isinstance(value, str):
        if _looks_like_fen(value):
            return _board_from_fen(value)
        lines = [line for line in value.splitlines() if line.strip()]
        if len(lines) >= 3 and all(len(line) >= 3 for line in lines):
            return [list(line) for line in lines]
        return None
    if isinstance(value, list):
        if not value:
            return None
        if all(isinstance(row, list) for row in value):
            return [[str(cell) for cell in row] for row in value]
        if all(isinstance(row, str) for row in value):
            return [list(row) for row in value]
        if all(isinstance(cell, (int, str)) for cell in value):
            size = _square_size(len(value))
            if size:
                return [[str(value[row * size + col]) for col in range(size)] for row in range(size)]
    return None


def _text_from_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "message", "observation", "thought", "reasoning"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _board_from_fen(fen: str) -> list[list[str]]:
    ranks = fen.split()[0].split("/")
    board: list[list[str]] = []
    for rank in ranks:
        row: list[str] = []
        for char in rank:
            if char.isdigit():
                row.extend("·" for _ in range(int(char)))
            elif char in _CHESS_PIECES:
                row.append(_CHESS_PIECES[char])
            else:
                row.append(char)
        board.append(row)
    return board


def _looks_like_fen(value: str) -> bool:
    first = value.split()[0]
    return "/" in first and len(first.split("/")) == 8 and bool(re.fullmatch(r"[prnbqkPRNBQK1-8/]+", first))


def _square_size(count: int) -> int | None:
    for size in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 19):
        if size * size == count:
            return size
    return None


def _normalize_game(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("(", 1)[0].strip()
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    text = text.replace("_", "-").replace(" ", "-").lower()
    aliases = {
        "tic-tac-toe": "tic-tac-toe",
        "tictactoe": "tic-tac-toe",
        "connect-four": "connect-four",
        "connect4": "connect-four",
        "connectx": "connect-four",
        "othello": "othello",
        "reversi": "othello",
        "nim": "nim",
        "chess": "chess",
        "go": "go",
        "kuhn-poker": "kuhn-poker",
        "kuhnpoker": "kuhn-poker",
    }
    return aliases.get(text, text)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # contract 只接受 date-time；不合法就让事件用导入时刻，避免整场失败。
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
