"""服务端文案目录：解说、战报、高光标题的英文与中文两种措辞。

为什么放在服务端而不是前端：
- 解说与高光是**写进事件流的文本**，第三方客户端（HTTPAgent、CLI、导播台）直接读
  事件流，不经过我们的前端；
- 但事件流又是 append-only 的事实记录，不该把某一种语言的成品句子固化进去。

所以约定：事件里存 `msg`（模板键）+ `params`（参数），`text` 仍然写一份英文的
默认展开值，供不认识 `msg` 的老客户端使用。读取方按自己的语言重新展开 `msg`，
认不出就退回 `text`。这样既向后兼容，又让事实与措辞分离。
"""

from __future__ import annotations

import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{(\w+)\}")

# 每条消息：键 -> {语言: 模板}。模板里的 {name} 由 params 填充。
MESSAGES: dict[str, dict[str, str]] = {
    # ---- 二十问解说 ----
    "narr.guess.correct": {
        "en": "{player} calls out {value} — correct.",
        "zh": "{player} 直接报出 {value}，猜中。",
    },
    "narr.guess.wrong": {
        "en": "{player} guesses {value} — wrong, immediate loss.",
        "zh": "{player} 报了 {value}，猜错，直接判负。",
    },
    "narr.answer.narrowed": {
        "en": '{player} asks "{value}" — {answer}; {narrowed} candidates eliminated, {remaining} left.',
        "zh": "{player} 问「{value}」，答{answer}，排除 {narrowed} 个候选，只剩 {remaining} 个。",
    },
    "narr.answer.flat": {
        "en": '{player} asks "{value}" — {answer}; nothing eliminated this turn.',
        "zh": "{player} 问「{value}」，答{answer}，这一问没有缩小范围。",
    },
    "narr.forfeit": {
        "en": "{player} could not produce a legal move — forfeits.",
        "zh": "{player} 无法给出合法动作，弃权。",
    },
    "narr.action.step": {
        "en": "{player} plays {action} on move {step}.",
        "zh": "{player} 在第 {step} 步落子 {action}。",
    },
    "narr.action.now": {
        "en": "{player} plays {action}.",
        "zh": "{player} 这一步落子 {action}。",
    },
    "narr.hint": {
        "en": '{player} gives the clue "{word}" for {count} words.',
        "zh": "{player} 给出线索「{word}」，指向 {count} 个词。",
    },
    "narr.reveal.assassin": {
        "en": '{team} reveals the assassin "{word}" — eliminated.',
        "zh": "{team}翻到刺客「{word}」，直接出局。",
    },
    "narr.reveal.own": {
        "en": '{team} reveals its own "{word}" — keeps the turn.',
        "zh": "{team}翻中「{word}」，继续。",
    },
    "narr.reveal.other": {
        "en": '{team} hits {other}\'s "{word}" — turn passes.',
        "zh": "{team}踩到{other}的「{word}」，换手。",
    },
    "narr.situation.leading": {
        "en": "{team} leads: {own} left to {other}'s {rival}.",
        "zh": "{team}还剩 {own} 个，{other} {rival} 个，{team}领先。",
    },
    "narr.situation.tied": {
        "en": "Both sides have {count} left.",
        "zh": "双方都还剩 {count} 个。",
    },
    # ---- 战报一句话 ----
    "recap.no_winner": {
        "en": "No winner: {reason}.",
        "zh": "无人获胜：{reason}。",
    },
    "recap.win.last_guess": {
        "en": "{winner} guesses correctly on move {turns}.",
        "zh": "{winner} 在第 {turns} 手报出答案并命中。",
    },
    "recap.win.forfeit": {
        "en": "{winner} wins — the opponent forfeited.",
        "zh": "{winner} 获胜：对手弃权。",
    },
    "recap.win.plain": {
        "en": "{winner} wins.",
        "zh": "{winner} 获胜。",
    },
    "recap.win.reason": {
        "en": "{winner} wins: {reason}.",
        "zh": "{winner} 获胜：{reason}。",
    },
    "recap.reason.finished": {
        "en": "the match ended",
        "zh": "比赛结束",
    },
    # ---- 高光 ----
    "hl.big_sweep.title": {
        "en": "{player} eliminates {narrowed} at once",
        "zh": "{player} 一次排除 {narrowed} 个",
    },
    "hl.big_sweep.detail": {
        "en": 'Asks "{value}" — {answer}; the candidate pool shrinks sharply.',
        "zh": "问「{value}」，答{answer}，候选大幅减少。",
    },
    "hl.narrowed_one.title": {
        "en": "Only one candidate left",
        "zh": "只剩一个可能",
    },
    "hl.narrowed_one.detail": {
        "en": "{player} narrows it to a single candidate — one guess from the win.",
        "zh": "{player} 把候选压到 1 个，下一步就能报答案。",
    },
    "hl.wasted.title": {
        "en": "{player} wastes a question",
        "zh": "{player} 白问了一手",
    },
    "hl.wasted.detail": {
        "en": 'Asks "{value}" — {answer}; the candidate count does not change.',
        "zh": "问「{value}」，答{answer}，候选数量没有变化。",
    },
    "hl.assassin.title": {
        "en": '{player} reveals the assassin "{word}"',
        "zh": "{player} 翻到刺客「{word}」",
    },
    "hl.assassin.detail": {
        "en": "Revealing the assassin loses the match immediately.",
        "zh": "翻到刺客立即判负，比赛结束。",
    },
    "hl.own_word.title": {
        "en": '{player} reveals own word "{word}"',
        "zh": "{player} 翻中己方词「{word}」",
    },
    "hl.own_word.detail": {
        "en": "A correct reveal keeps the turn.",
        "zh": "猜对可以继续。",
    },
    "hl.rejected.title": {
        "en": "{player} made an illegal move",
        "zh": "{player} 动作被判犯规",
    },
    "hl.guess.title": {
        "en": '{player} guesses "{value}"',
        "zh": "{player} 报出「{value}」",
    },
    "hl.guess.correct": {
        "en": "Correct — the match ends.",
        "zh": "猜中，比赛结束。",
    },
    "hl.guess.wrong": {
        "en": "Wrong — immediate loss.",
        "zh": "猜错，直接判负。",
    },
    "hl.forfeit.title": {
        "en": "{player} forfeits",
        "zh": "{player} 弃权",
    },
    "hl.forfeit.detail": {
        "en": "Could not produce a legal move twice in a row.",
        "zh": "连续无法给出合法动作。",
    },
    "hl.end.win": {
        "en": "{winner} wins",
        "zh": "{winner} 获胜",
    },
    "hl.end.none": {
        "en": "No winner",
        "zh": "无人获胜",
    },
    "hl.board.decisive.title": {
        "en": "{player} plays the decisive {action}",
        "zh": "{player} 落下决胜一手 {action}",
    },
    "hl.board.decisive.detail": {
        "en": "The move that ends the match.",
        "zh": "决定胜负的一手。",
    },
    "hl.board.mid.title": {
        "en": "{player} plays {action}",
        "zh": "{player} 落子 {action}",
    },
    "hl.board.mid.detail": {
        "en": "A key move in the mid or late game.",
        "zh": "中后盘的关键一手。",
    },
    "hl.board.early.detail": {
        "en": "Opening play.",
        "zh": "开局布局。",
    },
    # ---- 队伍名（语言无关引用键的翻译表） ----
    "team.red": {"en": "Red", "zh": "红队"},
    "team.blue": {"en": "Blue", "zh": "蓝队"},
    "team.neutral": {"en": "Neutral", "zh": "中立"},
    "team.assassin": {"en": "Assassin", "zh": "刺客"},

    # ---- 分享落地页 ----
    "share.title.win": {
        "en": "{game} · {winner} wins",
        "zh": "{game} · {winner} 胜",
    },
    "share.title.live": {
        "en": "{game} · live now",
        "zh": "{game} · 直播中",
    },
    "share.description.fallback": {
        "en": "melee broadcast studio — AI matches you can watch, replay and tell stories about.",
        "zh": "melee 转播台：把 AI 对抗做成节目。",
    },
    "share.cta": {
        "en": "Open the broadcast",
        "zh": "进入观战页",
    },
    # ---- 引擎结束原因 ----
    # 引擎用中文常量表达终局原因（它是事件流里的事实字段）。
    # 这里给出每种已知原因的英文说法；未知原因原样透传，不编造。
    "reason.guessed": {"en": "guessed correctly", "zh": "猜中"},
    "reason.guess_wrong": {"en": "guessed wrong — immediate loss", "zh": "猜错，直接判负"},
    "reason.questions_exhausted": {"en": "ran out of questions", "zh": "问满上限，无人猜中"},
    "reason.forfeit": {"en": "forfeit", "zh": "弃权"},
    # ---- 导播切镜触发原因 ----
    "cue.last_move": {"en": "last move — slow motion", "zh": "最后一手，慢镜收尾"},
    "cue.guess_reveal": {"en": "a guess is in — cut to reveal", "zh": "有人报答案，切揭晓"},
    "cue.board_step": {"en": "move {step} — close-up", "zh": "第 {step} 步，切特写"},
    "cue.hint_closeup": {"en": "spymaster gives a clue — close-up", "zh": "队长给线索，切特写"},
    "cue.assassin": {"en": "assassin revealed — slow motion", "zh": "翻到刺客，慢镜收尾"},
    "cue.last_flip": {"en": "last flip — slow motion", "zh": "最后一翻，慢镜收尾"},
    "cue.own_word": {"en": "own word revealed — cut to reveal", "zh": "翻中己方词，切揭晓"},
    "cue.wrong_flip": {"en": "wrong flip, turn passes — close-up", "zh": "翻错换手，切特写"},
    "cue.narrowed": {"en": "narrowed to {remaining} — cut to reveal", "zh": "候选收敛到 {remaining}，切揭晓"},
    "cue.no_progress": {"en": "{player} asked twice without narrowing — close-up", "zh": "{player} 连续两问没有缩小范围，切特写"},
    "cue.few_left": {"en": "only {remaining} candidates left — close-up", "zh": "只剩 {remaining} 个候选，切特写"},
    "cue.big_sweep": {"en": "{narrowed} candidates eliminated — close-up", "zh": "一次排除 {narrowed} 个候选，切特写"},
    "cue.long_thinking": {"en": "long reasoning or question detected", "zh": "思考中出现较长的推理或疑问"},
    # ---- 动作类型（战报"最慢一手"里用） ----
    "action.kind.ask": {"en": "asked", "zh": "提问"},
    "action.kind.guess": {"en": "guessed", "zh": "报答案"},
    "action.kind.forfeit": {"en": "forfeited", "zh": "弃权"},
    "action.kind.hint": {"en": "gave a clue", "zh": "给线索"},
    "action.kind.reveal": {"en": "revealed", "zh": "翻牌"},
    "action.kind.pass": {"en": "passed", "zh": "过牌"},
    # ---- 内置选手的内心戏 ----
    "thought.quiz": {
        "en": "{remaining} candidates left — pick the question that splits them most evenly.",
        "zh": "还剩 {remaining} 个候选，挑一个切得最均匀的问题。",
    },
    "thought.generic": {
        "en": "{count} legal moves; picking number {index}.",
        "zh": "可用动作 {count} 个，选第 {index} 个。",
    },
    "thought.captain": {
        "en": "{count} words left on our side; hinting at {base}.",
        "zh": "队里还有 {count} 个词要猜，先提示「{base}」。",
    },
    "thought.guess": {
        "en": 'Clue is "{clue}" — picking the closest of {count} unrevealed words.',
        "zh": "线索是「{clue}」，从 {count} 个未翻开的词里挑最像的。",
    },
    "thought.search": {
        "en": "Searching {depth} plies over {count} legal moves, choosing {choice}.",
        "zh": "在 {count} 个合法动作里搜索 {depth} 层，选择 {choice}。",
    },
    # ---- 赛制名（分享页与卡片用；前端有自己的字典） ----
    "game.twenty-questions": {"en": "Twenty Questions", "zh": "二十问"},
    "game.codenames": {"en": "Codenames", "zh": "代号"},
    "game.tic-tac-toe": {"en": "Tic-tac-toe", "zh": "井字棋"},
    "game.connect-four": {"en": "Connect Four", "zh": "四子棋"},
    "game.othello": {"en": "Othello", "zh": "黑白棋"},
    "game.nim": {"en": "Nim", "zh": "取石子"},
    "game.reverse-tic-tac-toe": {"en": "Reverse Tic-tac-toe", "zh": "反井字棋"},
    "game.kuhn-poker": {"en": "Kuhn Poker", "zh": "库恩扑克"},
    "game.game-of-pure-strategy": {"en": "Game of Pure Strategy", "zh": "纯策略博弈"},
    "game.simple-tak": {"en": "Simple Tak", "zh": "Simple Tak"},
    "game.wild-tic-tac-toe": {"en": "Wild Tic-tac-toe", "zh": "狂野井字棋"},
    "game.crusade": {"en": "Crusade", "zh": "十字军"},
}

LANG_FALLBACK = "en"
LANGS = ("en", "zh")


def format_message(key: str, params: dict[str, Any] | None = None, *, lang: str = LANG_FALLBACK) -> str:
    """按语言展开一条消息。未知键直接返回键名，让漏翻显形。"""
    table = MESSAGES.get(key)
    if table is None:
        return key
    template = table.get(lang) or table[LANG_FALLBACK]
    if not params:
        return template
    # 集中在这里翻译参数：三个调用方（解说/战报/高光）都不必各自记得做。
    localized = _localize_params(params, lang)
    return _PLACEHOLDER.sub(
        lambda match: str(localized[match.group(1)]) if match.group(1) in localized else match.group(0),
        template,
    )


def message(key: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回可直接写进事件的载荷：`msg` 键 + `params` + 英文默认文本。

    事件流按 `msg` 重新渲染，`text` 是给不认识 `msg` 的客户端的兜底；
    默认英文与产品主语言一致。
    """
    # 事件里存"原始"参数（可能是 team.red 这样的引用键），
    # 只有 text 是按默认语言渲染出来的兜底值。翻译发生在读取时，
    # 这样一条事件可以按任意语言重读。
    raw_params = params or {}
    return {
        "text": format_message(key, raw_params, lang=LANG_FALLBACK),
        "msg": key,
        "params": raw_params,
    }


# 布尔型参数（目前只有二十问的 yes/no 回答）落成各语言的说法。
_BOOL_WORDS = {
    "yes": {"en": "yes", "zh": "是"},
    "no": {"en": "no", "zh": "否"},
}

# 语言无关的引用型参数：值是消息键，渲染时才翻译。
# 例如队伍名在事件里存 `team.red`，英文渲染成 "Red"，中文渲染成「红队」。
_REFERENCE_PREFIXES = ("team.",)


def _translate_ref(value: str, lang: str) -> str | None:
    """如果参数是 `team.xxx` 这类引用键，返回它的译文；否则 None。"""
    if not any(value.startswith(prefix) for prefix in _REFERENCE_PREFIXES):
        return None
    return format_message(value, lang=lang) if value in MESSAGES else None


def _localize_params(params: dict[str, Any], lang: str = LANG_FALLBACK) -> dict[str, Any]:
    """把语言无关的参数落成目标语言的说法。

    三类参数：
    - yes/no 布尔词；
    - `team.red` 这类引用键（值本身是消息键）；
    - 其余原样（玩家名、棋盘词、数字）——它们是内容，不是文案。
    """
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            words = _BOOL_WORDS.get(value)
            if words:
                out[key] = words.get(lang, value)
                continue
            referenced = _translate_ref(value, lang)
            if referenced is not None:
                out[key] = referenced
                continue
        out[key] = value
    return out


# 引擎终局原因的中文常量 -> 消息键。未知原因原样保留。
_END_REASON_KEYS = {
    "猜中": "reason.guessed",
    "猜错，直接判负": "reason.guess_wrong",
    "问满上限，无人猜中": "reason.questions_exhausted",
    "弃权": "reason.forfeit",
}


def localize_reason(reason: Any, lang: str) -> Any:
    """把引擎的终局原因翻成目标语言；不认识的原因原样返回。"""
    if not isinstance(reason, str) or lang not in LANGS:
        return reason
    key = _END_REASON_KEYS.get(reason)
    return format_message(key, lang=lang) if key else reason


def localize_payload(payload: dict[str, Any], lang: str) -> dict[str, Any]:
    """把事件载荷按语言重新渲染；没有模板键的老事件原样返回。

    支持两种载荷形态：
    - narration.line：`msg` + `params` -> `text`
    - director.cue：`triggerKey` + `triggerParams` -> `trigger`
    """
    localized = dict(payload)

    key = payload.get("msg")
    if isinstance(key, str) and key in MESSAGES:
        raw_params = payload.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        localized["text"] = format_message(key, _localize_params(params, lang), lang=lang)

    trigger_key = payload.get("triggerKey")
    if isinstance(trigger_key, str) and trigger_key in MESSAGES:
        raw_params = payload.get("triggerParams")
        params = raw_params if isinstance(raw_params, dict) else {}
        localized["trigger"] = format_message(trigger_key, _localize_params(params, lang), lang=lang)

    # match.ended 的 reason 是引擎常量，同样按语言改写。
    if "reason" in payload:
        localized["reason"] = localize_reason(payload["reason"], lang)

    return localized


def localize_highlights(items: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
    """把战报高光按语言重新渲染 title/detail；没有模板键的条目原样返回。"""
    if lang not in LANGS:
        return items
    out: list[dict[str, Any]] = []
    for item in items:
        if item.get("titleKey"):
            item = {
                **item,
                "title": format_message(
                    item["titleKey"], _localize_params(item.get("titleParams") or {}, lang), lang=lang
                ),
            }
        if item.get("detailKey"):
            item = {
                **item,
                "detail": format_message(
                    item["detailKey"], _localize_params(item.get("detailParams") or {}, lang), lang=lang
                ),
            }
        out.append(item)
    return out


def game_label(game_id: str, lang: str) -> str:
    """按语言取赛制名；字典未登记的新赛制回退注册表中文名。

    返回值约定与 format_message 不同：赛制名有注册表兜底，
    不会把 `game.xxx` 这样的键名漏给用户。
    """
    key = f"game.{game_id}"
    if key in MESSAGES:
        return format_message(key, lang=lang)
    from .engine import registry as game_registry

    return next(
        (item["label"] for item in game_registry.catalog() if item["gameId"] == game_id),
        game_id,
    )


def localize_action_kind(kind: Any, lang: str) -> Any:
    """把动作类型枚举翻成可读动词；未登记的类型原样返回。"""
    if not isinstance(kind, str) or not kind:
        return kind
    key = f"action.kind.{kind}"
    if key not in MESSAGES:
        return kind
    return format_message(key, lang=lang)


def localize_recap(recap: dict[str, Any], lang: str) -> dict[str, Any]:
    """按语言重渲染一句话战报与高光标题；无模板键时保持原样。"""
    if lang not in LANGS:
        return recap
    result = dict(recap)
    key = result.get("oneLineKey")
    if isinstance(key, str) and key in MESSAGES:
        result["oneLine"] = format_message(key, result.get("oneLineParams") or {}, lang=lang)
    if "reason" in result:
        result["reason"] = localize_reason(result["reason"], lang)
    # 逐手耗时里的动作类型是枚举，界面上要读成动词。
    timing = result.get("timing")
    if isinstance(timing, dict):
        slow = timing.get("slowestTurn")
        if isinstance(slow, dict) and isinstance(slow.get("action"), dict):
            slow = {**slow, "action": {**slow["action"]}}
            slow["action"]["kind"] = localize_action_kind(slow["action"].get("kind"), lang)
            result["timing"] = {**timing, "slowestTurn": slow}
    highlights = result.get("highlights")
    if isinstance(highlights, list):
        result["highlights"] = localize_highlights(highlights, lang)
    return result
