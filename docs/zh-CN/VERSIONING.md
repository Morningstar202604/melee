# 版本与发布流程

[English](../VERSIONING.md) · **简体中文**

这份文件定义 melee 如何编号、一次发布必须包含什么、谁能发布。它的存在是为了让发布成为可重复的流程，而不是凭记忆临场判断。

## 语义化版本

melee 遵循 [SemVer 2.0.0](https://semver.org/)。对外接口是：

| 面 | 例子 | 什么算破坏性变更 |
|---|---|---|
| 事件契约 | `packages/contracts/schema/battle-event.schema.json` | 删字段、收紧类型、改枚举值 |
| HTTP API | `POST /matches`、`GET /matches/{id}/events` | 删端点、改响应结构、改错误状态码 |
| 配置变量 | `MELEE_*` | 改名、删除、改默认值 |
| 引擎接口 | `Game.observe/apply`、`Agent.move` | 改第三方 agent 要实现的方法签名 |
| 静态前端 URL | `watch.html?match=`、`?lang=` | 删掉已有链接依赖的查询参数 |

不属于对外接口、可以随 minor 变化的部分：内部模块划分、解说的具体措辞、日志格式、CSS 类名。

- **MAJOR** — 破坏上述任一接口。
- **MINOR** — 增加能力，向后兼容。
- **PATCH** — 只修 bug，不引入新行为。

1.0 之前的 `0.x` 是开发期历史，不受这套规则约束。

## 版本号存在哪里

每个面只有一个真相来源，测试负责让它们保持一致。一次发布会**同时**更新四处载体——1.9.0 之前只校验前两处，`melee --version` 和打包 exe 曾经悄悄落后于真实版本。

| 文件 | 字段 | 用户在哪里看到 |
|---|---|---|
| `services/match/api.py` | `FastAPI(version=...)` | `/openapi.json`、`/docs` |
| `services/match/cli.py` | `APP_VERSION` | `melee --version` |
| `scripts/build_exe.py` | `APP_VERSION` | 便携包的 INSTALL.txt 与 exe 的 `--version` |
| `apps/web/package.json` | `version` | 静态前端自己的版本 |
| `CHANGELOG.md` | 顶部 `## [x.y.z]` 标题 | 发布本身 |

`services/match/tests/test_architecture.py` 会断言五处载体都与 changelog 顶部一致，漏掉任何一处都会让门禁变红。

## 一次发布包含什么

发布提交必须包含以下全部内容。缺一项就说明这次发布还没完成。

1. **代码**，以及去掉这段代码就会失败的测试。
2. **`CHANGELOG.md` 条目**，放在 `## [x.y.z] — YYYY-MM-DD` 下，使用 Keep a Changelog 的分节：Added / Changed / Deprecated / Removed / Fixed / Security，只写适用的。
3. **版本号更新**（上表四处载体，不只是 API）。
4. **文档更新**：用户能看到的行为变了就要改。截图展示的界面变了就要重拍（`scripts/capture_gallery.py`）。
5. **绿色门禁**：`bash scripts/verify_all.sh` 退出 0 并打印 `ALL GREEN`。`SKIP` 不算通过；如果覆盖的是必需引擎，必须在发布说明里解释。
6. **Git 标签** `vX.Y.Z` 打在发布提交上，并与分支一起推送。

## 发布步骤

```bash
# 1. 从干净的 main 开始
git status --short          # 必须为空
git pull --ff-only

# 2. 全量门禁——这是唯一能决定“能不能发”的东西
bash scripts/verify_all.sh

# 3. 改版本号、写 changelog，然后提交
git add -A
git commit -m "release: vX.Y.Z"

# 4. 打标签并推送
git tag -a vX.Y.Z -m "vX.Y.Z"
git push origin main --tags
```

其余远端镜像同一个提交；镜像列表见 [部署](DEPLOY.md)。

## Changelog 规则

- 每个已发布版本一条。未发布的改动留在提交历史里，不设 `[Unreleased]` 空段——本仓库按批发布，常驻空段只会积累过期文字。
- 写给正在决定要不要升级的人：对他有什么变化，而不是你改了哪个模块。
- 每条都要可核对。“改进了性能”不是条目；“5000 事件的对局战报渲染低于 1 秒”才是。
- 结论不明显时，链接能证明它的测试或文档。

## 弃用

标为弃用的功能至少再工作一个 minor 版本，并在日志里写明替代方案。移除发生在下一个 MAJOR；changelog 要在**弃用当时**就写明，而不是移除时才写。

## 热修

安全或数据完整性问题从 `main` 发 PATCH，不开长期分支。修完、跑门禁、打标签、在同一个提交里写 changelog。如果写清楚修复就必然暴露漏洞细节，那就等下一个版本再写，并说明原因。
