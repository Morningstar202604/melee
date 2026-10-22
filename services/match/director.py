"""自动导播：把"该看哪里"变成可复现的事件。

导播不做审美判断，只做确定性规则。每条 cue 都能追溯到触发条件，
所以同一场比赛回放时镜头序列完全一致（不要随机、不要读时钟）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .engine.base import AppliedEffect

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

    def payload(self) -> dict[str, Any]:
        return {
            "shot": self.shot,
            "target": self.target,
            "durationMs": self.duration_ms,
            "trigger": self.trigger,
        }


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
            detail = effect.detail

            if effect.kind == "guess":
                if finished:
                    return Cue(SHOT_SLOWMO, player, 1200, "最后一手，慢镜收尾")
                return Cue(SHOT_REVEAL, player, 1600, "有人报答案，切揭晓")

            if effect.kind == "action":
                # 棋类的落子：最后一手用慢镜，其余在换手前给一次特写。
                if finished:
                    return Cue(SHOT_SLOWMO, player, 1200, "最后一手，慢镜收尾")
                step = int(public_state.get("turnIndex") or 0)
                if step >= 8:
                    return Cue(SHOT_CLOSE, player, 1000, f"第 {step} 步，切特写")
                return None

            if effect.kind == "hint":
                return Cue(SHOT_CLOSE, player, 1200, "队长给线索，切特写")

            if effect.kind == "reveal":
                color = detail.get("color")
                team = detail.get("team")
                if color == "assassin":
                    return Cue(SHOT_SLOWMO, player, 1600, "翻到刺客，慢镜收尾")
                if finished:
                    return Cue(SHOT_SLOWMO, player, 1400, "最后一翻，慢镜收尾")
                if color == team:
                    return Cue(SHOT_REVEAL, player, 1000, "翻中己方词，切揭晓")
                return Cue(SHOT_CLOSE, player, 1000, "翻错换手，切特写")

            if effect.kind == "answer":
                answered_yes = detail.get("answer") == "yes"
                narrowed = int(detail.get("narrowed", 0))
                remaining = int(public_state.get("remainingPublic", 0) or 0)

                if remaining <= 1:
                    return Cue(SHOT_REVEAL, player, 1600, f"候选收敛到 {remaining}，切揭晓")
                if narrowed == 0:
                    self._stalled[player] = self._stalled.get(player, 0) + 1
                else:
                    self._stalled[player] = 0
                if self._stalled.get(player, 0) >= 2:
                    return Cue(SHOT_CLOSE, player, 1400, f"{player} 连续两问没有缩小范围，切特写")
                if remaining <= self.tension_remaining:
                    return Cue(SHOT_CLOSE, player, 1200, f"只剩 {remaining} 个候选，切特写")
                if not answered_yes and narrowed >= 4:
                    return Cue(SHOT_CLOSE, player, 1000, f"一次排除 {narrowed} 个候选，切特写")

        return None

    def cue_for_thought(self, player: str, text: str) -> Cue | None:
        """思考流里出现高信息量片段时，插入内心戏镜头。"""
        if not text:
            return None
        if len(text) >= 24 or "?" in text or "？" in text:
            return Cue(SHOT_THOUGHT, player, 1000, "思考中出现较长的推理或疑问")
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
            public_state = event["payload"]["publicState"]
            # 终局只看公开状态，不能从动作类型推断：棋类与代号的动作都是 guess，
            # 按类型判断会把每一步都当成最后一手。
            finished = bool(public_state.get("finished"))
            effects = [
                AppliedEffect(kind=e["kind"], detail={k: v for k, v in e.items() if k != "kind"})
                for e in pending["payload"]["effects"]
            ]
            cue = director.cue_for(
                player=pending["player"],
                effects=effects,
                public_state=public_state,
                finished=finished,
            )
            if cue is not None:
                out.append(_cue_event(event, cue))
            pending = None
    return out


def _cue_event(anchor: dict[str, Any], cue: Cue) -> dict[str, Any]:
    return {
        "seq": anchor["seq"],
        "matchId": anchor["matchId"],
        "type": "director.cue",
        "at": anchor["at"],
        "payload": cue.payload(),
    }
