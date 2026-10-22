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


def find_highlights(events: list[dict[str, Any]], limit: int = 3) -> list[Highlight]:
    highlights: list[Highlight] = []

    for event in events:
        kind = event["type"]
        payload = event["payload"]

        if kind == "action.applied":
            effects = payload["effects"]
            player = event.get("player", "")

            for effect in effects:
                if effect["kind"] == "answer":
                    narrowed = int(effect.get("narrowed", 0))
                    answer = "是" if effect.get("answer") == "yes" else "否"
                    if narrowed >= 4:
                        highlights.append(
                            Highlight(
                                kind="big_sweep",
                                seq=event["seq"],
                                at=event["at"],
                                title=f"{player} 一次排除 {narrowed} 个",
                                detail=f"问「{effect.get('value')}」，答{answer}，候选从 {narrowed} 个以上直接砍到更少。",
                                weight=narrowed * 10,
                            )
                        )
                    remaining = effect.get("remainingPublic")
                    if remaining == 1:
                        highlights.append(
                            Highlight(
                                kind="narrowed_to_one",
                                seq=event["seq"],
                                at=event["at"],
                                title="只剩一个可能",
                                detail=f"{player} 把候选压到 1 个，下一步就能报答案。",
                                weight=80,
                            )
                        )
                    if narrowed == 0:
                        highlights.append(
                            Highlight(
                                kind="wasted_question",
                                seq=event["seq"],
                                at=event["at"],
                                title=f"{player} 白问了一手",
                                detail=f"问「{effect.get('value')}」，答{answer}，候选数量没有变化。",
                                weight=10,
                            )
                        )
                elif effect["kind"] == "guess":
                    correct = effect.get("correct")
                    highlights.append(
                        Highlight(
                            kind="guess",
                            seq=event["seq"],
                            at=event["at"],
                            title=f"{player} 报出「{effect.get('value')}」",
                            detail="猜中，比赛结束。" if correct else "猜错，直接判负。",
                            weight=100 if correct else 90,
                        )
                    )
                elif effect["kind"] == "forfeit":
                    highlights.append(
                        Highlight(
                            kind="forfeit",
                            seq=event["seq"],
                            at=event["at"],
                            title=f"{player} 弃权",
                            detail="连续无法给出合法动作。",
                            weight=70,
                        )
                    )
                elif effect["kind"] == "reveal":
                    color = effect.get("color")
                    team = effect.get("team")
                    if color == "assassin":
                        highlights.append(
                            Highlight(
                                kind="assassin",
                                seq=event["seq"],
                                at=event["at"],
                                title=f"{player} 翻到刺客「{effect.get('word')}」",
                                detail="翻到刺客立即判负，比赛结束。",
                                weight=100,
                            )
                        )
                    elif color == team:
                        highlights.append(
                            Highlight(
                                kind="own_word",
                                seq=event["seq"],
                                at=event["at"],
                                title=f"{player} 翻中己方词「{effect.get('word')}」",
                                detail="猜对可以继续。",
                                weight=40,
                            )
                        )

        elif kind == "match.ended":
            winner = payload.get("winner")
            highlights.append(
                Highlight(
                    kind="end",
                    seq=event["seq"],
                    at=event["at"],
                    title=f"{_TEAM_LABELS.get(winner, winner)} 获胜" if winner else "无人获胜",
                    detail=str(payload.get("reason", "")),
                    weight=60,
                )
            )

        elif kind == "action.rejected":
            highlights.append(
                Highlight(
                    kind="rejected",
                    seq=event["seq"],
                    at=event["at"],
                    title=f"{event.get('player')} 动作被判犯规",
                    detail=payload["reason"],
                    weight=30,
                )
            )

    highlights.sort(key=lambda h: (-h.weight, h.seq))
    return highlights[:limit]


_TEAM_LABELS = {"red": "红队", "blue": "蓝队"}


def _winner_label(events: list[dict[str, Any]], winner: str | None) -> str:
    """代号/团队赛的胜者是 red/blue，战报要翻成人话。"""
    if winner in _TEAM_LABELS:
        return _TEAM_LABELS[winner]
    return str(winner)


def build_recap(events: list[dict[str, Any]]) -> dict[str, Any]:
    """一句话战报 + 关键数据。所有数字都从事件里数出来。"""
    end = next((e for e in events if e["type"] == "match.ended"), None)
    created = next((e for e in events if e["type"] == "match.created"), None)
    game = (created or {}).get("payload", {}).get("game", "")
    applied = [e for e in events if e["type"] == "action.applied"]
    rejected = [e for e in events if e["type"] == "action.rejected"]
    thoughts = [e for e in events if e["type"] == "thought.delta"]

    asks = [e for e in applied if e["payload"]["action"]["kind"] == "ask"]
    guesses = [e for e in applied if e["payload"]["action"]["kind"] == "guess"]
    forfeits = [e for e in applied if e["payload"]["action"]["kind"] == "forfeit"]

    winner = end["payload"]["winner"] if end else None
    reason = end["payload"]["reason"] if end else ""

    players = sorted({e["player"] for e in applied if e.get("player")})
    per_player = {
        player: {
            "asks": sum(1 for e in asks if e.get("player") == player),
            "guesses": sum(1 for e in guesses if e.get("player") == player),
            "rejected": sum(1 for e in rejected if e.get("player") == player),
            "thoughts": sum(1 for e in thoughts if e.get("player") == player),
        }
        for player in players
    }

    turns = [t for t in applied if t["payload"]["action"]["kind"] != "forfeit"]
    label = _winner_label(events, winner)
    # 中文不加空格；拉丁字母的选手名加空格，读起来才不粘。
    pad = " " if label.isascii() else ""
    if winner:
        if guesses and guesses[-1].get("player") == winner and guesses[-1]["payload"]["effects"][0].get("correct"):
            one_line = f"{label}{pad}在第 {len(turns)} 手报出答案并命中。"
        elif forfeits and forfeits[-1].get("player") != winner:
            one_line = f"{label}{pad}获胜：对手弃权。"
        elif reason:
            # 棋类/代号的引擎自带"谁获胜"，再拼一遍就成了「A 获胜：A 获胜。」
            if reason in (f"{winner} 获胜", "对局结束", "猜中"):
                one_line = f"{label}{pad}获胜。"
            else:
                one_line = f"{label}{pad}获胜：{reason}。"
        else:
            one_line = f"{label}{pad}获胜。"
    else:
        one_line = f"无人获胜：{reason or '比赛结束'}。"

    return {
        "game": game,
        "winner": winner,
        "reason": reason,
        "oneLine": one_line,
        # 二十问按提问/报答案计数；棋类与代号只有"步"，统一给 moves。
        "turns": len(turns),
        "moves": len(turns),
        "asks": len(asks),
        "guesses": len(guesses),
        "rejected": len(rejected),
        "perPlayer": per_player,
        "highlights": [h.as_dict() for h in find_highlights(events)],
        "problems": verify_summary(
            {
                "winner": winner,
                "turns": len(turns),
                "asks": len(asks),
                "guesses": len(guesses),
                "rejected": len(rejected),
                "perPlayer": per_player,
            },
            events,
        ),
    }


def verify_summary(summary: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    """战报事实校验：返回所有对不上的地方，空列表表示通过。

    规则很简单——战报里出现的每个数字，都必须能在事件里数出来。
    """
    problems: list[str] = []
    counts = {
        "turns": sum(
            1
            for e in events
            if e["type"] == "action.applied" and e["payload"]["action"]["kind"] != "forfeit"
        ),
        "asks": sum(1 for e in events if e["type"] == "action.applied" and e["payload"]["action"]["kind"] == "ask"),
        "guesses": sum(
            1 for e in events if e["type"] == "action.applied" and e["payload"]["action"]["kind"] == "guess"
        ),
        "rejected": sum(1 for e in events if e["type"] == "action.rejected"),
    }
    for key, expected in counts.items():
        if summary.get(key) != expected:
            problems.append(f"{key} 对不上：战报 {summary.get(key)}，事件里 {expected}")

    end = next((e for e in events if e["type"] == "match.ended"), None)
    if end is not None and summary.get("winner") != end["payload"]["winner"]:
        problems.append("胜者与 match.ended 不一致")

    winner = summary.get("winner")
    if winner:
        # 团队赛的胜者是 red / blue，不在 perPlayer（选手 id）里，属正常。
        if winner not in _TEAM_LABELS and winner not in summary.get("perPlayer", {}):
            problems.append("胜者不在参赛名单里")
    return problems
