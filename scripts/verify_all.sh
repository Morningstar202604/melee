#!/usr/bin/env bash
# 一条命令跑完本地全部门禁；任何一步失败立即退出非零。
# 用法：bash scripts/verify_all.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1/11 pytest =="
python -m pytest

echo "== 2/11 coverage (lines>=91, branches>=85) =="
python scripts/check_coverage.py

echo "== 3/11 ruff =="
python -m ruff check .

echo "== 4/11 bandit (medium+) =="
python -m bandit -c pyproject.toml -ll -r services/match

echo "== 5/11 license gate =="
python scripts/check_licenses.py

echo "== 6/11 frontend syntax =="
# 递归扫：src/ 下已有 watch/ 子目录，只扫顶层会漏掉新拆出的模块。
while IFS= read -r -d '' f; do node --check "$f"; done < <(find apps/web/src -name '*.js' -print0)

echo "== 6b/11 frontend unit tests =="
# 零构建前端的纯函数（时间轴映射、去重）用 node --test 直接跑，
# 不需要浏览器与打包器；跳过它等于让 time axis 这类回归无人守。
if command -v node >/dev/null 2>&1; then
  node --test apps/web/test/*.test.mjs
else
  echo "FAIL 未安装 node，前端单测无法执行" >&2
  exit 1
fi

echo "== 7/11 contract codegen =="
python packages/contracts/scripts/codegen.py --check

echo "== 8/11 dependency CVE (OSV) =="
python scripts/audit_cves.py --fail-on high

echo "== 9/11 镜像静态校验（本机无 daemon 时的等价验证） =="
python scripts/verify_dockerfile.py

echo "== 10/11 browser UI audit =="
if python -c "import playwright" 2>/dev/null; then
  # 自带起服务：审计脚本依赖真实 HTTP 与 SSE，不能只有单元测试。
  UI_DB="${TMPDIR:-/tmp}/melee-ui-$$.db"
  MELEE_DB="$UI_DB" MELEE_QUOTA_MATCHES=500 MELEE_PORT=8187 \
    python scripts/serve.py --port 8187 >/dev/null 2>&1 &
  UI_PID=$!
  trap 'kill $UI_PID 2>/dev/null || true' EXIT
  for _ in $(seq 1 40); do
    curl -fsS http://127.0.0.1:8187/health >/dev/null 2>&1 && break
    sleep 1
  done
  # 本机只装了 Playwright 自带 chromium 时，audit_ui 会退回系统 Edge（见 _channel_for）。
  # firefox/webkit 缺失时脚本明确 SKIP 而不是假装通过；一个内核都跑不起来才返回 2。
  python scripts/audit_ui.py --api http://127.0.0.1:8187 \
    --browser chromium --browser firefox --browser webkit
  AUDIT_RC=$?
  kill $UI_PID 2>/dev/null || true
  trap - EXIT
  if [ "$AUDIT_RC" -eq 2 ]; then
    echo "FAIL 浏览器审计一个内核都没跑起来" >&2
    exit 1
  elif [ "$AUDIT_RC" -ne 0 ]; then
    exit "$AUDIT_RC"
  fi
else
  echo "SKIP 未安装 playwright：浏览器 UI 审计未执行（不是通过）"
fi

echo
echo "== 11/11 双语界面巡检 =="
# 双语是产品能力，不是文档装饰：任何一页在任一语言下报错、漏翻或横向溢出都要拦住。
# 具体步骤（起隔离实例 → 跑一场真实比赛 → 巡检）在 check_i18n.py 里。
python scripts/check_i18n.py

echo
echo "ALL GREEN"
