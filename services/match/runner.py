from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .engine.base import Game, Move, PlayerId


class EventLogLike(Protocol):
    def append(self, event_type: str, payload: dict[str, Any], *, player: str | None = None, at: str | None = None) -> dict[str, Any]: ...

    def mark_ended(self) -> None: ...


@dataclass
class RunnerResult:
    winner: PlayerId | None
    endReason: str
    turns: int
    final_state: dict[str, Any]


class Runner:
    """驱动一局比赛：取当前选手 → 问动作 → 引擎判定 → 写事件。

    事实只在 engine 里产生，只在 runner 里落成事件；选手看不到事件日志。
    """

    def __init__(
        self,
        game: Game,
        log: EventLogLike,
        agents: dict[PlayerId, Any],
        *,
        narrator: Any | None = None,
        director: Any | None = None,
        max_attempts_per_turn: int = 2,
        max_turns: int = 200,
        deadline_ms: int = 30_000,
        max_thought_chars: int = 4000,
        resume: bool = False,
    ) -> None:
        self.game = game
        self.log = log
        self.agents = agents
        self.narrator = narrator
        self.director = director
        self.max_attempts_per_turn = max_attempts_per_turn
        self.max_turns = max_turns
        self.deadline_ms = deadline_ms
        self.max_thought_chars = max_thought_chars
        # resume=True 表示日志里已有前半段：不再补写 match.created / match.started。
        self.resume = resume
        self._last_leader: str | None = None
        self._steps_since_situation = 0

    def play(self, state: dict[str, Any]) -> RunnerResult:
        players = list(state["players"])
        if not self.resume:
            self.log.append(
                "match.created",
                {
                    "game": self.game.game_id,
                    "players": players,
                    "seed": state["seed"],
                    "config": {
                        key: state[key]
                        for key in ("maxQuestions",)
                        if key in state
                    },
                },
            )
            first = self.game.current_player(state) or players[0]
            self.log.append("match.started", {"firstPlayer": first})

        turns = 0
        round_no = 1
        while not state["finished"]:
            if turns >= self.max_turns:
                return self._finish(state, "超过最大回合数，强制结束", None, turns)

            player = self.game.current_player(state)
            if player is None:
                break

            if turns % 2 == 0:
                self.log.append("round.started", {"round": round_no, "activePlayers": players})
            self.log.append("turn.started", {"deadlineMs": self.deadline_ms}, player=player)

            observation = self.game.observe(state, player)
            legal = self.game.legal_moves(state, player)
            thought_seen = 0

            def on_thought(text: str, *, _player: PlayerId = player) -> None:
                nonlocal thought_seen
                if not text:
                    return
                text = text[: self.max_thought_chars]
                self.log.append("thought.delta", {"text": text}, player=_player)
                thought_seen += 1
                if self.director is not None:
                    cue = self.director.cue_for_thought(_player, text)
                    if cue is not None:
                        self._append_cue(cue)

            result = None
            move: Move | None = None
            for _attempt in range(self.max_attempts_per_turn):
                output = self.agents[player].act(observation, legal, on_thought)
                move = output.move
                self.log.append(
                    "action.proposed",
                    {"action": {"kind": move.kind, "value": move.value}, "raw": output.raw[:4000]},
                    player=player,
                )
                result = self.game.apply(state, player, move)
                if result.legal:
                    break
                self.log.append(
                    "action.rejected",
                    {"reason": result.reason, "retryable": result.retryable},
                    player=player,
                )
                if not result.retryable:
                    break

            if result is not None and result.legal and result.next_state is not None:
                state = result.next_state
                self.log.append(
                    "action.applied",
                    {
                        "action": {"kind": move.kind, "value": move.value},
                        "effects": [{"kind": e.kind, **e.detail} for e in result.effects],
                    },
                    player=player,
                )
                self.log.append(
                    "state.changed",
                    {"publicState": result.public_state, "deltas": _deltas(result.effects)},
                )
                if self.director is not None:
                    cue = self.director.cue_for(
                        player=player,
                        effects=result.effects,
                        public_state=result.public_state,
                        finished=bool(state["finished"]),
                    )
                    if cue is not None:
                        self._append_cue(cue)
                if self.narrator is not None:
                    line = self.narrator.line(
                        result.public_state,
                        player,
                        move,
                        result.effects,
                        game_id=self.game.game_id,
                    )
                    if line:
                        self.log.append(
                            "narration.line",
                            {"text": line, "tone": "neutral", "refs": [self.log_last_seq() - 1]},
                        )
                    # 局势播报：只在转折点开口，避免每步都报变成噪音。
                    situation = self._maybe_situation(result.public_state, state)
                    if situation:
                        self.log.append(
                            "narration.line",
                            {"text": situation, "tone": "turn", "refs": [self.log_last_seq() - 1]},
                        )
            else:
                # 动作始终不合法：按弃权处理，对手直接获胜。
                forfeit = self.game.apply(state, player, Move(kind="forfeit", value=""))
                if forfeit.legal and forfeit.next_state is not None:
                    state = forfeit.next_state
                    self.log.append(
                        "action.applied",
                        {
                            "action": {"kind": "forfeit", "value": ""},
                            "effects": [{"kind": e.kind, **e.detail} for e in forfeit.effects],
                        },
                        player=player,
                    )
                    self.log.append(
                        "state.changed",
                        {"publicState": forfeit.public_state, "deltas": _deltas(forfeit.effects)},
                    )
                    return self._finish(state, state["endReason"], state["winner"], turns)
                return self._finish(state, "选手动作无法执行", state.get("winner"), turns)

            turns += 1
            if turns % 2 == 0:
                round_no += 1

            if state["finished"]:
                return self._finish(state, state["endReason"], state["winner"], turns)

        return self._finish(state, state.get("endReason") or "结束", state.get("winner"), turns)

    def _append_cue(self, cue: Any) -> None:
        self.log.append("director.cue", cue.payload())

    def _maybe_situation(self, public_state: dict[str, Any], state: dict[str, Any]) -> str:
        """判断此刻是否值得播报局势。

        触发条件（满足其一）：
        1. 领先方发生变化
        2. 距离上次播报已过 4 步（给沉默设上限）
        不满足就返回空，让画面安静。
        """
        if self.narrator is None or not hasattr(self.narrator, "situation"):
            return ""

        remaining = public_state.get("remaining") or {}
        if len(remaining) < 2:
            return ""

        values = sorted(remaining.values())
        leader = min(remaining, key=lambda k: remaining[k]) if values[0] != values[1] else None

        changed = leader != self._last_leader
        self._last_leader = leader

        steps_since = self._steps_since_situation + 1
        self._steps_since_situation = steps_since

        if not changed and steps_since < 4:
            return ""

        self._steps_since_situation = 0
        return self.narrator.situation(public_state, game_id=self.game.game_id)

    def log_last_seq(self) -> int:
        return getattr(self.log, "next_seq", 0)

    def _finish(self, state: dict[str, Any], reason: str, winner: PlayerId | None, turns: int) -> RunnerResult:
        self.log.append(
            "match.ended",
            {"winner": winner, "reason": reason, "finalState": self.game.public_state(state)},
        )
        self.log.mark_ended()
        return RunnerResult(winner=winner, endReason=reason, turns=turns, final_state=state)


def _deltas(effects: Any) -> list[dict[str, Any]]:
    return [{"kind": e.kind, **e.detail} for e in effects]
