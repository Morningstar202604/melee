"""提示词协议层：把"赛制规则"从各模型的自由发挥里收回来。

这一层只做三件事：

1. 给每个注册赛制一份 **场景协议**（目标、信息边界、动作约束），
   由 LLM 选手在系统提示词末尾统一追加；
2. 给教练指令一份 **注入模板**，明确"优先执行但不能越权/违规"；
3. 为未来新增赛制提供注册入口，未知赛制回退到安全通用协议。

刻意不在这里写长人格提示词：人格由选手自己的 `systemPrompt` 决定，
平台只负责不公平/违规的边界。把两者混在一起，会重演"自定义人格把
输出格式顶掉"的旧问题。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptProfile:
    """一个赛制的提示词协议。

    `family` 用于审计与文档；`rule` 是真正注入系统提示词的那一句。
    """

    game_id: str
    family: str
    objective: str
    rule: str


PROFILES: dict[str, PromptProfile] = {
    "twenty-questions": PromptProfile(
        "twenty-questions",
        "deduction",
        "用最少的问题锁定秘密物件",
        "只使用公开提问历史与候选表；每次提问必须能排除至少一个候选；"
        "报答案即终局，不确定时继续问。",
    ),
    "codenames": PromptProfile(
        "codenames",
        "social",
        "让队友理解线索并避开刺客词",
        "队长只能给出一个词与数字；不得直接说出队友颜色、位置或完整词表。"
        "特工只能根据公开线索与本队可见信息行动。",
    ),
    "tic-tac-toe": PromptProfile(
        "tic-tac-toe",
        "connection",
        "先连成三子",
        "只下空格；优先占据中心与双威胁点，不要编造棋盘上不存在的位置。",
    ),
    "reverse-tic-tac-toe": PromptProfile(
        "reverse-tic-tac-toe",
        "connection",
        "逼对手先连成三子",
        "只下空格；目标是制造对手的必连，不要按普通井字棋的胜负直觉行动。",
    ),
    "wild-tic-tac-toe": PromptProfile(
        "wild-tic-tac-toe",
        "connection",
        "在特殊格规则下连成三子",
        "只下合法空格；先确认特殊格的额外效果，再决定落子，不要假设普通规则。",
    ),
    "connect-four": PromptProfile(
        "connect-four",
        "connection",
        "四子连线",
        "只选择未满的列；落子会占据该列最底部的空位，不要指定具体行。",
    ),
    "othello": PromptProfile(
        "othello",
        "territory",
        "终局占据更多棋子",
        "只走能翻转至少一枚对方棋子的合法点；无子可下时按规则跳过，"
        "不要伪造可下位置。",
    ),
    "nim": PromptProfile(
        "nim",
        "arithmetic",
        "取走最后一枚石子",
        "只能从单一堆取走允许数量的石子；用异或和判断必败态，不要越界取子。",
    ),
    "kuhn-poker": PromptProfile(
        "kuhn-poker",
        "bluffing",
        "在信息不完全的猜牌中获利",
        "只使用自己的手牌与公开下注历史；不要声称知道对手手牌，"
        "下注/弃牌必须是合法动作。",
    ),
    "game-of-pure-strategy": PromptProfile(
        "game-of-pure-strategy",
        "strategy",
        "用资源与手牌组合赢下对抗",
        "只使用自己可见的手牌与公开资源；不要引用隐藏牌面，"
        "按当前合法动作选择。",
    ),
    "simple-tak": PromptProfile(
        "simple-tak",
        "territory",
        "在 Tak 棋盘上形成道路",
        "只使用规则允许的放置/移动；不得把棋子放到非法格，"
        "行动前确认源格与目标格。",
    ),
    "crusade": PromptProfile(
        "crusade",
        "territory",
        "在十字军棋盘中完成占领",
        "只使用当前合法走法；不要假设未出现的单位或地形，"
        "优先执行能直接改变胜负条件的动作。",
    ),
}

_FALLBACK = PromptProfile(
    "unknown",
    "generic",
    "按当前赛制规则取胜",
    "只使用观察中公开的信息与合法动作；不得读取隐藏状态、伪造动作或假设未出现的规则。",
)


def profile_for(game_id: str) -> PromptProfile:
    """取赛制协议；未知赛制回退到通用安全协议，绝不抛错。"""
    return PROFILES.get(str(game_id or ""), _FALLBACK)


def protocol_for(game_id: str) -> str:
    """注入 LLM 系统提示词的赛制协议段（不含人格）。"""
    profile = profile_for(game_id)
    return (
        "\n\n[平台对抗协议]\n"
        f"赛制：{game_id or profile.game_id}（{profile.family}）\n"
        f"目标：{profile.objective}\n"
        f"约束：{profile.rule}\n"
        "输出仍必须是平台规定的 JSON 动作；协议不改变输出格式。"
    )


def coach_block(instruction: str) -> str:
    """把教练指令包成不可越权的用户消息片段。

    教练能改变策略，但不能改变规则：这段文本明确"优先执行"与
    "不得要求读取隐藏信息/伪造动作"同时成立。
    """
    text = str(instruction or "").strip()
    if not text:
        return ""
    return (
        "\n\n[教练席实时指令]\n"
        f"{text}\n"
        "执行要求：在不违反赛制规则、不读取隐藏信息、不改变 JSON 输出格式的前提下，"
        "优先按这条指令调整下一步动作。"
    )
