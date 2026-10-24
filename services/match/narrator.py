"""解说器：只把已发生的事实翻成一句话，不参与判定。

两档实现：
- RuleNarrator：确定性、零依赖，永远可用。
- LLMNarrator：调模型，但每句都要过事实校验，不过就退回规则式。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .engine.base import AppliedEffect

_NUMBER = re.compile(r"\d+")
_CN_DIGITS = {
    "一": "1",
    "二": "2",
    "两": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
}
_FORBIDDEN = ("获胜", "赢了", "输了", "胜出", "败北", "比赛结束")


class RuleNarrator:
    """确定性解说，零模型依赖。

    每句话都能追溯到具体效果，所以不会出现"解说说了一件没发生的事"。
    """

    def line(
        self,
        public_state: dict[str, Any],
        player: str,
        move: Any,
        effects: list[AppliedEffect],
        *,
        game_id: str = "twenty-questions",
    ) -> str:
        for effect in effects:
            text = _line_for_effect(effect, player, public_state)
            if text:
                return text
        return ""

    def situation(
        self,
        public_state: dict[str, Any],
        *,
        game_id: str = "twenty-questions",
    ) -> str:
        """局势播报：不是"刚发生了什么"，而是"现在什么局面"。

        只在真正的转折点开口（比分变化、领先易手、连续无进展），
        不做每步都报——那是噪音。
        """
        if game_id == "codenames":
            remaining = public_state.get("remaining") or {}
            red = remaining.get("red")
            blue = remaining.get("blue")
            if red is None or blue is None:
                return ""
            # 剩得少的领先（先翻完就赢）
            if red < blue:
                return f"红队还剩 {red} 个，蓝队 {blue} 个，红队领先。"
            if blue < red:
                return f"蓝队还剩 {blue} 个，红队 {red} 个，蓝队领先。"
            return f"双方都还剩 {red} 个。"
        return ""


_TEAM_LABEL = {"red": "红队", "blue": "蓝队", "neutral": "中立", "assassin": "刺客"}


def _line_for_effect(effect: AppliedEffect, player: str, public_state: dict[str, Any]) -> str:
    """把一条效果翻成解说词；没有对应模板就返回空串。"""
    detail = effect.detail
    if effect.kind == "guess":
        if detail.get("correct"):
            return f"{player} 直接报出 {detail['value']}，猜中。"
        return f"{player} 报了 {detail['value']}，猜错，直接判负。"
    if effect.kind == "answer":
        return _answer_line(detail, player, public_state)
    if effect.kind == "forfeit":
        return f"{player} 无法给出合法动作，弃权。"
    if effect.kind == "action":
        # ---- 棋类（TextArena 适配器发的 action 效果）----
        action = str(detail.get("action", ""))
        step = public_state.get("turnIndex")
        step_text = f"第 {step} 步" if step else "这一步"
        return f"{player} 在{step_text}落子 {action}。"
    if effect.kind == "hint":
        return f"{player} 给出线索「{detail.get('word')}」，指向 {detail.get('count')} 个词。"
    if effect.kind == "reveal":
        return _reveal_line(detail)
    return ""


def _answer_line(detail: dict[str, Any], player: str, public_state: dict[str, Any]) -> str:
    narrowed = detail.get("narrowed", 0)
    answer = "是" if detail.get("answer") == "yes" else "否"
    if narrowed > 0:
        return (
            f"{player} 问「{detail.get('value')}」，答{answer}，"
            f"排除 {narrowed} 个候选，只剩 {public_state.get('remainingPublic')} 个。"
        )
    return f"{player} 问「{detail.get('value')}」，答{answer}，这一问没有缩小范围。"


def _reveal_line(detail: dict[str, Any]) -> str:
    word = detail.get("word")
    color = detail.get("color")
    team = detail.get("team")
    team_label = _TEAM_LABEL.get(team, team)
    if color == "assassin":
        return f"{team_label}翻到刺客「{word}」，直接出局。"
    if color == team:
        return f"{team_label}翻中「{word}」，继续。"
    return f"{team_label}踩到{_TEAM_LABEL.get(color, color)}的「{word}」，换手。"


def narrator_from_env(client: Any | None = None) -> Any:
    """按环境变量决定解说器：默认规则式，配齐了才用模型。

    MELEE_NARRATOR=llm 时必须同时给 model / base_url / api_key，缺一个就退回
    规则式并告警——宁可解说朴素，也不能让配置错误把比赛跑挂。
    """

    from .settings import settings

    cfg = settings()
    if cfg.narrator.lower() != "llm":
        return RuleNarrator()

    model = cfg.narrator_model.strip()
    base_url = cfg.narrator_base_url.strip()
    api_key = cfg.narrator_api_key.strip()
    if not (model and base_url and api_key):
        return RuleNarrator()

    if client is None:
        import httpx

        client = httpx.Client(timeout=cfg.narrator_timeout)
    return LLMNarrator(client, model=model, base_url=base_url, api_key=api_key)


class LLMNarrator:
    """模型解说，但只有通过事实校验的句子才会被采用。

    三条硬规则：
    1. 提示词里只有已发生的事实（公开状态 + 这一手的结算效果）。
    2. 输出必须是一句话，不能包含判定结论（胜负由引擎说，不由解说员说）。
    3. 句中出现的每个数字，都必须能在事实里找到；任何一项对不上就整句丢弃。
    """

    def __init__(
        self,
        client: Any,
        *,
        model: str,
        base_url: str,
        api_key: str,
        fallback: RuleNarrator | None = None,
        timeout_s: float = 20.0,
        max_chars: int = 60,
    ) -> None:
        self._client = client
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._fallback = fallback or RuleNarrator()
        self._timeout = timeout_s
        self._max_chars = max_chars

    def __repr__(self) -> str:
        # 默认 repr 不泄漏字段，但显式写死更稳：Key 永远不该出现在任何 repr 里。
        return f"LLMNarrator(model={self._model!r}, base_url={self._base_url!r})"

    def line(
        self,
        public_state: dict[str, Any],
        player: str,
        move: Any,
        effects: list[AppliedEffect],
        *,
        game_id: str = "twenty-questions",
    ) -> str:
        rule_line = self._fallback.line(public_state, player, move, effects, game_id=game_id)
        if not rule_line:
            return ""
        # 提示词与事实结构目前只覆盖二十问；别的赛制直接走规则式，
        # 硬套会让模型拿到 None 字段后编造。
        if game_id != "twenty-questions":
            return rule_line

        facts = _facts(public_state, player, move, effects)
        try:
            text = self._ask(facts)
        except Exception:  # noqa: BLE001 - 解说失败不能影响比赛
            return rule_line

        if not text or not _is_faithful(text, facts, self._max_chars):
            return rule_line
        return text

    def _ask(self, facts: dict[str, Any]) -> str:
        system = (
            "你是一场双人「二十问」比赛的解说员。"
            "只能用下面给出的事实写一句中文解说，不超过 40 个字。"
            "禁止：宣布胜负、预测走势、使用事实里没有的数字或名词、使用形容词夸奖。"
            "只输出这一句话，不要引号、不要解释。"
        )
        user = json.dumps(facts, ensure_ascii=False)
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.4,
                "max_tokens": 120,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        content = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return content.strip().strip('"').strip()


def _facts(public_state: dict[str, Any], player: str, move: Any, effects: list[AppliedEffect]) -> dict[str, Any]:
    return {
        "player": player,
        "action": {"kind": getattr(move, "kind", ""), "value": getattr(move, "value", "")},
        "effects": [{"kind": e.kind, **e.detail} for e in effects],
        "remaining": public_state.get("remainingPublic"),
        "turn": public_state.get("turnIndex"),
        "maxQuestions": public_state.get("maxQuestions"),
        "asked": len(public_state.get("history") or []),
    }


_ANSWERS = {"是": "yes", "对": "yes", "否": "no", "不是": "no"}


def _is_faithful(text: str, facts: dict[str, Any], max_chars: int) -> bool:
    """事实校验，三层：

    1. 形状：非空、不超长、不含胜负判定词。
    2. 数字：出现的中文或阿拉伯数字必须能在事实里找到。
    3. 语义：回答方向不能反、角色不能错位、动作类型不能张冠李戴。

    第 3 层是这次新加的——前两层拦不住"答否"写成"答是"这类错误，
    而这类错误比数字错更致命：观众会被直接误导。
    """
    if not text or len(text) > max_chars:
        return False
    if any(word in text for word in _FORBIDDEN):
        return False

    if not _numbers_ok(text, facts):
        return False
    return _semantics_ok(text, facts)


def _numbers_ok(text: str, facts: dict[str, Any]) -> bool:
    allowed_numbers = {int(n) for n in _NUMBER.findall(json.dumps(facts, ensure_ascii=False))}
    mentioned = {int(n) for n in _NUMBER.findall(text)}
    if not mentioned.issubset(allowed_numbers):
        return False

    allowed_cn = {str(value) for value in allowed_numbers}
    for char in re.findall(r"[零一二两三四五六七八九十]", text):
        if _CN_DIGITS.get(char) not in allowed_cn:
            return False
    return True


_YES_WORDS = ("是", "对", "命中", "正确")
_NO_WORDS = ("否", "不是", "没有", "未")
_ASK_WORDS = ("问", "提问")
_GUESS_WORDS = ("报出", "报答案", "猜")


def _answer_direction_ok(text: str, answer: str | None) -> bool:
    """句中同时提到"是/否"时，必须与真实答案一致。"""
    if answer not in ("yes", "no"):
        return True
    says_yes, says_no = _polarity_of(text)
    if says_yes and says_no:
        return False
    if answer == "yes" and says_no:
        return False
    if answer == "no" and says_yes:
        return False
    return True


def _polarity_of(text: str) -> tuple[bool, bool]:
    says_yes = any(word in text for word in _YES_WORDS)
    says_no = any(word in text for word in _NO_WORDS)
    # "是不是"这类问句不算判断，先排除
    return says_yes and "是不是" not in text, says_no


def _role_ok(text: str, player: str | None) -> bool:
    """句中出现的选手名必须是这一手的执行者。"""
    for other in ("A", "B"):
        if other == player:
            continue
        if _mentions_player(text, other):
            return False
    return True


def _action_kind_ok(text: str, kind: str | None) -> bool:
    """报答案的一手不能写成"问"，提问的一手不能写成"报答案"。"""
    asks = any(word in text for word in _ASK_WORDS)
    guesses = any(word in text for word in _GUESS_WORDS)
    if kind == "guess" and asks and not guesses:
        return False
    if kind == "ask" and guesses and not asks:
        return False
    return True


def _semantics_ok(text: str, facts: dict[str, Any]) -> bool:
    action = facts.get("action") or {}
    return (
        _answer_direction_ok(text, _answer_of(facts))
        and _role_ok(text, facts.get("player"))
        and _action_kind_ok(text, action.get("kind"))
    )


def _mentions_player(text: str, player: str) -> bool:
    """只把独立出现的 A / B 当作选手指代，避免误伤英文单词。"""
    return re.search(rf"(?<![A-Za-z]){re.escape(player)}(?![A-Za-z])", text) is not None


def _answer_of(facts: dict[str, Any]) -> str | None:
    for effect in facts.get("effects") or []:
        if effect.get("kind") == "answer":
            return effect.get("answer")
    return None
