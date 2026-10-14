# 官方符合性与前沿方案对照报告

- 日期：2026-10-13
- 方法：以 `node_modules` 内的官方文档与类型定义为准（AI SDK v7.0.136 内置 docs + d.ts、Next 16.4.0 内置 docs、依赖包自身元数据），逐项核对本项目的"引入"是否官方路径；再对照 AI SDK `03-agents/*`（agents / workflows / memory / subagents / loop-control）与 `06-advanced/*`（caching / multiple-streamables）评估是否采用
- 纪律：能官方就官方；自研必须给出判决理由；每项落到"采用 / 保留（附理由）/ 二期候选"

## 一、逐项核对：现有引入 vs 官方路径

| 引入 | 官方出处核对 | 判决 |
|---|---|---|
| Next 16 App Router + Route Handlers + `runtime='nodejs'` | `node_modules/next/dist/docs/01-app/*` | 官方用法 ✓ |
| `instrumentation.ts` 启动钩子（预热沙箱 Worker） | Next 16 文档（`02-project-structure.md` 等） | 官方机制 ✓ |
| `allowedDevOrigins`（修 127.0.0.1 不水合） | Next 16 dev 安全默认 + 官方建议配置 | 官方建议 ✓ |
| `cacheComponents:false` / `partialPrefetching:false` | Next 16 官方配置项（显式关闭新渲染特性以保持行为） | 保留 ✓ |
| Tailwind v4 + `@tailwindcss/turbopack` loader | 包元数据：`tailwindlabs/tailwindcss` 官方仓库、provenance 发布，"A Turbopack loader for Tailwind CSS v4" | 官方路径 ✓（Next 文档的 postcss 方案是通用路径；Turbopack 下用官方 loader） |
| AI SDK v7 `streamText` / `generateText` | `docs/03-ai-sdk-core/05-generating-text.mdx` | 官方 ✓ |
| 原生 `maxRetries`（此前替换手写退避） | `25-settings.mdx:115` | 官方 ✓ |
| `Output.object({schema})` 裁判结构化输出 + 兼容兜底 | v7 已把 `generateObject` 并入 `generateText` 的 `output` | 官方 ✓ |
| `ai/test` 的 `MockLanguageModelV4`（测试夹具） | 官方测试工具（`ai/test` 导出） | 官方 ✓ |
| `node:sqlite`（DatabaseSync） | Node 内置 API（experimental） | 官方 ✓（Drizzle 无 node:sqlite 驱动，此前已复核 npm exports） |
| chess.js（残局校验） | 社区标准国际象棋库 | 成熟件 ✓ |
| zustand / zod / clsx / tailwind-merge / lucide-react | 各自领域社区标准 | 成熟件 ✓ |
| 手写 SSE 事件契约（zod discriminated union + EventSource 原生重连） | 对照 `04-ai-sdk-ui/50-stream-protocol.mdx`（UIMessageChunk 协议） | **保留**：UIMessage 协议建模"单会话"，本项目是"多选手多路复用赛事流"（11 种事件 × N 槽位）；现有契约有 140 用例与续传语义，迁移收益为负。若做单模型聊天页再用 `useChat` |
| 手写双层限流 / 日志脱敏 / node:sqlite 薄封装 | 前报告中已逐项判决 | 保留（附理由见 `2026-10-13-project-audit.md`） |

## 二、本轮采用（4 项官方方案）

1. **官方 reasoning 流全链**（`03-ai-sdk-core/26-reasoning.mdx` + d.ts `TextStreamReasoningDeltaPart`）
   - `streamText` 的 `reasoning-delta{text}` 部件 → 新事件 `contestant.reasoning` → store 按回合归并 → 卡片/时间线"思考过程"面板（流式中自动展开并计数，完成后折叠可查）
   - 单轮竞技的思考过程落库（`contestants.reasoning_text`，含旧库 `PRAGMA table_info` + `ALTER TABLE` 迁移）；多轮落进 turns JSON
   - 只思考未作答仍按 `empty` 判翻车（口径不变，有行为锁）
2. **多轮改为官方消息数组对话**（对照 `03-agents/03-workflows.mdx` 的 sequential 模式与 provider 会话语义）
   - `TurnPlan.buildTurnMessages` 返回标准 `ModelMessage[]`：`user(角色+历史要求) → assistant(自己的上一轮) → user(对手发言+本轮要求) → …`
   - 相比"把所有内容拼进一个 user 字符串"：前缀稳定（provider 提示缓存友好）、角色语义正确、可无缝接 `pruneMessages`
3. **judge0 改官方 batch + expected_output 判定**（原实现是"合并用例按行数折算"的近似算法）
   - `POST /submissions/batch` 每用例一份提交、带 `expected_output`；轮询 `GET /submissions/batch?tokens=…`
   - 通过数=Accepted 计数（不再折算）；编译错误/全超时/运行时错误各有语义；新增假 Judge0 服务器夹具 + 9 条行为锁
4. **消息修剪/缓存评估结论落地**：`pruneMessages`（d.ts:6959）暂不需要（固定 3 轮）；响应级缓存中间件（`06-advanced/04-caching.mdx`）不适用（每场请求唯一）——**做了它建议的前提**：把多轮前缀做稳定（见 2）

## 三、审视但有意不采用（附判决理由）

| 官方/前沿方案 | 出处 | 判决 |
|---|---|---|
| `ToolLoopAgent`（Agent）与工具循环 `stopWhen` | `03-agents/02-building-agents.mdx`、`04-loop-control.mdx` | **不采用（本期）**：本产品的运动规则是"比裸模型单次作答"，给选手挂工具/循环会改变比赛性质。**二期候选**："工具场"（模型自带工具解题）用 `tool()` + `stopWhen` 直接搭 |
| Workflows（sequential/parallel/orchestrator-worker 模式） | `03-agents/03-workflows.mdx` | **已按模式实现**：多轮编排器就是 sequential + parallel（轮内并发）；编排器形态与官方模式对齐，不需要额外抽象层 |
| Memory（Anthropic memory tool / 自定义记忆工具） | `03-agents/06-memory.mdx` | **不采用（本期）**：官方 memory 面向"常驻助手"，本项目是赛事——跨场记忆的正确形态是"战绩档案"（我们的 SQLite 数据），已有押注战绩/导演数据。**二期候选**："常驻选手"人设记忆（让模型记得上次被打脸的事）时可挂 memory tool |
| Subagents / 多智能体协作 | `03-agents/06-subagents.mdx` | **不采用**：项目里"多模型"是竞技对手而非协作智能体，语义不同 |
| UIMessage 流协议 + `useChat` | `04-ai-sdk-ui/50-stream-protocol.mdx` | 保留自研（理由见一表） |
| Provider 响应缓存中间件 | `06-advanced/04-caching.mdx` | 不适用（无重复请求）；提示缓存靠消息数组前缀稳定 + provider 自动能力 |

## 四、本轮还清的债

- judge0 近似判定 → 官方 batch 逐用例判定（含 9 条行为锁）
- 死代码：`runner.ts` 的 `phaseLabel/statusLabel`、`hard-code.ts` 的 `getHardCodeTopic` 已删
- 陈旧构建产物 `out/` 清理
- 旧库迁移补齐（`reasoning_text` 列，`CREATE TABLE IF NOT EXISTS` 不会补列）

## 五、未关闭项与二期候选

- **审计门禁**：跨厂商 deep 审计仍不可用（DE 托管工具缺失），`17d9f39/469c463` 与本轮新提交均处于 `needs-cross-llm-rerun`
- **二期候选（官方方案已定位）**：工具场（`ToolLoopAgent`+`tool()`+`stopWhen`）、单模型聊天页（`useChat`+UIMessage 协议）、`pruneMessages`（轮次增多时）、`LanguageModelV4Middleware`（遥测/缓存统一入口）、常驻选手记忆（memory tool）
