"""队伍显示名与语言无关引用键的唯一实现。

战报、解说、分享卡、代号引擎各自维护过 `{red: 红队, blue: 蓝队}`，
动态队伍 `team3` 在其中几张表里直接漏成原始字符串。这里把三件事分开：
- `team_reference`：给事件/文案参数用的语言无关键（`team.red`、`team.3`）；
- `team_label_zh`：中文显示名（`红队`、`第 3 队`）；
- `team_reference_from_label`：把中文显示名反查回引用键。
英文显示名仍由 `messages` 的 `team.*` 词条渲染，避免内容层反向依赖文案层。
"""

from __future__ import annotations

import re

TEAM_KEYS = ("red", "blue", "neutral", "assassin")
TEAM_LABELS_ZH = {"red": "红队", "blue": "蓝队", "neutral": "中立", "assassin": "刺客"}

_INDEXED = re.compile(r"team(\d+)")
_LABEL_INDEXED = re.compile(r"第 (\d+) 队")


def is_known_team(value: object) -> bool:
    """是不是引擎认识的队伍标识（含 team3 这类动态队伍）。"""
    if not isinstance(value, str):
        return False
    return value in TEAM_KEYS or bool(_INDEXED.fullmatch(value))


def team_reference(team: object) -> str:
    """转成语言无关的引用键；选手名原样返回。"""
    if not isinstance(team, str):
        return str(team)
    if team in TEAM_KEYS:
        return f"team.{team}"
    match = _INDEXED.fullmatch(team)
    if match:
        return f"team.{match.group(1)}"
    return team


def team_label_zh(team: object) -> str:
    """中文显示名；动态队伍按编号显示，未知标识原样返回。"""
    if not isinstance(team, str):
        return str(team)
    if team in TEAM_LABELS_ZH:
        return TEAM_LABELS_ZH[team]
    match = _INDEXED.fullmatch(team)
    if match:
        return f"第 {int(match.group(1))} 队"
    return team


def team_reference_from_label(label: str) -> str:
    """把中文显示名反查成 `team.red` / `team.3`；认不出就原样返回。"""
    for key, text in TEAM_LABELS_ZH.items():
        if label == text:
            return f"team.{key}"
    match = _LABEL_INDEXED.fullmatch(label)
    if match:
        return f"team.{match.group(1)}"
    return label
