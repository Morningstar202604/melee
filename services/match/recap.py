"""战报与高光：从事件流里挑出"值得回看的那几秒"。

两条纪律：
1. 转折点必须由事件定义，不能由模型"觉得精彩"决定。
2. 一句话战报只能陈述事件里真实发生的事，否则整句作废。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .messages import format_message


@dataclass
class Highlight:
    kind: str
    seq: int
    at: str
    title: str
    detail: str
    weight: int
    # 标题/说明的模板键：事件流按语言重新渲染时用，老客户端继续读 title/detail。
    title_key: str = ""
    title_params: dict[str, Any] | None = None
    detail_key: str = ""
    detail_params: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "seq": self.seq,
            "at": self.at,
            "title": self.title,
            "detail": self.detail,
            "weight": self.weight,
        }
        if self.title_key:
            payload["titleKey"] = self.title_key
            payload["titleParams"] = self.title_params or {}
        if self.detail_key:
            payload["detailKey"] = self.detail_key
            payload["detailParams"] = self.detail_params or {}
        return payload


def _answer_highlights(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    """二十问的一次提问：大范围收窄 / 收敛到 1 / 白问。"""
    out: list[Highlight] = []
    narrowed = int(effect.get("narrowed", 0))
    # 参数保持语言无关的 yes/no；由 messages 的模板负责落成「是/否」或「yes/no」。
    answer = "yes" if effect.get("answer") == "yes" else "no"
    if narrowed >= 4:
        tp = {"player": player, "narrowed": narrowed}
        dp = {"value": effect.get("value", ""), "answer": answer}
        out.append(_hl(event, "big_sweep", format_message("hl.big_sweep.title", tp),
                       format_message("hl.big_sweep.detail", dp), narrowed * 10,
                       title_key="hl.big_sweep.title", title_params=tp,
                       detail_key="hl.big_sweep.detail", detail_params=dp))
    if effect.get("remainingPublic") == 1:
        tp = {"player": player}
        out.append(_hl(event, "narrowed_to_one", format_message("hl.narrowed_one.title", tp),
                       format_message("hl.narrowed_one.detail", tp), 80,
                       title_key="hl.narrowed_one.title", title_params=tp,
                       detail_key="hl.narrowed_one.detail", detail_params=tp))
    if narrowed == 0:
        tp = {"player": player}
        dp = {"value": effect.get("value", ""), "answer": answer}
        out.append(_hl(event, "wasted_question", format_message("hl.wasted.title", tp),
                       format_message("hl.wasted.detail", dp), 10,
                       title_key="hl.wasted.title", title_params=tp,
                       detail_key="hl.wasted.detail", detail_params=dp))
    return out


def _reveal_highlights(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    """代号的一次翻牌：翻到刺客最重，翻中己方次之。"""
    if effect.get("color") == "assassin":
        tp = {"player": player, "word": effect.get("word", "")}
        return [_hl(event, "assassin", format_message("hl.assassin.title", tp),
                    format_message("hl.assassin.detail"), 100,
                    title_key="hl.assassin.title", title_params=tp,
                    detail_key="hl.assassin.detail")]
    if effect.get("color") == effect.get("team"):
        tp = {"player": player, "word": effect.get("word", "")}
        return [_hl(event, "own_word", format_message("hl.own_word.title", tp),
                    format_message("hl.own_word.detail"), 40,
                    title_key="hl.own_word.title", title_params=tp,
                    detail_key="hl.own_word.detail")]
    return []


def _hl(
    event: dict[str, Any],
    kind: str,
    title: str,
    detail: str,
    weight: int,
    *,
    title_key: str = "",
    title_params: dict[str, Any] | None = None,
    detail_key: str = "",
    detail_params: dict[str, Any] | None = None,
) -> Highlight:
    return Highlight(
        kind=kind,
        seq=event["seq"],
        at=event["at"],
        title=title,
        detail=detail,
        weight=weight,
        title_key=title_key,
        title_params=title_params,
        detail_key=detail_key,
        detail_params=detail_params,
    )


def _highlights_of(event: dict[str, Any], *, is_last_move: bool = False, move_index: int = 0) -> list[Highlight]:
    """单条事件产生的所有高光；没有就返回空列表。

    `is_last_move` / `move_index` 由 `find_highlights` 按整段流算好传进来：
    单条事件自己看不出「这是不是最后一手」，而决胜手的权重必须依赖这个事实。
    """
    kind, payload = event["type"], event["payload"]
    if kind == "match.ended":
        return [_end_highlight(event, payload)]
    if kind == "action.rejected":
        tp = {"player": event.get("player", "")}
        return [_hl(event, "rejected", format_message("hl.rejected.title", tp), payload["reason"], 30,
                    title_key="hl.rejected.title", title_params=tp)]
    if kind != "action.applied":
        return []

    player = event.get("player", "")
    out: list[Highlight] = []
    for effect in payload["effects"]:
        handler = _EFFECT_HIGHLIGHTS.get(effect["kind"])
        if handler is None:
            continue
        if effect["kind"] == "action":
            out += handler(event, effect, player, is_last=is_last_move, move_index=move_index)
        else:
            out += handler(event, effect, player)
    return out


def _end_highlight(event: dict[str, Any], payload: dict[str, Any]) -> Highlight:
    winner = payload.get("winner")
    if winner:
        tp = {"winner": _TEAM_LABELS.get(winner, winner)}
        key = "hl.end.win"
    else:
        tp = {}
        key = "hl.end.none"
    # 终局原因走消息键，让详情也能切语言；未知原因原样保留。
    from .messages import localize_reason

    reason = localize_reason(payload.get("reason", ""), "en")
    return _hl(event, "end", format_message(key, tp), str(reason), 60,
               title_key=key, title_params=tp)


def _guess_highlight(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    correct = effect.get("correct")
    tp = {"player": player, "value": effect.get("value", "")}
    detail_key = "hl.guess.correct" if correct else "hl.guess.wrong"
    return [
        _hl(
            event,
            "guess",
            format_message("hl.guess.title", tp),
            format_message(detail_key),
            100 if correct else 90,
            title_key="hl.guess.title",
            title_params=tp,
            detail_key=detail_key,
        )
    ]


def _forfeit_highlight(event: dict[str, Any], effect: dict[str, Any], player: str) -> list[Highlight]:
    tp = {"player": player}
    return [_hl(event, "forfeit", format_message("hl.forfeit.title", tp),
                format_message("hl.forfeit.detail"), 70,
                title_key="hl.forfeit.title", title_params=tp,
                detail_key="hl.forfeit.detail")]


# 棋类终局权重：普通落子必须低于它，否则剪辑会挑中一手无关紧要的棋、
# 反而把决胜手挤掉。数值与 _end_highlight 的 60 对齐，决胜手抬到 95。
_BOARD_MOVE_WEIGHT = 20
_BOARD_MOVE_WEIGHT_LATE = 35
_BOARD_MOVE_WEIGHT_LAST = 95


def _board_move_highlight(
    event: dict[str, Any],
    effect: dict[str, Any],
    player: str,
    *,
    is_last: bool = False,
    move_index: int = 0,
) -> list[Highlight]:
    """棋类的一步落子。

    为什么必须单独一条分支：`_EFFECT_HIGHLIGHTS` 过去只认二十问与代号的
    effect 名，而 TextArena 适配器统一发 `kind=action`。结果 10 个棋类赛制
    的高光永远只有 match.ended 一条，剪辑窗口也就永远贴着最后三条事件
    ——README 宣称的「十二种赛制都有高光」在这里是空的。

    权重分三档而不是只看步数：开局几步权重最低（信息量低），
    中后盘抬升（每次落子都在改变胜负），最后一步压过终局事件本身
    （观众想看的正是那一手）。全程仍只依赖事件，不含随机与时钟。
    """
    action = str(effect.get("action", "")).strip()
    if not action:
        return []
    if is_last:
        tp = {"player": player, "action": action}
        return [
            _hl(
                event,
                "board_move",
                format_message("hl.board.decisive.title", tp),
                format_message("hl.board.decisive.detail"),
                _BOARD_MOVE_WEIGHT_LAST,
                title_key="hl.board.decisive.title",
                title_params=tp,
                detail_key="hl.board.decisive.detail",
            )
        ]
    if move_index >= 8:
        tp = {"player": player, "action": action}
        return [
            _hl(
                event,
                "board_move",
                format_message("hl.board.mid.title", tp),
                format_message("hl.board.mid.detail"),
                _BOARD_MOVE_WEIGHT_LATE,
                title_key="hl.board.mid.title",
                title_params=tp,
                detail_key="hl.board.mid.detail",
            )
        ]
    tp = {"player": player, "action": action}
    return [
        _hl(
            event,
            "board_move",
            format_message("hl.board.mid.title", tp),
            format_message("hl.board.early.detail"),
            _BOARD_MOVE_WEIGHT,
            title_key="hl.board.mid.title",
            title_params=tp,
            detail_key="hl.board.early.detail",
        )
    ]


_EFFECT_HIGHLIGHTS = {
    "answer": _answer_highlights,
    "guess": _guess_highlight,
    "forfeit": _forfeit_highlight,
    "reveal": _reveal_highlights,
    "action": _board_move_highlight,
}


def find_highlights(events: list[dict[str, Any]], limit: int = 3) -> list[Highlight]:
    """按权重挑前 N 个转折点。

    棋类的「决胜手」需要知道整段流里哪一步是最后一步，所以先扫一遍把它们
    标出来，再逐条抽高光。二十问与代号不需要这个额外信息，行为保持不变。
    """
    applied = [event for event in events if event["type"] == "action.applied"]
    # 只有落子（kind=guess 且 effect 是 action）才算「棋步」；
    # 二十问的报答案同样记成 guess，不能把它算成决胜手。
    board_moves = {
        event["seq"]: index
        for index, event in enumerate(
            [e for e in applied if _is_board_move(e)]
        )
    }
    last_board_seq = max(board_moves) if board_moves else -1

    highlights: list[Highlight] = []
    for event in events:
        highlights.extend(
            _highlights_of(
                event,
                is_last_move=event["seq"] == last_board_seq,
                move_index=board_moves.get(event["seq"], 0),
            )
        )
    # 权重决定"选哪几段"，展示顺序按 seq 还原时间线——
    # 按权重排会让战报把 seq 250 的瞬间放在 seq 13 前面，读起来像时间倒流。
    highlights.sort(key=lambda h: (-h.weight, h.seq))
    return sorted(highlights[:limit], key=lambda h: h.seq)


def _is_board_move(event: dict[str, Any]) -> bool:
    """是不是棋类的一步落子：action.applied 且 effects 里带 kind=action。"""
    if event["type"] != "action.applied":
        return False
    return any(effect.get("kind") == "action" for effect in event["payload"].get("effects") or [])


from .engine.codenames import team_label as _team_label

_TEAM_LABELS = {"red": "红队", "blue": "蓝队"}


def _winner_label(winner: str | None) -> str:
    """代号/团队赛的胜者是队伍名，战报要翻成人话。

    队伍名统一走 codenames.team_label：多队编队后可能是 team3 这类
    动态名，不能只查红蓝两张小表。"""
    if winner in _TEAM_LABELS:
        return _TEAM_LABELS[winner]
    if isinstance(winner, str) and (winner.startswith("team") or winner.startswith("side")):
        return _team_label(winner)
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
    # 真实观测到的耗时与用量聚合；没有数据时是 0 / None，不编造。
    elapsed_ms_total: int = 0
    elapsed_ms_max: int = 0
    tokens_total: int = 0
    slowest_turn: dict[str, Any] | None = None


def _by_kind(applied: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in applied if e["payload"]["action"]["kind"] == kind]


def _per_player(
    players: list[str],
    asks: list[dict[str, Any]],
    guesses: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    thoughts: list[dict[str, Any]],
    applied: list[dict[str, Any]],
    game: str = "",
) -> dict[str, dict[str, int]]:
    """每个选手的计数。键按赛制区分，不硬套二十问的"提问/报答案"。

    代号与棋类没有"提问"，把每一步都算成 guesses 会让战报出现
    "提问 0 / 报答案 10"这种读起来像 bug 的数字。
    判断依据是赛制 id，不是"有没有 guess 事件"——代号把翻牌也记成
    `kind=guess`，按事件判断会误判。
    """
    def count(events: list[dict[str, Any]], player: str) -> int:
        return sum(1 for e in events if e.get("player") == player)

    out: dict[str, dict[str, int]] = {}
    for player in players:
        moves = count(applied, player)
        out[player] = {
            "moves": moves,
            "rejected": count(rejected, player),
            "thoughts": count(thoughts, player),
        }
        # 只有二十问这两个数才有意义；其它赛制不输出，前端也就不显示。
        if game == "twenty-questions":
            out[player]["asks"] = count(asks, player)
            out[player]["guesses"] = count(guesses, player)
    return out


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


def _timing_of(applied: list[dict[str, Any]]) -> dict[str, Any]:
    """把各手的 elapsedMs / usage 聚合成战报要展示的四个数。

    只统计真的写了 elapsedMs 的事件：脚本选手没有耗时，混进去会把均值拉成 0，
    "平均 0ms"比不显示更误导。
    """
    timed = [
        (event, int(event["payload"]["elapsedMs"]))
        for event in applied
        if isinstance(event["payload"].get("elapsedMs"), int) and event["payload"]["elapsedMs"] > 0
    ]
    tokens = 0
    for event in applied:
        usage = event["payload"].get("usage")
        if not isinstance(usage, dict):
            continue
        value = usage.get("total_tokens") or usage.get("tokensUsedTotal")
        if isinstance(value, int) and value > 0:
            tokens = max(tokens, value)
    if not timed:
        return {"elapsedMsTotal": 0, "elapsedMsMax": 0, "tokensTotal": tokens, "slowestTurn": None}
    slowest, peak = max(timed, key=lambda pair: pair[1])
    return {
        "elapsedMsTotal": sum(value for _event, value in timed),
        "elapsedMsMax": peak,
        "tokensTotal": tokens,
        "slowestTurn": {
            "seq": slowest["seq"],
            "player": slowest.get("player"),
            "elapsedMs": peak,
            "action": slowest["payload"]["action"],
        },
    }


def _collect(events: list[dict[str, Any]]) -> _Counts:
    s = _slice(events)
    timing = _timing_of(s.applied)
    return _Counts(
        game=(s.created or {}).get("payload", {}).get("game", ""),
        winner=s.ended["payload"]["winner"] if s.ended else None,
        reason=s.ended["payload"]["reason"] if s.ended else "",
        # 与 verify_summary 用同一口径：弃权不算一步，pass 算一步。
        turns=len(s.applied) - len(s.forfeits),
        asks=len(s.asks),
        guesses=len(s.guesses),
        rejected=len(s.rejected),
        per_player=_per_player(
            s.players,
            s.asks,
            s.guesses,
            s.rejected,
            s.thoughts,
            s.applied,
            game=(s.created or {}).get("payload", {}).get("game", ""),
        ),
        last_guess_correct=_last_guess_hit(s.guesses),
        last_forfeit_by=s.forfeits[-1].get("player") if s.forfeits else None,
        elapsed_ms_total=timing["elapsedMsTotal"],
        elapsed_ms_max=timing["elapsedMsMax"],
        tokens_total=timing["tokensTotal"],
        slowest_turn=timing["slowestTurn"],
    )


def _one_line(counts: _Counts) -> tuple[str, str, dict[str, Any]]:
    """一句话战报：只陈述事件里能查到的事。

    返回 (英文默认文本, 模板键, 参数)。事件流按语言重新展开模板，
    不把某一种语言的成品句子固化进去。
    """
    if not counts.winner:
        params = {"reason": counts.reason or format_message("recap.reason.finished")}
        key = "recap.no_winner"
        return format_message(key, params), key, params

    label = _winner_label(counts.winner)
    last_guess_by_winner = counts.last_guess_correct and counts.guesses > 0
    if last_guess_by_winner:
        key, params = "recap.win.last_guess", {"winner": label, "turns": counts.turns}
    elif counts.last_forfeit_by is not None and counts.last_forfeit_by != counts.winner:
        key, params = "recap.win.forfeit", {"winner": label}
    elif not counts.reason:
        key, params = "recap.win.plain", {"winner": label}
    # 棋类/代号的引擎自带"谁获胜"，再拼一遍就成了「A 获胜：A 获胜。」
    elif counts.reason in (f"{counts.winner} 获胜", "对局结束", "猜中"):
        key, params = "recap.win.plain", {"winner": label}
    else:
        key, params = "recap.win.reason", {"winner": label, "reason": counts.reason}
    return format_message(key, params), key, params


def build_recap(events: list[dict[str, Any]]) -> dict[str, Any]:
    """一句话战报 + 关键数据。所有数字都从事件里数出来。

    拆成 _collect（数事实）/ _one_line（措辞）两步：
    之前一个函数 44 圈复杂度，改一个措辞要通读一遍统计逻辑。
    """
    counts = _collect(events)
    one_line, one_line_key, one_line_params = _one_line(counts)
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
        "oneLine": one_line,
        "oneLineKey": one_line_key,
        "oneLineParams": one_line_params,
        # 二十问按提问/报答案计数；棋类与代号只有"步"，统一给 moves。
        "turns": counts.turns,
        "moves": counts.turns,
        "asks": counts.asks,
        "guesses": counts.guesses,
        "rejected": counts.rejected,
        "perPlayer": counts.per_player,
        "highlights": [h.as_dict() for h in find_highlights(events)],
        "problems": verify_summary(summary, events),
        # 观战与战报的"这一手为什么慢"：全部来自真实观测，没有就是 0 / null。
        "timing": {
            "elapsedMsTotal": counts.elapsed_ms_total,
            "elapsedMsMax": counts.elapsed_ms_max,
            "tokensTotal": counts.tokens_total,
            "slowestTurn": counts.slowest_turn,
        },
    }


def _event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """从事件里数出可对账的四个数。

    `turns` 排除弃权（弃权不是走了一步棋），但**包含 `[pass]`**：
    跳过是规则允许的真实一步，把它排除会让 turns 与 action.applied 条数
    对不上——战报自检会因此报假警（GOPS 实测踩到）。
    """
    applied = [e for e in events if e["type"] == "action.applied"]
    # 弃权用 action.kind 判定；棋类的跳过是 kind=guess + value="[pass]"。
    forfeited = sum(1 for e in applied if e["payload"]["action"]["kind"] == "forfeit")
    kind_of = [e["payload"]["action"]["kind"] for e in applied]
    return {
        "turns": len(applied) - forfeited,
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
    # 团队赛的胜者是队伍名（red / blue / team3…），不在 perPlayer（选手 id）里，属正常。
    if (
        winner
        and winner not in _TEAM_LABELS
        and not (isinstance(winner, str) and winner.startswith("team"))
        and winner not in summary.get("perPlayer", {})
    ):
        problems.append("胜者不在参赛名单里")
    return problems

