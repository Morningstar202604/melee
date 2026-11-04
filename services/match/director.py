"""自动导播：把"该看哪里"变成可复现的事件。

导播不做审美判断，只做确定性规则。每条 cue 都能追溯到触发条件，
所以同一场比赛回放时镜头序列完全一致（不要随机、不要读时钟）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine.base import AppliedEffect
from .messages import format_message

SHOT_WIDE = "wide"
SHOT_CLOSE = "close"
SHOT_THOUGHT = "thought"
SHOT_REVEAL = "reveal"
SHOT_SLOWMO = "slowmo"


@dataclass
class Cue:
    shot: str
    target: str | None
    duration_ms: int
    trigger: str
    # 触发原因的模板键与参数：事件流里存英文默认 trigger，读取方按语言重渲染。
    trigger_key: str = ""
    trigger_params: dict[str, Any] | None = None

    def payload(self) -> dict[str, Any]:
        payload = {
            "shot": self.shot,
            "target": self.target,
            "durationMs": self.duration_ms,
            "trigger": self.trigger,
        }
        if self.trigger_key:
            payload["triggerKey"] = self.trigger_key
            payload["triggerParams"] = self.trigger_params or {}
        return payload


def _cue(shot: str, target: str | None, duration_ms: int, key: str,
         params: dict[str, Any] | None = None) -> Cue:
    """构造一条带模板的切镜：事件流存英文 trigger，读取方按语言重渲染。"""
    return Cue(
        shot=shot,
        target=target,
        duration_ms=duration_ms,
        trigger=format_message(key, params or {}),
        trigger_key=key,
        trigger_params=params or {},
    )


class Director:
    """规则式导播。

    规则按优先级排列，第一条命中即返回；都没有命中就维持广角。
    """

    def __init__(
        self,
        *,
        close_threshold: int = 2,
        tension_remaining: int = 3,
        watch_streak: int = 2,
    ) -> None:
        self.close_threshold = close_threshold
        self.tension_remaining = tension_remaining
        self.watch_streak = watch_streak
        self._last_player: str | None = None
        self._streak = 0
        self._stalled: dict[str, int] = {}

    def cue_for(
        self,
        *,
        player: str,
        effects: list[AppliedEffect],
        public_state: dict[str, Any],
        finished: bool,
    ) -> Cue | None:
        for effect in effects:
            cue = self._cue_for_effect(effect, player, public_state, finished)
            if cue is not None:
                return cue
        return None

    def _cue_for_effect(
        self,
        effect: AppliedEffect,
        player: str,
        public_state: dict[str, Any],
        finished: bool,
    ) -> Cue | None:
        handler = {
            "guess": self._cue_guess,
            "action": self._cue_action,
            "hint": self._cue_hint,
            "reveal": self._cue_reveal,
            "answer": self._cue_answer,
        }.get(effect.kind)
        if handler is None:
            return None
        return handler(effect.detail, player, public_state, finished)

    @staticmethod
    def _cue_guess(
        detail: dict[str, Any], player: str, public_state: dict[str, Any], finished: bool
    ) -> Cue | None:
        if finished:
            return _cue(SHOT_SLOWMO, player, 1200, "cue.last_move")
        return _cue(SHOT_REVEAL, player, 1600, "cue.guess_reveal")

    @staticmethod
    def _cue_action(
        detail: dict[str, Any], player: str, public_state: dict[str, Any], finished: bool
    ) -> Cue | None:
        # 棋类的落子：最后一手用慢镜，其余在换手前给一次特写。
        if finished:
            return _cue(SHOT_SLOWMO, player, 1200, "cue.last_move")
        step = int(public_state.get("turnIndex") or 0)
        if step >= 8:
            return _cue(SHOT_CLOSE, player, 1000, "cue.board_step", {"step": step})
        return None

    @staticmethod
    def _cue_hint(
        detail: dict[str, Any], player: str, public_state: dict[str, Any], finished: bool
    ) -> Cue | None:
        return _cue(SHOT_CLOSE, player, 1200, "cue.hint_closeup")

    @staticmethod
    def _cue_reveal(
        detail: dict[str, Any], player: str, public_state: dict[str, Any], finished: bool
    ) -> Cue | None:
        color = detail.get("color")
        if color == "assassin":
            return _cue(SHOT_SLOWMO, player, 1600, "cue.assassin")
        if finished:
            return _cue(SHOT_SLOWMO, player, 1400, "cue.last_flip")
        if color == detail.get("team"):
            return _cue(SHOT_REVEAL, player, 1000, "cue.own_word")
        return _cue(SHOT_CLOSE, player, 1000, "cue.wrong_flip")

    def _cue_answer(
        self,
        detail: dict[str, Any],
        player: str,
        public_state: dict[str, Any],
        finished: bool,
    ) -> Cue | None:
        answered_yes = detail.get("answer") == "yes"
        narrowed = int(detail.get("narrowed", 0))
        remaining = int(public_state.get("remainingPublic", 0) or 0)

        if remaining <= 1:
            return _cue(SHOT_REVEAL, player, 1600, "cue.narrowed", {"remaining": remaining})
        if narrowed == 0:
            self._stalled[player] = self._stalled.get(player, 0) + 1
        else:
            self._stalled[player] = 0
        if self._stalled.get(player, 0) >= 2:
            return _cue(SHOT_CLOSE, player, 1400, "cue.no_progress", {"player": player})
        if remaining <= self.tension_remaining:
            return _cue(SHOT_CLOSE, player, 1200, "cue.few_left", {"remaining": remaining})
        if not answered_yes and narrowed >= 4:
            return _cue(SHOT_CLOSE, player, 1000, "cue.big_sweep", {"narrowed": narrowed})
        return None

    def cue_for_thought(self, player: str, text: str) -> Cue | None:
        """思考流里出现高信息量片段时，插入内心戏镜头。"""
        if not text:
            return None
        if len(text) >= 24 or "?" in text or "？" in text:
            return _cue(SHOT_THOUGHT, player, 1000, "cue.long_thinking")
        return None

    def track(self, player: str) -> None:
        if player == self._last_player:
            self._streak += 1
        else:
            self._last_player = player
            self._streak = 1


def apply_director(events: list[dict[str, Any]], director: Director | None = None) -> list[dict[str, Any]]:
    """离线给一份已有事件流补上 director.cue。

    cue 的位置与实时导播一致：紧跟在 state.changed 之后、narration 之前。
    这样回放时镜头序列才会和直播逐条对齐。
    """
    director = director or Director()
    out: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    for event in events:
        out.append(event)
        kind = event["type"]
        if kind == "thought.delta":
            cue = director.cue_for_thought(event["player"], event["payload"]["text"])
            if cue is not None:
                out.append(_cue_event(event, cue))
        elif kind == "action.applied":
            pending = event
        elif kind == "state.changed" and pending is not None:
            cue = _state_cue(director, pending, event)
            if cue is not None:
                out.append(_cue_event(event, cue))
            pending = None
    return out


def _state_cue(
    director: Director, applied: dict[str, Any], changed: dict[str, Any]
) -> Cue | None:
    public_state = changed["payload"]["publicState"]
    # 终局只看公开状态，不能从动作类型推断：棋类与代号的动作都是 guess，
    # 按类型判断会把每一步都当成最后一手。
    return director.cue_for(
        player=applied["player"],
        effects=[
            AppliedEffect(kind=e["kind"], detail={k: v for k, v in e.items() if k != "kind"})
            for e in applied["payload"]["effects"]
        ],
        public_state=public_state,
        finished=bool(public_state.get("finished")),
    )


def _cue_event(anchor: dict[str, Any], cue: Cue) -> dict[str, Any]:
    return {
        "seq": anchor["seq"],
        "matchId": anchor["matchId"],
        "type": "director.cue",
        "at": anchor["at"],
        "payload": cue.payload(),
    }
