#!/usr/bin/env bash
# 一条命令跑完本地全部门禁；任何一步失败立即退出非零。
# 用法：bash scripts/verify_all.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1/8 pytest =="
python -m pytest

echo "== 2/8 coverage (>=80) =="
python -m pytest --cov=services.match --cov-branch --cov-report=term --cov-fail-under=80 -q

echo "== 3/8 ruff =="
python -m ruff check .

echo "== 4/8 bandit (medium+) =="
python -m bandit -c pyproject.toml -ll -r services/match

echo "== 5/8 license gate =="
python scripts/check_licenses.py

echo "== 6/8 frontend syntax =="
for f in apps/web/src/*.js; do node --check "$f"; done

echo "== 7/8 contract codegen =="
python packages/contracts/scripts/codegen.py --check

echo "== 8/8 dependency CVE (OSV) =="
python scripts/audit_cves.py --fail-on high

echo
echo "ALL GREEN"
