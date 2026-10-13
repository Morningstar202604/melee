# melee · AI 大乱斗 — 全仓审计与改造报告

- 日期：2026-10-13
- 范围：全仓逐层审计（重复造轮子排查 + 定位匹配度）+ 前端推翻重构 + 后端成熟方案替换
- 方法：逐文件通读（约 8.7k 行源码 + 测试）→ 对照 Node/Next 16/AI SDK v7 内置文档核实 → 改造 → `npm test`（100 用例）/ lint / build / dev 浏览器实测全绿

---

## 一、定位匹配度评估（结论先行）

spec 的定位是"观赏性优先、翻车即内容、战报要能一句话讲完"。改造前的产物**功能闭环完整、工程质量不低**（事件契约、SSE 续传、裁判双轮对调、WASM 沙箱都有真实测试），但存在三类与定位脱节的问题：

1. **验收标准里有一条静默失败**：spec §1.3 要求"裁判分写入 SQLite，均带 position_index"，实际上 `recordJudgement`（`lib/db/repo.ts:419`）**没有任何调用方**，`judgements` 表从未被写过。页面上看不出（Settlement 用实时事件渲染），但数据在库里是缺的，二期偏好学习会踩空。本轮已修复（`lib/battle/runner.ts` persistJudgements）。
2. **观赏性没有落到界面**：观战页只是"并排文本框 + 通用状态条"，冲线、翻车、战报这些核心观赏时刻没有视觉重量；深浅两套主题 + emoji 图标 + 全站淡入动效，是典型的"AI 模板味"，与"赛事转播台"的品牌气质无关。本轮已推翻重构。
3. **硬战场的时间都花在等待上**：沙箱执行是**串行**的（`hard-code.ts` 原实现为 for 循环），且每个选手都冷启一个新的 Pyodide Worker（每次约 1.5~4s 加载 WASM），4 名选手的判定阶段要等十几秒、期间界面零反馈。本轮改为 worker 池复用 + 并行执行 + 沙箱进度实时事件。

---

## 二、逐层分析：每一层是干嘛的、练得对不对

| 层 | 职责 | 结论 |
|---|---|---|
| `app/api/*` | 5 个对战路由 + provider 预检/模型列表 + health | 合理。11 个路由，职责单一，`runtime = 'nodejs'` 显式声明 |
| `lib/battle/events.ts` | 服务端→前端的事件契约（zod discriminated union） | 合理且是本项目最扎实的设计。11 种事件全部有 schema，前端只有一个 reducer（`lib/client/battle-store.ts`） |
| `lib/battle/registry.ts` | 内存事件日志 + 订阅 + 断线重放（Last-Event-ID） | 合理。这是在 EventSource 标准语义上做的最小扩展，不重复造轮子 |
| `lib/battle/runner.ts` | 对战状态机（running→judging→scoring→completed/cancelled/failed） | 合理。取消竞态、静默异常、定时器回收此前已有专门修复 |
| `lib/providers/*` | 9 家 provider 注册表 + AI SDK 流式收口 | 手写重试已替换为 AI SDK 原生 `maxRetries`（见下） |
| `lib/judge/runner.ts` | 三轮位置对调裁判（反位置偏见）+ 汇总 | 产品级设计，保留。默认调用改为 AI SDK 结构化输出 |
| `lib/arena/*` | 赛场抽象（软/硬两个实现）+ 题库 | 合理，extractCode / 判定口径真实可测 |
| `lib/sandbox/*` | Pyodide WASM（默认）/ Judge0（可选）双后端 | 本轮重做 worker 池（见下）；judge0 保留为可选，有已知近似算法（见遗留） |
| `lib/db/*` | node:sqlite 薄封装（client 32 行 / repo 序列化写队列） | **保留，且是被约束逼出来的正确选择**（见下） |
| `lib/log/*` | 日志器 + 值级脱敏（把用户 Key 从任意文本里打掉） | 保留。pino 的字段级 redact 做不到"自由文本里的 Key 打码"，自定义有真实用途 |
| `lib/security.ts` | 零依赖安全头（原在仓库根目录） | 归位到 `lib/`；根目录多文件是历史遗留 |
| `components/arena/*` | 全部 UI | 本轮推翻重构 |
| `workers/pyodide-worker.mjs` | 沙箱 Worker 入口（禁互操作、清 environ、输出限额） | 保留设计，增加"可复用 + 每次运行前状态重置" |
| `roles/ rules/ scenarios/ experts/ framework/ context/ hooks/ mcp/ scaffolds/ scaffold.yaml .cursorrules` | **与本项目无关** | `.git/info/exclude` 里被本地忽略的脚手架工具残留（meituan-catpaw），不在 git 历史里。建议清理：`rm -rf` 这些目录（本地操作，不影响仓库） |

---

## 三、重复造轮子清单（逐项核实 + 处置）

| # | 自造物 | 官方/成熟替代 | 核实依据 | 处置 |
|---|---|---|---|---|
| 1 | `lib/providers/stream.ts` 手写重试循环（maxAttempts/backoff/isRetryable，约 50 行） | AI SDK `streamText` 原生 `maxRetries`（指数退避 + Retry-After 支持） | `node_modules/ai/docs/03-ai-sdk-core/25-settings.mdx:115`「Maximum number of retries… Default: 2」 | **已替换**。保留自己的错误分类（401/429/5xx/abort → 产品语义状态） |
| 2 | `lib/judge/runner.ts` 手工"去围栏 + 截取 {} + 尝试解析" | AI SDK v7 `generateText({ output: Output.object({ schema }) })` 结构化输出 | `node_modules/ai/docs/…/10-generating-structured-data.mdx:27`；v7 已把 `generateObject` 并入 `generateText` 的 `output` | **已替换**（保留宽松解析做兜底，见 #3） |
| 3 | — | provider 不支持 json_schema 时的兼容 | 国内 OpenAI 兼容端点（DeepSeek 等）对 `response_format: json_schema` 支持不一 | **新增兜底**：400/404/422 或结构化解析失败时退回纯文本 + 宽松解析，裁判不降级（有行为锁测试） |
| 4 | `lib/sandbox/pyodide-worker.ts` 每次 run 新建并销毁 Worker | Pyodide 官方用法即为"worker 内单实例长驻"；池化用自实现的 30 行 acquire/release 队列（tinypool 等池库引入成本 > 收益） | 现有测试（死循环 terminate、环境变量隔离、输出限额）全部复用并通过 | **已改造**：2 个 worker 池 + `instrumentation.ts` 启动预热 + 每次运行前 builtins/modules/path/environ 重置；超时/异常即销毁重建 |
| 5 | 沙箱判定串行 + `sandbox.result` 事件已定义但从未发出（死代码） | — | `events.ts` 里 schema 早就有，`runner.ts` 无发射点 | **已补齐**：并行执行（Promise.all）+ 每条结果实时进 SSE，前端战报/卡片实时显示"沙箱 x/y" |
| 6 | `lib/db/*` 手写 SQL 行映射（repo.ts 499 行） | Drizzle / Kysely 等 ORM | **Drizzle 没有 node:sqlite 驱动**（npm exports 只有 better-sqlite3 / libsql / bun-sqlite / d1 等，2026-10-13 实际核查 unpkg `drizzle-orm@latest` package.json）；ORM 化必须引入原生编译依赖，与"Windows 零摩擦 + 零原生编译"冲突 | **保留**。这是被约束逼出的正确选择；spec 也明确要求"薄封装隔离，二期换 Postgres 只改一个文件" |
| 7 | `lib/battle/ratelimit.ts` 手写双层限流（121 行） | `rate-limiter-flexible` / Upstash（需 Redis） | 单进程内存限流，语义简单可控；引入库仍需自写封禁/全局帽逻辑 | **保留**，升级路径已写在文件头注释（多实例时换 Redis sliding window） |
| 8 | `lib/log/*` 手写日志器 | pino | 见二节：值级脱敏是产品硬需求，pino 不支持 | **保留** |
| 9 | 手写 SSE 事件流 + 重连 | EventSource 是 W3C 标准（已用），服务端 SSE 编码无官方替代 | — | **简化**：撤掉"onerror 后关闭再手动重连"的自造逻辑，交给浏览器的原生自动重连（自动带 Last-Event-ID），仅在连续失败 3 次后停止并回退快照 |
| 10 | 无 | `instrumentation.ts`（Next 官方启动钩子） | Next 16 文档 | **新增**：启动时预热一个沙箱 Worker |
| 11 | 加密钥脱敏 | AI SDK 流式错误默认 `console.error(error)` 会**原样打印 provider 响应体**（响应体可能回显 API Key，实测输出中出现过凭证串） | `node_modules/ai/dist/index.js:8706` 默认 `onError = ({error}) => console.error(error)` | **已修**：`onError` 接脱敏 logger，杜绝 Key 进日志 |
| 12 | — | Next 16 跨源 dev 资源拦截 | dev 日志：`Blocked cross-origin request to Next.js dev resource /_next/hmr from "127.0.0.1"` | **已修**：`next.config.ts` 加 `allowedDevOrigins: ['127.0.0.1']`。这是浏览器实测时"页面永不出水合"的根因（HMR 客户端被 403，引导链中断），排查耗时最长的一处 |

---

## 四、本轮实际改动清单

**后端**
- `lib/battle/runner.ts`：裁判分落库（`persistJudgements`，round=0 表示三轮汇聚值，position_index=slot）；沙箱进度接入 SSE `sandbox.result`
- `lib/arena/hard-code.ts`：判定循环并行化；`judgeCodeSubmissions` 逐选手上报进度
- `lib/arena/types.ts`：`JudgeContext.onSandboxResult` + `SandboxProgress`
- `lib/battle/events.ts`：`sandbox.result` 增加 `status`
- `lib/providers/stream.ts`：原生 `maxRetries`；`onError` 防 Key 泄漏
- `lib/judge/runner.ts`：结构化输出 + 兼容兜底
- `lib/sandbox/pyodide-worker.ts` + `workers/pyodide-worker.mjs`：worker 池 + 状态重置
- `instrumentation.ts`：启动预热
- `lib/security.ts`：从仓库根目录归位
- `next.config.ts`：allowedDevOrigins
- `lib/client/battle-store.ts`：`sandbox` 字段；`summaryFromSnapshot`（已结束对战重开也能看战报）

**前端（推翻重构）**
- 设计令牌：`app/globals.css`（@theme）+ `components/arena/theme.ts`（赛场皮肤/队色/状态色）
- 全部组件重写：`arena-setup`（步骤导轨工作台）、`lineup-editor`（datalist 可搜可选）、`battle-shell`、`status-bar`（HUD）、`contestant-card`、`vote-bar`、`settlement`（战报 + 记分板 + 翻车现场）
- 新增依赖（已说明代价）：`clsx` + `tailwind-merge`（~8KB gzip，类名合并标准件）、`lucide-react`（按图标 tree-shaking，~1KB/个）——替换全部 emoji 图标

**测试（100 用例全绿，新增 8 条行为锁）**
- 裁判结构化输出、provider 不兼容兜底、judgements 落库、沙箱并行 + 逐条进度、sandbox.result 先于 judge.done、已结束快照重建战报（软/硬）、运行中快照不重建

## 五、已知遗留（不在本轮范围）

- `lib/sandbox/judge0.ts`：合并多用例为单次提交、按行数比例折算通过数，是近似算法（默认后端 Pyodide 不受影响）。正确做法是逐用例提交
- 事件日志与内存注册表在单进程内，多实例部署需外部化（文件头已注明升级路径）
- 界面为暗色单主题（转播台定位），亮色主题未提供
- rate limit 的 60s 封禁名单等策略需在公网开放前随 Judge0 一起复核

## 六、提交前审计门禁状态（重要）

按 `agent-quality-gates/docs/policies/audit-trigger.md` 分级：本改动触及密钥处理（onError 脱敏）、信任边界（裁判结构化输出/兜底解析）、数据模型（judgements 落库）、安装完整性（3 个新依赖）→ **Gate A 命中 → deep**。

- 托管 Decision Engine MCP 工具在本会话不可用（工具列表无 `mcp__decision-engine__*`），按 `/audit` 路由读取 DE Lite 契约后尝试本地桥：
  - `begin` 成功：`local_host_e4ab0f76f3ae303f06c8d211`（`advisory_only=true`，`audit_id=null`）
  - 五维（logic/edge_cases/security/performance/concurrency）本地咨询完成，ledger 校验通过：`VALID (NEEDS CROSS-LLM RERUN)`，0 CRITICAL / 0 HIGH / 0 MEDIUM / 9 LOW（逐条处置见 ledger）
  - `complete` 被桥拒绝：`local-advisory-rejected`（该主机 capability status 为 `activated`，DE Lite 分支按契约不适用）——**审计门禁未关闭**
- 结论：本次提交在"门禁开启（needs-cross-llm-rerun）"状态下由用户授权进行；待 Decision Engine 可用时，应对该 diff 补跑一次 deep 复核。本地咨询的 ledger 记录：`%TEMP%/2026-10-13-multi-review.md`（单模型、非独立，不能替代跨厂商审计）
