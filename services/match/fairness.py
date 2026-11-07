"""公平性边界：把"可复现"与"不可预测"分成两种建场模式。

问题：种子曾经既生成隐藏信息（20Q 的目标、代号的配色、Kuhn 的手牌、
GOPS 的奖品顺序），又在建场响应、赛事库和 match.created 事件里公开。
于是任何知道种子的人都能力重建全部隐藏信息——20Q 第一手直接猜中，
代号对诚实选手 100:0。这不是选手强，是平台把答案和抽签结果一起公开了。

两类模式，各服务一个真实需求：

- `secure`（默认）：服务端生成种子，客户拿不到。观众、对手、选手都无法
  反推隐藏信息。代价是外部无法用种子复现同一局。
- `replay`（显式）：调用方自己给种子，可复现、可回归、可做数据集。
  此时种子必须公开，因为复现本身就是它的用途；但**建场方不该同时是参赛方**。
  这条由 `assert_seed_disclosure_allowed` 记录成显式语义，而不是靠人自觉。

为什么不是"用密码学随机但照样公开"：公开的随机数不提供任何保密性。
为什么不是"把 seed 哈希后公开"：那仍然是同一个熵源，能重建隐藏信息。
"""

from __future__ import annotations

import secrets
from typing import Any

# 种子可见性档位。
SEED_MODE_SECURE = "secure"
SEED_MODE_REPLAY = "replay"
SEED_MODES = (SEED_MODE_SECURE, SEED_MODE_REPLAY)

# 默认用 63 位随机数：Python 的 random.Random 接受任意大整数，
# 63 位足以让暴力枚举不可行，又不会在 JSON 里变成超长字符串。
_SEED_BITS = 63


def new_seed() -> int:
    """不可预测的种子。用 secrets 而不是 random：后者可被状态推断。"""
    return secrets.randbits(_SEED_BITS)


def resolve_seed(requested: int | None, mode: str = SEED_MODE_SECURE) -> tuple[int, str]:
    """返回（实际种子, 生效模式）。

    调用方显式给 seed 就进 replay 模式——他要的是可复现，我们不能假装
    他给的值不存在。没给就生成不可预测的种子。
    """
    if requested is not None:
        return int(requested), SEED_MODE_REPLAY
    if mode not in SEED_MODES:
        raise ValueError(f"unknown seed mode: {mode}")
    return new_seed(), SEED_MODE_SECURE


def public_payload(seed: int, mode: str) -> dict[str, Any]:
    """建场响应里如何描述种子。

    secure 模式不返回种子本身，只声明"这场不可用种子复现"。
    对用户是有用信息：想知道为什么复现不了，看这里。
    """
    if mode == SEED_MODE_REPLAY:
        return {"seed": seed, "seedMode": mode, "reproducible": True}
    return {"seed": None, "seedMode": mode, "reproducible": False}


def assert_seed_disclosure_allowed(mode: str) -> bool:
    """seed 是否可以出现在 API 响应/事件里。

    只有 replay 模式允许：那时公开种子是产品功能（复现），不是泄漏。
    """
    return mode == SEED_MODE_REPLAY
