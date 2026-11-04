# 质量与复验

本文件不固化测试数量或覆盖率数字，避免与代码演进漂移。每次交付以本仓库当前命令输出为准。

## 一键门禁

```bash
bash scripts/verify_all.sh
```

脚本依次执行：

1. 后端 pytest；
2. 行覆盖率与分支覆盖率（门禁分别为 91% 与 85%）；
3. ruff；
4. Bandit Medium+；
5. 许可证台账；
6. 前端 JavaScript 语法与 Node 单测；
7. JSON Schema 代码生成物一致性；
8. OSV 依赖漏洞扫描；
9. Dockerfile/Compose 静态校验；
10. Playwright 浏览器审计。

编号 `6b` 是前端 Node 单测，因此日志中会看到 10 个编号组、11 个实际门禁步骤。所有步骤通过时才输出 `ALL GREEN`。

## 单独复验

```bash
.venv/Scripts/python.exe -m pytest -q
node --test apps/web/test/*.test.mjs
.venv/Scripts/python.exe scripts/check_coverage.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe scripts/audit_cves.py --fail-on high
```

Windows 环境使用仓库 `.venv`；若 Python 解释器不在 PATH，使用 `.venv/Scripts/python.exe`。依赖安装沿用项目约定：`uv pip`。

## 结果解释

- Playwright 缺少某个浏览器内核时，脚本会明确显示 `SKIP`；没有任何内核成功运行时才失败。
- Dockerfile 静态校验检查入口、COPY 源、运行时依赖、非 root、健康检查和数据卷；它不等同于 Docker daemon 实际构建，镜像构建由 CI 或目标环境单独验收。
- 真实 Anthropic/Gemini、Redis、ffmpeg 和外部 HTTPAgent 的长期稳定性不能由本地 mock 测试替代；测试锁定的是请求契约、解析、错误处理和公平性边界。
- 测试 fixture 中的示例 Key 仅用于脱敏回归，不能当作生产凭据。

## 质量文档边界

历史审计和发布记录保留原始时间点，不作为当前状态依据。当前状态只由代码、测试、`verify_all.sh` 最新输出和对应 commit 决定。
