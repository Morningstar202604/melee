#!/usr/bin/env python
"""依赖 CVE 扫描：直接查 OSV（https://api.osv.dev），不依赖 pip 索引。

为什么自己写：pip-audit 需要访问 PyPI 的 JSON API 与完整依赖解析，
在不稳定网络下会连续超时；OSV 的批量查询接口只按"包名+版本"查，轻得多。

用法：
    python scripts/audit_cves.py                 # 读 requirements.txt
    python scripts/audit_cves.py --fail-on high  # CI 用：high 及以上返回 1
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OSV_BATCH = "https://api.osv.dev/v1/querybatch"
SEVERITY_ORDER = {"low": 1, "moderate": 2, "medium": 2, "high": 3, "critical": 4}


def parse_requirements(path: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-r"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)==([0-9][^\s;]*)", line)
        if match:
            out.append((match.group(1), match.group(2)))
    return out


def query(packages: list[tuple[str, str]], timeout: float = 30.0) -> dict[str, list[dict]]:
    payload = {"queries": [{"package": {"name": name, "ecosystem": "PyPI"}, "version": version} for name, version in packages]}
    request = urllib.request.Request(
        OSV_BATCH,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    results: dict[str, list[dict]] = {}
    for (name, version), item in zip(packages, body.get("results", [])):
        results[f"{name}=={version}"] = item.get("vulns", [])
    return results


def severity_of(vuln: dict) -> str:
    """OSV 的 severity 字段结构不统一，取数据库里给出的最高等级。"""
    for entry in vuln.get("severity") or []:
        score = entry.get("score", "")
        # CVSS 向量里没有直接等级，按 CVSS 分数段判断：/AV:N/AC:L/... 不可直接解析，
        # 这里退回 database_specific，再退回 moderate。
        if score.startswith("CVSS:"):
            continue
    specific = vuln.get("database_specific") or {}
    level = str(specific.get("severity", "")).lower()
    if level in SEVERITY_ORDER:
        return level
    for entry in vuln.get("severity") or []:
        if entry.get("type", "").startswith("CVSS"):
            return "high"
    return "moderate"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default=str(ROOT / "requirements.txt"))
    parser.add_argument("--fail-on", choices=["none", "low", "moderate", "high", "critical"], default="high")
    args = parser.parse_args()

    packages = parse_requirements(Path(args.requirements))
    if not packages:
        print("没有可扫描的依赖")
        return 0
    try:
        results = query(packages)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"OSV 查询失败，无法给出 CVE 结论：{exc}", file=sys.stderr)
        return 2

    total = 0
    threshold = SEVERITY_ORDER.get(args.fail_on, 3)
    worst = 0
    for spec, vulns in results.items():
        for vuln in vulns:
            level = severity_of(vuln)
            worst = max(worst, SEVERITY_ORDER.get(level, 0))
            total += 1
            print(f"{spec} | {level} | {vuln.get('id')} | {(vuln.get('summary') or '')[:100]}")

    print(f"\n扫描 {len(packages)} 个依赖，发现 {total} 个已知漏洞（最高等级 {worst}）")
    if args.fail_on == "none":
        return 0
    return 1 if worst >= threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
