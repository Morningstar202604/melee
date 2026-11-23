"""语言列表、回退规则与 Accept-Language 解析的唯一实现。

`request_lang`（HTTP 层）、`messages`（文案层）、二十问词表（内容层）
此前各有一套“en 开头算英文，否则中文”的判断，回退默认值还不一样。
"""

from __future__ import annotations

LANGS = ("en", "zh")
LANG_FALLBACK = "en"


def normalize_lang(value: object, *, default: str = LANG_FALLBACK) -> str:
    """把 `en-US` / `zh-Hans` / `EN` 收敛成 `en` / `zh`；未知值回退 default。"""
    text = str(value or "").strip().lower()
    if text.startswith("en"):
        return "en"
    if text.startswith("zh"):
        return "zh"
    return default


def parse_accept_language(header: str) -> str:
    """按出现顺序取第一个支持的语言；浏览器已按 q 值排好序。"""
    for chunk in header.split(","):
        tag = chunk.split(";")[0].strip()
        code = normalize_lang(tag, default="")
        if code in LANGS:
            return code
    return LANG_FALLBACK
