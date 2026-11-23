"""给两个独立打包脚本读取唯一版本字面量，避免各自写一遍正则。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_PATTERN = re.compile(r'APP_VERSION = "([^"]+)"')


def read_version() -> str:
    source = (ROOT / "services" / "match" / "version.py").read_text(encoding="utf-8")
    match = _PATTERN.search(source)
    if match is None:
        raise RuntimeError("services/match/version.py 缺少 APP_VERSION")
    return match.group(1)
