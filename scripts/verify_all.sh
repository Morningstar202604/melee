#!/usr/bin/env bash
# 一条命令跑完本地全部门禁；任何一步失败立即退出非零。
# 用法：bash scripts/verify_all.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== 1/13 pytest =="
python -m pytest

echo "== 2/13 coverage (lines>=91, branches>=85) =="
python scripts/check_coverage.py

echo "== 3/13 ruff =="
python -m ruff check .

echo "== 4/13 bandit (medium+) =="
python -m bandit -c pyproject.toml -ll -r services/match

echo "== 5/13 license gate =="
python scripts/check_licenses.py

echo "== 6/13 frontend syntax =="
# 递归扫：src/ 下已有 watch/ 子目录，只扫顶层会漏掉新拆出的模块。
while IFS= read -r -d '' f; do node --check "$f"; done < <(find apps/web/src -name '*.js' -print0)

echo "== 6b/13 frontend unit tests =="
# 零构建前端的纯函数（时间轴映射、去重）用 node --test 直接跑，
# 不需要浏览器与打包器；跳过它等于让 time axis 这类回归无人守。
if command -v node >/dev/null 2>&1; then
  node --test apps/web/test/*.test.mjs
else
  echo "FAIL 未安装 node，前端单测无法执行" >&2
  exit 1
fi

echo "== 7/13 contract codegen =="
python packages/contracts/scripts/codegen.py --check

echo "== 8/13 dependency CVE (OSV) =="
python scripts/audit_cves.py --fail-on high

echo "== 9/13 镜像静态校验（本机无 daemon 时的等价验证） =="
python scripts/verify_dockerfile.py

echo "== 9b/13 installer template =="
# 安装器是发布产物：模板必须能渲染、版本必须与 CHANGELOG 同步。
# 只查模板（秒级）；完整的"安装→运行→卸载"在发布流程手动跑一次
# （PyInstaller + ISCC 全程约 10 分钟，不适合每轮门禁）。
python scripts/build_installer.py --check

echo "== 10/13 browser UI audit =="
if python -c "import playwright" 2>/dev/null; then
  # 自带起服务：审计脚本依赖真实 HTTP 与 SSE，不能只有单元测试。
  #
  # 端口必须动态取：写死 8187 时，若机器上已有别的实例占着它，新服务会
  # 静默绑定失败退出，而 curl /health 仍返回旧实例的 200——审计就跑在
  # 旧代码上（实测出现过：5 个页面报 400，单独跑却全绿）。
  UI_PORT=$(python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
  UI_DB="${TMPDIR:-/tmp}/melee-ui-$$.db"
  MELEE_DB="$UI_DB" MELEE_QUOTA_MATCHES=500 MELEE_PORT="$UI_PORT" \
    python scripts/serve.py --port "$UI_PORT" >/dev/null 2>&1 &
  UI_PID=$!
  trap 'kill $UI_PID 2>/dev/null || true' EXIT
  UI_READY=0
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$UI_PORT/health" >/dev/null 2>&1; then
      UI_READY=1
      break
    fi
    sleep 1
  done
  if [ "$UI_READY" -ne 1 ]; then
    echo "FAIL 审计服务在端口 $UI_PORT 上没起来" >&2
    exit 1
  fi
  # 本机只装了 Playwright 自带 chromium 时，audit_ui 会退回系统 Edge（见 _channel_for）。
  # firefox/webkit 缺失时脚本明确 SKIP 而不是假装通过；一个内核都跑不起来才返回 2。
  python scripts/audit_ui.py --api "http://127.0.0.1:$UI_PORT" \
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
echo "== 10b/13 主题对比度（亮/暗两套主题，WCAG AA） =="
# 主题不是装饰：同一份文案在亮色与深色下都必须读得清。真实浏览器取
# computed style，因为渐变与半透明叠加只有浏览器算得准。
if python -c "import playwright" 2>/dev/null; then
  CONTRAST_PORT=$(python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()")
  CONTRAST_DB="${TMPDIR:-/tmp}/melee-contrast-$$.db"
  MELEE_DB="$CONTRAST_DB" MELEE_QUOTA_MATCHES=500 MELEE_PORT="$CONTRAST_PORT"     python scripts/serve.py --port "$CONTRAST_PORT" >/dev/null 2>&1 &
  CONTRAST_PID=$!
  trap 'kill $CONTRAST_PID 2>/dev/null || true' EXIT
  CONTRAST_READY=0
  for _ in $(seq 1 40); do
    if curl -fsS "http://127.0.0.1:$CONTRAST_PORT/health" >/dev/null 2>&1; then
      CONTRAST_READY=1
      break
    fi
    sleep 1
  done
  if [ "$CONTRAST_READY" -ne 1 ]; then
    echo "FAIL 对比度门禁的服务没起来（端口 $CONTRAST_PORT）" >&2
    exit 1
  fi
  python scripts/check_contrast.py --api "http://127.0.0.1:$CONTRAST_PORT"
  kill $CONTRAST_PID 2>/dev/null || true
  trap - EXIT
else
  echo "SKIP 未安装 playwright：对比度门禁未执行（不是通过）"
fi

echo
echo "== 11/13 双语界面巡检 =="
# 双语是产品能力，不是文档装饰：任何一页在任一语言下报错、漏翻或横向溢出都要拦住。
# 具体步骤（起隔离实例 → 跑一场真实比赛 → 巡检）在 check_i18n.py 里。
python scripts/check_i18n.py

echo
echo "== 12/13 外部可观测验收 =="
# 只对运行中的服务发 HTTP：每一项都是用户或集成方能看到的承诺。
# 单元测试全绿但功能不可达的情况，只有这一层能拦住（历史事故：宿敌
# 面板永远为空、双循环第二回合永远不跑、中立队被判获胜）。
python scripts/check_acceptance.py

echo
echo "ALL GREEN"
