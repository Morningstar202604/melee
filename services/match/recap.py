"""战报与高光：从事件流里挑出"值得回看的那几秒"。

两条纪律：
1. 转折点必须由事件定义，不能由模型"觉得精彩"决定。
2. 一句话战报只能陈述事件里真实发生的事，否则整句作废。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Highlight:
    kind: str
    seq: int
    at: str
    title: str
    detail: str
    weight: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "seq": self.seq,
            "at": self.at,
            "title": self.title,
            "detail": self.detail,
            "weight": self.weight,
        }


def _answer_highlights(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    """二十问的一次提问：大范围收窄 / 收敛到 1 / 白问。"""
    out: list[Highlight] = []
    narrowed = int(effect.get("narrowed", 0))
    answer = "是" if effect.get("answer") == "yes" else "否"
    if narrowed >= 4:
        out.append(_hl(event, "big_sweep", f"{player} 一次排除 {narrowed} 个",
                       f"问「{effect.get('value')}」，答{answer}，候选大幅减少。", narrowed * 10))
    if effect.get("remainingPublic") == 1:
        out.append(_hl(event, "narrowed_to_one", "只剩一个可能",
                       f"{player} 把候选压到 1 个，下一步就能报答案。", 80))
    if narrowed == 0:
        out.append(_hl(event, "wasted_question", f"{player} 白问了一手",
                       f"问「{effect.get('value')}」，答{answer}，候选数量没有变化。", 10))
    return out


def _reveal_highlights(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    """代号的一次翻牌：翻到刺客最重，翻中己方次之。"""
    if effect.get("color") == "assassin":
        return [_hl(event, "assassin", f"{player} 翻到刺客「{effect.get('word')}」",
                    "翻到刺客立即判负，比赛结束。", 100)]
    if effect.get("color") == effect.get("team"):
        return [_hl(event, "own_word", f"{player} 翻中己方词「{effect.get('word')}」",
                    "猜对可以继续。", 40)]
    return []


def _hl(event: dict[str, Any], kind: str, title: str, detail: str, weight: int) -> Highlight:
    return Highlight(kind=kind, seq=event["seq"], at=event["at"], title=title, detail=detail, weight=weight)


def _highlights_of(event: dict[str, Any]) -> list[Highlight]:
    """单条事件产生的所有高光；没有就返回空列表。"""
    kind, payload = event["type"], event["payload"]
    if kind == "match.ended":
        return [_end_highlight(event, payload)]
    if kind == "action.rejected":
        return [_hl(event, "rejected", f"{event.get('player')} 动作被判犯规", payload["reason"], 30)]
    if kind != "action.applied":
        return []

    player = event.get("player", "")
    out: list[Highlight] = []
    for effect in payload["effects"]:
        handler = _EFFECT_HIGHLIGHTS.get(effect["kind"])
        if handler is not None:
            out += handler(event, effect, player)
    return out


def _end_highlight(event: dict[str, Any], payload: dict[str, Any]) -> Highlight:
    winner = payload.get("winner")
    title = f"{_TEAM_LABELS.get(winner, winner)} 获胜" if winner else "无人获胜"
    return _hl(event, "end", title, str(payload.get("reason", "")), 60)


def _guess_highlight(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    correct = effect.get("correct")
    return [
        _hl(
            event,
            "guess",
            f"{player} 报出「{effect.get('value')}」",
            "猜中，比赛结束。" if correct else "猜错，直接判负。",
            100 if correct else 90,
        )
    ]


def _forfeit_highlight(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    return [_hl(event, "forfeit", f"{player} 弃权", "连续无法给出合法动作。", 70)]


_EFFECT_HIGHLIGHTS = {
    "answer": _answer_highlights,
    "guess": _guess_highlight,
    "forfeit": _forfeit_highlight,
    "reveal": _reveal_highlights,
}


def find_highlights(events: list[dict[str, Any]], limit: int = 3) -> list[Highlight]:
    """按权重挑前 N 个转折点。规则分散在 _highlights_of 的几个分支里。"""
    highlights: list[Highlight] = []
    for event in events:
        highlights.extend(_highlights_of(event))
    highlights.sort(key=lambda h: (-h.weight, h.seq))
    return highlights[:limit]


_TEAM_LABELS = {"red": "红队", "blue": "蓝队"}


def _winner_label(winner: str | None) -> str:
    """代号/团队赛的胜者是 red/blue，战报要翻成人话。"""
    if winner in _TEAM_LABELS:
        return _TEAM_LABELS[winner]
    return str(winner)


@dataclass
class _Counts:
    """一场比赛的可数事实。所有字段都由事件算出，不含推断。"""

    game: str
    winner: str | None
    reason: str
    turns: int
    asks: int
    guesses: int
    rejected: int
    per_player: dict[str, dict[str, int]]
    last_guess_correct: bool
    last_forfeit_by: str | None


def _by_kind(applied: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in applied if e["payload"]["action"]["kind"] == kind]


def _per_player(
    players: list[str],
    asks: list[dict[str, Any]],
    guesses: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    thoughts: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    def count(events: list[dict[str, Any]], player: str) -> int:
        return sum(1 for e in events if e.get("player") == player)

    return {
        player: {
            "asks": count(asks, player),
            "guesses": count(guesses, player),
            "rejected": count(rejected, player),
            "thoughts": count(thoughts, player),
        }
        for player in players
    }


def _last_guess_hit(guesses: list[dict[str, Any]]) -> bool:
    if not guesses:
        return False
    effects = guesses[-1]["payload"]["effects"]
    return bool(effects) and bool(effects[0].get("correct"))


@dataclass
class _Streams:
    """按事件类型切好的几摞，后面数数只跟这些打交道。"""

    created: dict[str, Any] | None
    ended: dict[str, Any] | None
    applied: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    thoughts: list[dict[str, Any]]
    asks: list[dict[str, Any]]
    guesses: list[dict[str, Any]]
    forfeits: list[dict[str, Any]]

    @property
    def players(self) -> list[str]:
        """参赛名单以 match.created 为准。

        只从 action.applied 推导会在"全程没走出合法动作、直接弃权"的比赛里
        丢掉胜者，导致战报自检误报"胜者不在参赛名单里"（真实模型联调时复现）。
        """
        from_created = (self.created or {}).get("payload", {}).get("players") or []
        applied = {e["player"] for e in self.applied if e.get("player")}
        return sorted(set(from_created) | applied)


def _slice(events: list[dict[str, Any]]) -> _Streams:
    by_type = _group_by_type(events)
    applied = by_type.get("action.applied", [])
    return _Streams(
        created=_first(by_type.get("match.created")),
        ended=_first(by_type.get("match.ended")),
        applied=applied,
        rejected=by_type.get("action.rejected", []),
        thoughts=by_type.get("thought.delta", []),
        asks=_by_kind(applied, "ask"),
        guesses=_by_kind(applied, "guess"),
        forfeits=_by_kind(applied, "forfeit"),
    )


def _group_by_type(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(event["type"], []).append(event)
    return grouped


def _first(items: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return items[0] if items else None


def _collect(events: list[dict[str, Any]]) -> _Counts:
    s = _slice(events)
    return _Counts(
        game=(s.created or {}).get("payload", {}).get("game", ""),
        winner=s.ended["payload"]["winner"] if s.ended else None,
        reason=s.ended["payload"]["reason"] if s.ended else "",
        turns=len(s.applied) - len(s.forfeits),
        asks=len(s.asks),
        guesses=len(s.guesses),
        rejected=len(s.rejected),
        per_player=_per_player(s.players, s.asks, s.guesses, s.rejected, s.thoughts),
        last_guess_correct=_last_guess_hit(s.guesses),
        last_forfeit_by=s.forfeits[-1].get("player") if s.forfeits else None,
    )


def _one_line(counts: _Counts) -> str:
    """一句话战报：只陈述事件里能查到的事。"""
    label = _winner_label(counts.winner)
    # 中文不加空格；拉丁字母的选手名加空格，读起来才不粘。
    pad = " " if label.isascii() else ""
    if not counts.winner:
        return f"无人获胜：{counts.reason or '比赛结束'}。"

    last_guess_by_winner = counts.last_guess_correct and counts.guesses > 0
    if last_guess_by_winner:
        return f"{label}{pad}在第 {counts.turns} 手报出答案并命中。"
    if counts.last_forfeit_by is not None and counts.last_forfeit_by != counts.winner:
        return f"{label}{pad}获胜：对手弃权。"
    if not counts.reason:
        return f"{label}{pad}获胜。"
    # 棋类/代号的引擎自带"谁获胜"，再拼一遍就成了「A 获胜：A 获胜。」
    if counts.reason in (f"{counts.winner} 获胜", "对局结束", "猜中"):
        return f"{label}{pad}获胜。"
    return f"{label}{pad}获胜：{counts.reason}。"


def build_recap(events: list[dict[str, Any]]) -> dict[str, Any]:
    """一句话战报 + 关键数据。所有数字都从事件里数出来。

    拆成 _collect（数事实）/ _one_line（措辞）两步：
    之前一个函数 44 圈复杂度，改一个措辞要通读一遍统计逻辑。
    """
    counts = _collect(events)
    summary = {
        "winner": counts.winner,
        "turns": counts.turns,
        "asks": counts.asks,
        "guesses": counts.guesses,
        "rejected": counts.rejected,
        "perPlayer": counts.per_player,
    }
    return {
        "game": counts.game,
        "winner": counts.winner,
        "reason": counts.reason,
        "oneLine": _one_line(counts),
        # 二十问按提问/报答案计数；棋类与代号只有"步"，统一给 moves。
        "turns": counts.turns,
        "moves": counts.turns,
        "asks": counts.asks,
        "guesses": counts.guesses,
        "rejected": counts.rejected,
        "perPlayer": counts.per_player,
        "highlights": [h.as_dict() for h in find_highlights(events)],
        "problems": verify_summary(summary, events),
    }


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    applied = [e for e in events if e["type"] == "action.applied"]
    kind_of = [e["payload"]["action"]["kind"] for e in applied]
    return {
        "turns": sum(1 for kind in kind_of if kind != "forfeit"),
        "asks": kind_of.count("ask"),
        "guesses": kind_of.count("guess"),
        "rejected": sum(1 for e in events if e["type"] == "action.rejected"),
    }


def verify_summary(summary: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    """战报事实校验：返回所有对不上的地方，空列表表示通过。

    规则很简单——战报里出现的每个数字，都必须能在事件里数出来。
    """
    problems = [
        f"{key} 对不上：战报 {summary.get(key)}，事件里 {expected}"
        for key, expected in _event_counts(events).items()
        if summary.get(key) != expected
    ]

    end = next((e for e in events if e["type"] == "match.ended"), None)
    if end is not None and summary.get("winner") != end["payload"]["winner"]:
        problems.append("胜者与 match.ended 不一致")

    winner = summary.get("winner")
    # 团队赛的胜者是 red / blue，不在 perPlayer（选手 id）里，属正常。
    if winner and winner not in _TEAM_LABELS and winner not in summary.get("perPlayer", {}):
        problems.append("胜者不在参赛名单里")
    return problems

