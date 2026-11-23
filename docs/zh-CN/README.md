# 文档

[English](../README.md) · **简体中文**

按你要做的事选文档。每篇核心文档都在 `docs/zh-CN/` 有中文版；历史报告放在 `docs/archive/`，明确标注为当时快照。

## 给使用者

| 文档 | 回答什么 |
|---|---|
| [README](../../README.zh-CN.md) | melee 是什么、怎么装、长什么样 |
| [项目状态](STATUS.md) | 现在能做什么、哪些依赖环境、哪些明确不做 |
| [部署](DEPLOY.md) | 单进程、Compose、反代、多 worker、备份 |
| [API](API.md) | HTTP 与 SSE 端点、BYOK 模型、HTTP agent、事件契约 |
| [截图](../screenshots/README.md) | 中英文完整画廊 |

## 给贡献者

| 文档 | 回答什么 |
|---|---|
| [架构](ARCHITECTURE.md) | 分层、五条不变量、关键机制、数据模型 |
| [质量](QUALITY.md) | 门禁有哪些、怎么跑、`SKIP` 怎么读 |
| [公平性](FAIRNESS.md) | 审计发现了什么、改了什么、怎么锁住 |
| [Agent 接入](AGENT-HARNESS.md) | MCP、A2A、HTTPAgent、webhook、打包 |
| [版本与发布](VERSIONING.md) | 语义化范围、版本载体、发布流程 |
| [路线图](ROADMAP.md) | 下一步做什么，以及验收标准 |
| [变更记录](CHANGELOG.md) | 已经发了什么 |

## 归档

[`docs/archive/`](../archive/README.md) 保留审计、对标、调研和早期计划。它们只作为历史参考，不代表当前承诺。
