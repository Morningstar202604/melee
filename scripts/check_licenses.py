#!/usr/bin/env python
"""许可证门禁：运行时依赖禁止 GPL / AGPL，并检查 NOTICE 台账覆盖。

用法：
    python scripts/check_licenses.py          # 校验
    python scripts/check_licenses.py --write  # 用当前环境重写 NOTICE 的依赖表

为什么不用第三方工具：只需读已安装包的元数据，几十行就够，不必引入依赖。
"""

from __future__ import annotations

import argparse
import re
import sys
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICE = ROOT / "NOTICE"
REQUIREMENTS = ROOT / "requirements.txt"

# 运行时必须安装的包（requirements 里的"非可选"部分由注释标记区分）
FORBIDDEN = ("GPL", "AGPL", "LGPL")

# 可选依赖：缺了服务照常启动，但对应用功能降级
OPTIONAL = {"textarena", "redis"}

# 只在开发/构建期用，不算运行时
DEV_ONLY = {"pytest", "datamodel-code-generator"}


def parse_requirements() -> list[str]:
    names: list[str] = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(re.split(r"[=\s\[]", line, maxsplit=1)[0])
    return names


def normalize(license_text: str) -> str:
    """把 `MIT License` / `BSD License` 这类写法归一到 SPDX 简写。

    importlib 的元数据里同一个许可证有多种写法（README 写 MIT、分类器写
    `MIT License`），直接字符串比会误报。这里只做保守归一，不做模糊匹配。
    """
    text = (license_text or "").strip()
    upper = text.upper()
    if upper in {"MIT", "MIT LICENSE", "MIT-CMU"}:
        return "MIT" if upper != "MIT-CMU" else "MIT-CMU"
    if upper in {"BSD", "BSD LICENSE", "BSD-3-CLAUSE", "BSD 3-CLAUSE"}:
        return "BSD-3-Clause"
    if upper in {"APACHE 2", "APACHE-2.0", "APACHE SOFTWARE LICENSE"}:
        return "Apache-2.0"
    return text


def license_of(pkg: str) -> str:
    try:
        meta = metadata(pkg)
    except PackageNotFoundError:
        return "MISSING"
    expression = meta.get("License-Expression") or ""
    if expression:
        return normalize(expression)
    declared = (meta.get("License") or "").strip()
    if declared and declared.upper() != "UNKNOWN":
        return normalize(declared)
    classifiers = [
        item.split("::")[-1].strip()
        for item in (meta.get_all("Classifier") or [])
        if item.startswith("License ::")
    ]
    if classifiers:
        return normalize(classifiers[0])
    return "UNKNOWN"


def notice_versions() -> dict[str, tuple[str, str]]:
    """从 NOTICE 读出 `包 | 版本 | 许可证` 三列。"""
    rows: dict[str, tuple[str, str]] = {}
    for line in NOTICE.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"包", "---"}:
            continue
        rows[cells[0].lower()] = (cells[1], cells[2])
    return rows


def check() -> list[str]:
    problems: list[str] = []
    rows = notice_versions()
    for pkg in parse_requirements():
        key = pkg.lower().replace("_", "-")
        lic = license_of(pkg)
        if lic == "MISSING":
            problems.append(f"{pkg}: 未安装，无法核验许可证（可选依赖可跳过安装后再验）")
            continue
        if any(bad in lic.upper() for bad in FORBIDDEN):
            problems.append(f"{pkg}: 许可证 {lic} 属于禁止类别")
        if key not in rows:
            problems.append(f"{pkg}: NOTICE 台账缺这一行")
            continue
        recorded_version, recorded_license = rows[key]
        if recorded_version != version(pkg):
            problems.append(f"{pkg}: NOTICE 版本 {recorded_version} ≠ 实际 {version(pkg)}")
        if recorded_license != lic:
            problems.append(f"{pkg}: NOTICE 许可证 {recorded_license} ≠ 实际 {lic}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="重写 NOTICE 的依赖表")
    args = parser.parse_args()

    if args.write:
        lines = [
            "# NOTICE — 第三方依赖台账",
            "",
            "本项目运行时依赖与其许可证。新增依赖必须在同一提交里追加一行。",
            "",
            "| 包 | 版本 | 许可证 | 用途 |",
            "|---|---|---|---|",
        ]
        old = notice_versions()
        for pkg in parse_requirements():
            key = pkg.lower().replace("_", "-")
            purpose = old.get(key, ("", "", ""))[2] if key in old else ""
            try:
                lines.append(f"| {pkg} | {version(pkg)} | {license_of(pkg)} | {purpose or '见 requirements.txt'} |")
            except PackageNotFoundError:
                lines.append(f"| {pkg} | - | - | 未安装 |")
        NOTICE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"已重写 {NOTICE}")
        return 0

    problems = check()
    if problems:
        print("许可证门禁未通过：")
        for item in problems:
            print(" -", item)
        return 1
    print("许可证门禁通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
