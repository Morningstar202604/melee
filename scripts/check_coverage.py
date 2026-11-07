#!/usr/bin/env python
"""覆盖率门禁：行覆盖率与分支覆盖率都要达线。

pytest-cov 的 --cov-fail-under 只卡综合百分比（行+分支混合），
单独卡"分支覆盖率"它会漏。这里读 coverage.json 精确判定两个指标。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_MIN = 91.0
BRANCH_MIN = 85.0


def main() -> int:
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "--cov=services.match", "--cov-branch",
            "--cov-report=json:coverage.json", "-q",
        ],
        cwd=ROOT,
    )
    report = ROOT / "coverage.json"
    if not report.exists():
        print("未生成 coverage.json", file=sys.stderr)
        return 2
    totals = json.loads(report.read_text(encoding="utf-8"))["totals"]
    lines = round(totals["percent_statements_covered"], 2)
    branches = round(totals["percent_branches_covered"], 2)
    print(f"行覆盖率 {lines}%（门禁 ≥{LINE_MIN}）")
    print(f"分支覆盖率 {branches}%（门禁 ≥{BRANCH_MIN}）")
    failures = []
    if result.returncode != 0:
        failures.append(f"测试失败（exit {result.returncode}）")
    if lines < LINE_MIN:
        failures.append(f"行覆盖率不足：{lines}% < {LINE_MIN}%")
    if branches < BRANCH_MIN:
        failures.append(f"分支覆盖率不足：{branches}% < {BRANCH_MIN}%")
    if failures:
        print("；".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
