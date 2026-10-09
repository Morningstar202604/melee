# melee 第一期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个本地可跑的 Web 应用：选多个大模型 + 一个题目 → 实时观战它们的流式输出 → 软战场人类投票 + LLM 裁判双轨判定 → 硬战场代码在沙箱里真跑并按通过率判定 → 出结构化战报。

**Architecture:** Next.js 全栈单体（App Router，Node runtime）。Route Handler 做 SSE 网关与编排；`Arena` 抽象把"比什么"做成插件；沙箱做成 `SandboxBackend` 接口，默认 Pyodide WASM 跑在专用 Worker 里，Judge0 留作可选的 OS 级隔离后端。SQLite 落库，Key 只存在于当次对战的服务端内存。

**Tech Stack:** Next.js >=16.3.6（App Router / Turbopack）、Node >=24.14、Vercel AI SDK 7.x（`ai` + `@ai-sdk/openai`）、`node:sqlite`、`pyodide`、`zustand`、`zod`、Node 内置 `node:test`。

**Spec:** `docs/superpowers/specs/2026-10-09-ai-battle-arena-design.md`

## Global Constraints

以下每条对所有 task 生效，值与 spec 逐字一致：

- **产品目的是观赏性与情绪价值，不是评测能力**（spec §0）。观感预算高于工程严谨度；但"没真实发生的事必须明说"这条底线不放宽。
- **不重复造轮子**：能用成熟库或协议标准解决的，不手搓。下表是本项目唯一豁免清单——豁免项必须写明理由，否则视为违规。

| 需求 | 采用 | 手写替代（禁止） |
|---|---|---|
| 模型流式调用 | Vercel AI SDK | 自造 SSE 解析 |
| 沙箱 runtime | Pyodide 官方 npm 包 | 自造解释器/沙箱 |
| 客户端流式状态 | **Zustand** | 手搓 store / 手写订阅 |
| 裁判输出与事件校验 | **Zod** schema + 容错回退 | 手写宽松正则 |
| SSE 断线补发 | **协议自带的 `id:` + `Last-Event-ID`** | 自造"缓冲 500 条"队列 |
| 沙箱后端切换 | 单一接口 + `SANDBOX_BACKEND` 环境变量 | 自造插件/依赖注入框架 |

两处**有意保留手写**，理由如下，不得改为引依赖：① SQLite 用 raw SQL + 薄封装——5 张表、无迁移需求，ORM 在此是净负担；② 限流约 10 行纯函数——引依赖不划算。

- Next.js `>=16.3.6`；**禁用** `next/og` 与 Image Optimization（2026 年多个 critical RCE 涉及这两处）。
- Node `>=24.14`。所有 Route Handler **必须**显式 `export const runtime = 'nodejs'`（`node:sqlite` 不能在 Edge/middleware 用）。
- Next 16 动态路由 `ctx.params` 是 `Promise`，**必须** `await`。
- `lib/` 内部一律**相对路径**导入且不带扩展名；**禁止**在 `lib/` 里使用 `@/` 别名（否则 `node --test` 无法解析）。`app/` 与 `components/` 可以用 `@/`。
- 沙箱四条硬约束（对应真实逃逸事故，不许省）：① 禁止宿主进程内 `loadPyodide()`；② 每提交新建 Worker、结束即 `terminate()`；③ Worker 内零凭证、零 DB 连接、零宿主回调；④ 禁用 Python 侧 `js` 模块与 `pyodide.globals` 互操作。
- 沙箱参数：`SANDBOX_BACKEND` 默认 `pyodide`；Pyodide 时间上限 **15s**；Judge0 为 `wall time 5s` / `memory 256MB` / `max_processes 32` / `max_file_size 1MB` / 禁网。stdout 采集上限 64KB。
- 各参赛模型超时 **120s**（可配）。`run` 入口限流 **每 IP 每分钟 5 场**。
- API Key 只出现在请求体内，服务端仅存于当次对战内存：**不写库、不写日志**。日志统一经 `redact()` 处理。
- 诚实降级：沙箱不可用 → 不真实执行、`isolation_level='none'`、战报出横幅；裁判不可用 → 跳过裁判轨、出横幅。**任何情况下不得把未执行的代码写成通过。**
- 测试用 Node 内置 `node:test`，**不引入**任何测试框架依赖。`npm test` = `node --test "lib/**/*.test.ts"`。
- 涉及密钥、信任边界输入、沙箱执行的改动，**爆炸半径按 `deep` 处理**：走 `aqg-code-construction` 的 6 步，提交前过一次审计门禁。
- 提交信息用 Conventional Commits + 中文描述。里程碑推送，不按改动推送。

## Review Focus

spec 暗示但没有 task 明确覆盖、最容易在真实使用中咬人的输入与故障：

1. **单个模型 Key 无效 / 欠费**：该选手显示可读原因（含 provider 名与状态），其余选手照常流式输出；**不得**整场失败。
2. **模型输出里没有代码块**（硬战场）：必须判 0 分并标注"未产出可执行代码"，**不得**猜测抽取或把整段文本当代码提交。
3. **裁判返回畸形输出**（无 JSON、带围栏、字段缺失、纯散文）：降级为"裁判不可用，仅人类投票"并出横幅，**不得**让整场 500。
4. **沙箱内提交代码读 `os.environ`**：必须拿不到任何值（Worker 内零凭证），这是 §6.2 逃逸缓解的核心断言。
5. **死循环代码**：必须在 wall time 内被 `terminate()` 截断，沙箱返回 `timeout`，**不得**让整个对战请求挂住。
6. **SSE 断线重连**：重连后经 `Last-Event-ID` 续传已收内容，不出现空白页、不重复回放已显示的文本。
7. **长输出炸屏**：某个模型输出上万字时，观战页不能因每 chunk 重渲染而卡死（这是观赏性事故，不只是性能问题）。

---

## 文件结构（先锁死）

| 文件 | 职责 |
|---|---|
| `app/page.tsx` | 擂台首页（server component，壳） |
| `app/battle/[id]/page.tsx` | 观战页（server component，壳） |
| `app/api/providers/ping/route.ts` | 预检候选模型可达性 |
| `app/api/providers/models/route.ts` | 拉取 OpenAI 兼容端点的模型列表 |
| `app/api/battle/run/route.ts` | 建对战 |
| `app/api/battle/[id]/stream/route.ts` | SSE 事件流 |
| `app/api/battle/[id]/vote/route.ts` | 投票 |
| `app/api/battle/[id]/cancel/route.ts` | 取消 |
| `app/api/battle/[id]/snapshot/route.ts` | 断线重连快照 |
| `lib/log/redact.ts` | 日志脱敏 |
| `lib/db/client.ts` | `node:sqlite` 薄封装（唯一直接碰 SQLite 的文件） |
| `lib/db/schema.sql` | 建表 DDL |
| `lib/db/repo.ts` | 全部读写，写操作经内存队列串行化 |
| `lib/providers/registry.ts` | provider 清单 + `createModel` + `listModels` |
| `lib/providers/stream.ts` | `streamModel`：AI SDK 差异的唯一收口处 |
| `lib/arena/types.ts` | `Arena` / `Submission` / `Verdict` 等类型 |
| `lib/arena/soft.ts` | 软战场实现 |
| `lib/arena/hard-code.ts` | 硬战场实现（含代码块抽取） |
| `lib/arena/topics.ts` | 预置题库（软题 + Python 题含测试用例） |
| `lib/judge/runner.ts` | 裁判 prompt 构建 + 宽松解析 + 双轮一致性 |
| `lib/sandbox/backend.ts` | `SandboxBackend` 接口 + `getBackend()` |
| `lib/sandbox/pyodide-worker.ts` | 主线程侧：起 Worker、计时、terminate |
| `lib/sandbox/judge0.ts` | 可选后端（HTTP） |
| `workers/pyodide-worker.mjs` | Worker 入口（纯 .mjs，不经打包） |
| `lib/battle/events.ts` | 事件类型（前后端共享语义） |
| `lib/battle/registry.ts` | 运行中对战的内存注册表 + 事件订阅 |
| `lib/battle/runner.ts` | 状态机与编排 |
| `lib/battle/ratelimit.ts` | 每 IP 每分钟 5 场 |
| `lib/client/battle-store.ts` | Zustand store（rAF 批量合并写） |
| `components/arena/arena-setup.tsx` | 选题 + 选模型 + 预检 |
| `components/arena/battle-shell.tsx` | EventSource 订阅 + 布局 |
| `components/arena/contestant-card.tsx` | 单个选手卡片（流式 / 失败态） |
| `components/arena/vote-bar.tsx` | 投票条 + 实时票数 |
| `components/arena/settlement.tsx` | 战报（双轨并列 + 沙箱标注 + 降级横幅） |

`workers/` 放在项目根、不进 `app/`，因为 `new Worker(path)` 需要运行期真实文件路径，Next 的打包器会破坏放进 `app/` 的文件。

---

### Task 1: 脚手架与工具链

**Files:**
- Create: 由 `create-next-app` 生成；Create `.gitignore` 追加项；Modify `package.json`（脚本 + 依赖）
- Test: `lib/smoke.test.ts`

**Interfaces:**
- Consumes: 无
- Produces: `npm run dev`、`npm test`（`node --test "lib/**/*.test.ts"`）、`npm run build` 可用；`@/` 别名在 `app/`、`components/` 中可用

- [ ] **Step 1: 脚手架**

```
npx create-next-app@latest . --typescript --app --tailwind --eslint --no-src-dir --import-alias "@/*" --yes
```

若它试图重新初始化 git，忽略即可（`git init` 幂等，不会覆盖已有提交 `bedb49a`）。

- [ ] **Step 2: 装依赖**

```
npm i ai @ai-sdk/openai @ai-sdk/anthropic @ai-sdk/google zod pyodide zustand
```

不锁 provider 小组件版本，用 latest 解析；装完把实际版本号写进 `package.json` 并提交。若 `pyodide` 安装因体积/网络失败，记录错误并继续 Task 9 再处理。

- [ ] **Step 3: 写冒烟测试**

`lib/smoke.test.ts`：

```ts
import { test } from 'node:test'
import assert from 'node:assert/strict'

test('test runner 能加载 TypeScript 测试文件', () => {
  assert.equal(1 + 1, 2)
})
```

- [ ] **Step 4: 加 npm scripts 并验证测试跑通**

`package.json` 的 `scripts` 加 `"test": "node --test \"lib/**/*.test.ts\""`。

Run: `npm test`
Expected: 1 passing

若报 `ERR_MODULE_NOT_FOUND` 而非通过，说明 Node 无法解析测试里的导入：加 `npm i -D tsx` 并把脚本改为 `tsx --test "lib/**/*.test.ts"`。**这是本计划唯一允许的偏离，且必须在 commit message 里写明。**

- [ ] **Step 5: 补 `.gitignore`**

追加：`node_modules/`、`.next/`、`data/`、`.env`、`*.db`、`*.db-wal`、`*.db-shm`。

- [ ] **Step 6: 验证 dev server 起来**

Run: `npm run dev`（后台），然后 `curl -s -o NUL -w "%{http_code}" http://localhost:3000`
Expected: `200`

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "chore: Next.js 16 脚手架、依赖与 node:test 工具链"
```

---

### Task 2: 数据层

**Files:**
- Create: `lib/db/schema.sql`、`lib/db/client.ts`、`lib/db/repo.ts`、`lib/db/repo.test.ts`
- Test: `lib/db/repo.test.ts`

**Interfaces:**
- Consumes: 无
- Produces:
  - `client.ts`: `openDb(file: string): Database`、`getDb(): Database`、`closeDb(): void`
  - `repo.ts`: `createBattle(b: NewBattle): string`、`getBattle(id: string): BattleRow | undefined`、`insertContestant(c: NewContestant): void`、`updateBattlePhase(id: string, phase: BattlePhase, patch?: { error?: string; completedAt?: number }): void`、`recordVote(v: NewVote): void`、`recordJudgement(j: NewJudgement): void`、`recordSandboxResult(r: NewSandboxResult): void`、`getSnapshot(id: string): Snapshot | undefined`

- [ ] **Step 1: 写失败的测试**

`lib/db/repo.test.ts`，使用临时库文件 `data/test-<random>.db`：

```ts
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { openDb } from './client.ts'
import { createBattle, insertContestant, recordVote, getSnapshot } from './repo.ts'

test('createBattle 返回 id 并可读回', () => { /* createBattle({arena:'soft',topic:'t'}) 后 getBattle(id).status === 'pending' */ })

test('同一 battle 下同一 voter_key 只能投一票', () => {
  /* 先 recordVote 一次，第二次必须抛错（UNIQUE(battle_id, voter_key)） */
})

test('vote 落库时写入 position_index', () => {
  /* contestant slot=2 的票，position_index 必须是 2 */ })
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— `Cannot find module './client.ts'`

- [ ] **Step 3: 实现 `lib/db/schema.sql`**

按 spec §5 五张表 `battles` / `contestants` / `votes` / `judgements` / `sandbox_results` 逐字建表（含 `UNIQUE(battle_id, voter_key)`、`position_index` 列、`sandbox_results.backend` 与 `isolation_level` 列）。文件开头写 `PRAGMA journal_mode=WAL;`。

- [ ] **Step 4: 实现 `lib/db/client.ts`**

`import { DatabaseSync } from 'node:sqlite'`。`openDb(file)` 执行 WAL pragma + `schema.sql`（用 `node:fs` 读 `schema.sql` 再 `db.exec`），返回 `Database`。`getDb()` 懒初始化到 `process.env.MELEE_DB ?? 'data/melee.db'`，并 `mkdirSync(dirname, { recursive: true })`。`closeDb()` 供测试用。

- [ ] **Step 5: 实现 `lib/db/repo.ts`**

所有 `stmt.run(...)` 包在一个模块级 `writeQueue: Promise<unknown>` 链里串行化（`const next = writeQueue.then(() => doWrite()); writeQueue = next.catch(() => {}); return next`），读不排队。`getSnapshot(id)` 返回 `BattleRow + contestants[] + votes[] + judgements[] + sandboxResults[]` 的聚合对象。

- [ ] **Step 6: 跑测试确认通过**

Run: `npm test`
Expected: 全部 PASS

- [ ] **Step 7: 验证 `node:sqlite` 在 Next runtime 下可用**

Create `app/api/health/route.ts`：`export const runtime = 'nodejs'`，`GET` 里调 `getDb()` 并返回 `{ ok: true, battleCount: number }`。

Run: `curl -s http://localhost:3000/api/health`
Expected: `{"ok":true,...}`。若报 `node:sqlite` 不可用，把 `getDb()` 换成动态 `await import('node:sqlite')` 再试一次；仍失败则停下来报告，不要私自换驱动。

- [ ] **Step 8: 提交**

```bash
git add lib/db app/api/health package.json
git commit -m "feat(db): node:sqlite 封装、schema 与串行写队列"
```

---

### Task 3: provider 适配层与预检

**Files:**
- Create: `lib/providers/registry.ts`、`lib/providers/stream.ts`、`lib/providers/registry.test.ts`、`lib/providers/stream.test.ts`、`test/fixtures/fake-openai-server.ts`、`app/api/providers/ping/route.ts`、`app/api/providers/models/route.ts`
- Test: `lib/providers/registry.test.ts`、`lib/providers/stream.test.ts`

**Interfaces:**
- Consumes: Task 2 的 `lib/log/redact` 不涉及；无
- Produces:
  - `registry.ts`: `PROVIDERS: ProviderDef[]`、`createModel(p: ProviderDef, modelId: string, apiKey: string): LanguageModel`、`listModels(p: ProviderDef, apiKey: string): Promise<{ ok: boolean; models?: string[]; reason?: string }>`
  - `stream.ts`: `streamModel(model: LanguageModel, messages: ModelMessage[], opts: { signal: AbortSignal }): AsyncGenerator<StreamEvent>`，其中 `StreamEvent = { type: 'delta'; text: string } | { type: 'done'; usage: { prompt: number; completion: number } | null; latencyMs: number } | { type: 'error'; status: ContestantStatus; message: string }`；`stream.ts` **同时定义并导出** `ContestantStatus = 'ok' | 'timeout' | 'rate_limited' | 'auth_failed' | 'provider_error' | 'empty'`（该类型的唯一出处，Task 4 从这里 import）

- [ ] **Step 1: 写失败的测试（provider 工厂）**

`lib/providers/registry.test.ts`：断言 `PROVIDERS` 含 `ollama` / `deepseek` / `siliconflow` / `zhipu` / `moonshot` / `dashscope`（`kind: 'openai-compatible'`, `region: 'cn'`, `needsProxy: false`）与 `openai` / `anthropic` / `google`（`needsProxy: true`）；断言 `createModel(PROVIDERS.find(p=>p.id==='ollama')!, 'qwen3', 'ollama')` 返回非空对象且不抛错。

- [ ] **Step 2: 写失败的测试（流式解析）**

先实现 `test/fixtures/fake-openai-server.ts`：`node:http` 起一个服务，按固定脚本对 `/chat/completions` 回 SSE 分片 `["你", "好"]` 再结束；导出 `startFakeServer(): Promise<{ baseUrl: string; close: () => Promise<void> }>`。

`lib/providers/stream.test.ts`：对 `createOpenAI({ baseUrl, apiKey: 'test' })` 造出的模型调 `streamModel`，断言收到两个 delta `你`、`好`（顺序与拼接都要断言），再收到 `done`。

- [ ] **Step 3: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— `Cannot find module './registry'`

- [ ] **Step 4: 实现 `registry.ts`**

`ProviderDef = { id, label, kind: 'openai-compatible' | 'anthropic' | 'google', baseUrl?: string, region: 'cn' | 'global', needsProxy: boolean }`。**不硬编码 model id**（会过期），`defaultModelId` 设为可选字段且列表里留空，由 UI 手填或从 `listModels` 选。

`createModel`：`openai-compatible` → `createOpenAI({ baseURL: p.baseUrl, apiKey })` 的 `(modelId)`；`anthropic` → `createAnthropic({ apiKey })(modelId)`；`google` → `createGoogleGenerativeAI({ apiKey })(modelId)`。类型从 `ai` 包导入 `LanguageModel`。

`listModels`：仅 `openai-compatible` 支持，`fetch(p.baseUrl + '/models', { headers: { Authorization: 'Bearer ' + apiKey } })` 取 `data[].id` 并排序；`kind !== 'openai-compatible'` 直接返回 `{ ok: false, reason: '该 provider 需手填模型 id' }`；HTTP 非 2xx 返回 `{ ok: false, reason: \`HTTP ${status}\` }`。

- [ ] **Step 5: 实现 `stream.ts`**

用 `streamText({ model, messages, abortSignal })`，`for await (const chunk of result.textStream)` 累积 delta；token 用量取 `result.usage`（若类型不匹配改 `result.totalUsage`，以 `tsc` 通过为准）。把异常映射为 `Submission.status` 之一：401/403 → `auth_failed`，429 → `rate_limited`，AbortError → `timeout`，其余 → `provider_error`。**错误消息必须 redact 掉 Key 且带 provider 名。**

- [ ] **Step 6: 实现两个路由**

`app/api/providers/ping/route.ts`：`export const runtime = 'nodejs'`，`POST` 收 `{ contestants: [{ providerId, modelId, apiKey }] }`，对每个跑一次**最小流式请求**（如 maxOutputTokens 取最小值），返回 `[{ slot, ok, status, message }]`，不写库。

`app/api/providers/models/route.ts`：`POST` 收 `{ providerId, apiKey }`，透传 `listModels` 结果。

- [ ] **Step 7: 跑测试确认通过 + 类型检查**

Run: `npm test && npx tsc --noEmit`
Expected: 测试全 PASS，tsc 无错误

- [ ] **Step 8: 提交**

```bash
git add lib/providers test/fixtures app/api/providers package-lock.json
git commit -m "feat(providers): provider 注册表、AI SDK 流式收口与预检/模型列表路由"
```

---

### Task 4: 事件契约、内存注册表与对战状态机

**Files:**
- Create: `lib/battle/events.ts`、`lib/battle/registry.ts`、`lib/battle/runner.ts`、`lib/battle/runner.test.ts`、`lib/arena/types.ts`
- Test: `lib/battle/runner.test.ts`

**Interfaces:**
- Consumes: Task 3 的 `streamModel(model, messages, { signal })`；Task 2 的 repo 函数
- Produces:
  - `arena/types.ts`: `ArenaId`、`ContestantInput`、`Submission`、`Verdict`、`JudgeContext`、`Arena` —— 逐字照 spec §4.1 的签名。**`ContestantStatus` 不在这里定义**，从 `../providers/stream` import 后 re-export。**本 task 必须先定义这些类型**，后续所有 arena 实现与 runner 都依赖它们。
  - `events.ts`: `BattlePhase = 'pending'|'running'|'judging'|'scoring'|'completed'|'cancelled'|'failed'`，以及 `BattleEvent` 联合类型（`phase.changed` / `contestant.started` / `contestant.delta` / `contestant.done` / `contestant.failed` / `judge.delta` / `judge.done` / `sandbox.result` / `vote.tally` / `battle.completed` / `battle.failed`）
  - `registry.ts`: `registerBattle(id, handle): void`、`getBattleHandle(id): BattleHandle | undefined`，`BattleHandle = { emit(e: BattleEvent): void; subscribe(fn: (e: BattleEvent) => void): () => void; cancel(): void }`
  - `runner.ts`: `startBattle(input: StartBattleInput): Promise<string>`（battleId，异步跑不阻塞返回）、`cancelBattle(id: string): boolean`

- [ ] **Step 1: 写失败的状态机测试**

`lib/battle/runner.test.ts`，注入一个可编程的假 provider（不碰网络）：`makeFakeModel(script: { chunks: string[]; fail?: Submission['status'] })`，返回 `{ spec: 'fake', script }` 形状的对象。断言四条路径：

```ts
test('全部选手成功 → phase 依次 running/completed，contestant.done 各一次')
test('一个选手失败 → 其余仍完成，battle 仍 completed')
test('全部失败 → battle 变 failed 且给出 reason')
test('cancelBattle → phase 变 cancelled，在飞的 stream 被 abort')
test('事件 id 从 0 单调递增且不重复（Last-Event-ID 依赖它）')
```

每条都要断言**事件顺序**（`phase.changed` 在前，`contestant.started` 在 delta 之前，`battle.completed` 在最后）。

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— `Cannot find module './runner'`

- [ ] **Step 3: 实现 `arena/types.ts`、`events.ts` 与 `registry.ts`**

`arena/types.ts` 逐字照 spec §4.1 的 `Arena` 接口与相关类型（**不要**加 spec 没有的字段）。`events.ts` 用 **Zod** 定义每个事件的 schema（`z.discriminatedUnion('type', [...])`）并从 schema 导出 TS 类型，避免手写 interface 与 schema 漂移。

`registry.ts` 用一个 `Map<string, BattleHandle>`；`BattleHandle` 提供 `emit(e)`、`eventsSince(id: number): BattleEvent[]`、`subscribe(fn)`、`nextId()`。事件序列**不截断**（`Last-Event-ID` 依赖 id 连续可查，截断会让补发静默丢事件）。

- [ ] **Step 4: 实现 `runner.ts`**

`StartBattleInput = { arena: ArenaId; topic: string; contestants: { slot: number; providerId: string; modelId: string; apiKey: string }[] }`。

流程：`createBattle` → `registerBattle` → 立即 `phase.changed('running')` 并**不 await** 后续工作 → 对每个选手 `Promise.all` 跑 `streamModel`，逐 delta `emit('contestant.delta')`，结束 `emit('contestant.done' | 'contestant.failed')` → 全结束后 `emit('phase.changed','judging')` → 调 `arena.judge(...)` → `emit('phase.changed','scoring')` → `emit('battle.completed', { result })` → `phase.changed('completed')`。

单选手异常只转 `contestant.failed`，不进 catch 到整场。全失败才 `battle.failed`。

- [ ] **Step 5: 跑测试确认通过**

Run: `npm test`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add lib/battle lib/arena/types.ts
git commit -m "feat(battle): 事件契约、内存注册表与对战状态机"
```

---

### Task 5: 软战场与双轨裁判

**Files:**
- Create: `lib/arena/topics.ts`、`lib/arena/soft.ts`、`lib/arena/soft.test.ts`、`lib/judge/runner.ts`、`lib/judge/runner.test.ts`
- Test: `lib/arena/soft.test.ts`、`lib/judge/runner.test.ts`

**Interfaces:**
- Consumes: Task 4 的 `BattleEvent` 与 `lib/arena/types.ts`（`Arena` 接口已定义）；Task 3 的 `streamModel` 与 `generateText`
- Produces:
  - `topics.ts`: `SOFT_TOPICS: { id, title, prompt, kind: 'debate'|'creative'|'roleplay' }[]`（≥6 道）、`CODE_TOPICS: CodeTopic[]`（5~8 道 Python 题，含 `tests: { input: string; expected: string }[]`）
  - `judge/runner.ts`: `parseJudgeOutput(raw: string): ParsedJudgement | null`、`runJudge(opts: JudgeOptions): Promise<JudgeOutcome>`，`JudgeOutcome = { verdicts: Verdict[]; roundsAgreed: boolean; unavailable: boolean; raw?: string }`（**不计算统计置信度**——见 Global Constraints 与 spec §0）
  - `arena/soft.ts`: `softArena: Arena`

- [ ] **Step 1: 写宽松解析的失败测试**

`lib/judge/runner.test.ts`：

```ts
test('纯 JSON 字符串能解析')
test('带 ```json 围栏能解析')
test('JSON 前后夹带解释文字能解析')
test('缺 rationale 字段时用默认理由，不抛错')
test('完全不可解析时返回 null')
test('两轮名次一致 → roundsAgreed 为 true')
test('两轮名次完全相反 → roundsAgreed 为 false')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— `Cannot find module './runner'`

- [ ] **Step 3: 实现 `parseJudgeOutput` 与 `roundsAgreed`**

`parseJudgeOutput` 走 **Zod**（不手写宽松正则）：`judgeSchema = z.object({ verdicts: z.array(z.object({ label: z.string(), logic: z.number(), evidence: z.number(), relevance: z.number(), rationale: z.string().optional() })) })`。解析顺序：整串 `JSON.parse` → 剥 ``` 围栏行 → 取第一个 `{` 到最后一个 `}` 的子串重试；Zod 校验不通过时按字段补默认值（`rationale` 补 `"未提供理由"`、缺失分数补 0）而不抛错；三次都失败返回 `null`。

`roundsAgreed(a, b)` 只做一件事：把两轮名次（label 映射回 slot 后排序）**逐位比较**，完全相同返回 `true`，否则 `false`。**不实现 Kendall tau 或任何统计置信度**——那是评测严谨性，不是本产品的目的（spec §0）。

- [ ] **Step 4: 实现 `runJudge`**

`JudgeOptions = { topic, submissions, judgeModel, signal }`。用 `generateText`（非流式）一次判整场：prompt 里把答案**匿名化为 A/B/C**并要求返回 spec §0 的 verdicts JSON。跑两轮（第二轮交换顺序），把 label 映射回 slot，取两轮分项均值，`roundsAgreed = roundsAgreed(r1, r2)`。

`runJudge` 自身异常或两次都解析失败 → 返回 `{ verdicts: [], confidence: 0, unavailable: true }`。

- [ ] **Step 5: 写 softArena 的失败测试**

`lib/arena/soft.test.ts`：

```ts
test('buildPrompt 带上题目且 system 要求按格式作答')
test('extractCode 对软战场恒返回 null')
test('judge 在裁判不可用时返回空 verdicts 但不抛错')
```

- [ ] **Step 6: 实现 `topics.ts` 与 `soft.ts`**

`SOFT_TOPICS` 写 6 道软题（2 辩论 / 2 创意 / 2 角色扮演），中文，每道 prompt 明确输出格式要求。`soft.ts` 的 `judge` 调 `runJudge`，把 `Verdict.scores` 填 `{ logic, evidence, relevance }`，`rationale` 取裁判原文。`Arena` 接口已由 Task 4 定义，此处只实现接口成员。

- [ ] **Step 7: 跑测试确认通过**

Run: `npm test`
Expected: 全部 PASS

- [ ] **Step 8: 提交**

```bash
git add lib/arena lib/judge
git commit -m "feat(arena): 软战场、题库与双轮位置对调裁判"
```

---

### Task 6: 对战 API 五件套

**Files:**
- Create: `lib/battle/ratelimit.ts`、`app/api/battle/run/route.ts`、`app/api/battle/[id]/stream/route.ts`、`app/api/battle/[id]/vote/route.ts`、`app/api/battle/[id]/cancel/route.ts`、`app/api/battle/[id]/snapshot/route.ts`、`lib/battle/api.test.ts`
- Test: `lib/battle/api.test.ts`

**Interfaces:**
- Consumes: Task 4 的 `startBattle`/`cancelBattle`/`registry`；Task 2 的 repo；Task 5 的 `softArena`
- Produces: 五个 Route Handler；`ratelimit.ts` 导出 `allow(ip: string): boolean`

- [ ] **Step 1: 写路由的失败测试**

`lib/battle/api.test.ts`，用 `node:test` 直接 import handler 函数并构造 `Request`：

```ts
test('run 返回 202 与 battleId，不等待对战结束')
test('run 在同 IP 第 6 次调用时返回 429')
test('vote 第二次同 voterKey 返回 409')
test('snapshot 对未知 id 返回 404')
test('cancel 对未知 id 返回 404')
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— handler 尚不存在

- [ ] **Step 3: 实现 `ratelimit.ts`**

模块级 `Map<string, number[]>` 记时间戳，超过 5/min 返回 false；10 分钟未清理的条目惰性清除。

- [ ] **Step 4: 实现 `run/route.ts`**

`export const runtime = 'nodejs'`。`POST`：从 `x-forwarded-for` 或 `req.headers` 取 IP → `allow(ip)` 否则 429 → 校验 body（`arena` 必填、`contestants` 至少 2 个、每个含 `providerId`/`modelId`/`apiKey`）→ 调 `startBattle` → 返回 `202 { battleId }`。**Key 只在这一次调用栈里出现，不写库、不进日志。**

- [ ] **Step 5: 实现 `stream/route.ts`**

`GET` 返回 `new Response(readableStream, { headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' } })`。

**用 SSE 协议自带的断线续传，不手写补发队列**：`registry` 给每条事件分配**该 battle 内单调递增**的整数 id，`emit` 时写成 SSE 的 `id: <n>` 行再写 `data: <json>`；连接建立时读请求头 `Last-Event-ID`（缺省视为 -1），只补发 id 大于它的历史事件，然后接 `subscribe` 继续推。battle 终态（completed/failed/cancelled）时 `return` 关闭流。`ctx.params` 必须 `await`。

`emit` 侧只需在内存里保留每 battle 的事件序列供 `Last-Event-ID` 查找，**不设"最近 500 条"这种截断**（截断会让大 id 之后的补发静默丢事件）。dev 模式 HMR 可能清空内存，此时 `Last-Event-ID` 查不到就当新连接从当前时刻开始，前端有 `/snapshot` 兜底。

- [ ] **Step 6: 实现 `vote` / `cancel` / `snapshot`**

`vote`：`POST { contestantId }`，从 body 收 `voterKey`（前端生成），`recordVote` 成功返 200，UNIQUE 冲突返 409；每次成功投票后 `emit('vote.tally')`。`cancel`：调 `cancelBattle`，成功 200、未知 id 404。`snapshot`：`GET` 返回 `getSnapshot(id)`，未找到 404。

- [ ] **Step 7: 跑测试确认通过**

Run: `npm test && npx tsc --noEmit`
Expected: 全 PASS，tsc 无错误

- [ ] **Step 8: 提交**

```bash
git add app/api/battle lib/battle
git commit -m "feat(api): 对战 run/stream/vote/cancel/snapshot 五件套与限流"
```

---

### Task 7: 擂台首页（选题、选模型、预检）

**Files:**
- Create: `app/page.tsx`、`components/arena/arena-setup.tsx`
- Test: 手动验收（本任务无自动化测试，理由：纯客户端交互组件，验收靠 Task 12 的浏览器端到端）

**Interfaces:**
- Consumes: `app/api/providers/models`、`app/api/providers/ping`、`app/api/battle/run`、`lib/providers/registry`（需经一个只读 server 组件把 `PROVIDERS` 传给 client 组件）
- Produces: 首页可完成「选题 → 选 ≥2 选手 → 预检 → 开打」，成功后 `router.push('/battle/' + battleId)`

- [ ] **Step 1: server 组件壳 `app/page.tsx`**

读 `PROVIDERS` 与 `SOFT_TOPICS`/`CODE_TOPICS`，把数据作为 props 传给 `<ArenaSetup providers={...} softTopics={...} codeTopics={...} />`，并渲染 `<h1>melee · AI 大乱斗</h1>`。

- [ ] **Step 2: 客户端组件 `arena-setup.tsx`**

`'use client'`。四块：赛道切换（软/硬）→ 题目下拉（软题库或 Python 题）→ 选手列表（每行一个 provider 下拉 + 模型输入，带"获取模型列表"按钮调 `/api/providers/models` 自动填入）→ 操作区。

- [ ] **Step 3: 实现阵容预设与预检**

阵容存 `localStorage` 的 `melee.lineup`，页面挂载时读入。点"预检"并发 `POST /api/providers/ping`，把每个选手标绿/标红并显示后端返回的 `message`；标红的选手点"开打"时禁用。

- [ ] **Step 4: 实现开打**

点"开打"收集 `{ arena, topic, contestants }` → `POST /api/battle/run` → 429 时提示限流 → 成功则 `router.push(\`/battle/${battleId}\`)`。

- [ ] **Step 5: 验证**

Run: `npm run dev` 后用内置浏览器打开 `http://localhost:3000`
Expected: 能看到赛道切换、题目下拉、至少两个 provider 的选手行；点"获取模型列表"能拿到 Ollama 的本地模型列表或明确提示不可达；点"预检"给出每个选手的可达性。

- [ ] **Step 6: 提交**

```bash
git add app/page.tsx components/arena
git commit -m "feat(ui): 擂台首页选题、选阵容、预检与开打"
```

---

### Task 8: 观战页（流式渲染与观感）

**Files:**
- Create: `app/battle/[id]/page.tsx`、`lib/client/battle-store.ts`、`components/arena/battle-shell.tsx`、`components/arena/contestant-card.tsx`、`components/arena/status-bar.tsx`
- Test: 手动 + Task 12 浏览器验收

**Interfaces:**
- Consumes: `app/api/battle/[id]/stream`、`/snapshot`、`/vote`、`/cancel`
- Produces: `battle-store.ts` 导出 Zustand store `useBattleStore`（`state: { phase, startedAt, contestants: Record<slot, ContestantView>, voteTally, summary }`，动作 `apply(event)` / `hydrate(snapshot)`）；`ContestantView = { slot, providerId, modelId, text, status, latencyMs, finishedAt, isFirstFinisher }`

- [ ] **Step 1: 实现 `battle-store.ts`（Zustand，不手搓 store）**

`apply(event)` 按 slot 累积 delta；每次改动置 dirty flag，用一个模块级 `requestAnimationFrame` 调度器批量提交——**每帧最多一次 `set()`**（这是本任务的核心性能约束，用 Zustand 的 `subscribeWithSelector` + 合并写实现，不自己实现响应式系统）。`hydrate(snapshot)` 用服务端快照整体覆盖。终态事件到达后取消调度。

- [ ] **Step 2: 实现 `contestant-card.tsx`**

四态：`streaming`（打字机光标 + 流式文本）、`ok`（正常）、`failed`（红色横幅 + 后端给的**可读原因**，不显示 Key）、`timeout`（黄色横幅 + "超时未完成"）。**先完成的卡片加一个「先手」角标**（`isFirstFinisher`），制造节奏感。文本用 `<pre className="whitespace-pre-wrap break-words">`，长输出虚拟化不做（第一期接受）。

- [ ] **Step 3: 实现 `status-bar.tsx`（观感主体）**

常驻顶部一行三段：当前 `phase`（中文词：准备中 / 开打中 / 裁判中 / 计分中 / 已结束 / 已取消）、**已完成选手数 / 总数**、**本场已用时长**（从 `startedAt` 起每秒刷新一次，用独立的轻量 `useEffect` + `setInterval`，不要放进流式 store 的渲染路径）。本场无人完成时时长仍要走。

- [ ] **Step 4: 实现 `battle-shell.tsx`**

`'use client'`。挂载时 `new EventSource(/api/battle/${id}/stream)`，`onmessage` 里 `JSON.parse` 后 `store.apply`。`onerror` 时关流、2s 后重连（**浏览器会自动带 `Last-Event-ID`，服务端据此补发**，见 Task 6；仍不完整时再 `GET /snapshot` 兜底），连续失败 3 次后显示"连接中断，请刷新"。布局：顶部 `<StatusBar />`，下方选手卡片按 `slot` 网格排列，右上"取消对战"调 `/cancel`。终态时渲染 `<Settlement />`（Task 11 之前先渲染原始 `battle.completed` 载荷）。

- [ ] **Step 5: 实现页面壳 `app/battle/[id]/page.tsx`**

server component，`const { id } = await params`（**必须 await**），服务端先 `getSnapshot(id)` 并 `hydrate` 作为初始值传给 `BattleShell`。

- [ ] **Step 6: 验证**

Run: `npm run dev`，首页用 Ollama（若未装则用 Task 3 的 fake server）开一场软战场，用内置浏览器打开 `/battle/<id>`
Expected: 各选手卡片**逐段增长**（不是整块一次出现）；赛况条的三段数字都在动；先完成的卡片出现「先手」角标；失败态有可读原因；关掉页面再打开能看到已完成内容。

- [ ] **Step 7: 提交**

```bash
git add app/battle components/arena lib/client package.json package-lock.json
git commit -m "feat(ui): 观战页 Zustand 流式渲染、赛况条与先手标记"
```

---

### Task 9: 沙箱层（Pyodide Worker）

**Files:**
- Create: `lib/sandbox/backend.ts`、`lib/sandbox/pyodide-worker.ts`、`workers/pyodide-worker.mjs`、`lib/sandbox/pyodide-worker.test.ts`
- Test: `lib/sandbox/pyodide-worker.test.ts`

**Interfaces:**
- Consumes: `pyodide` npm 包
- Produces:
  - `backend.ts`: `SandboxBackend` 接口、`getBackend(): SandboxBackend`
  - `pyodide-worker.ts`: `runInPyodide(input: { code: string; tests: TestCase[]; timeLimitMs?: number }): Promise<SandboxResult>`、`SandboxResult = { status: 'ok'|'timeout'|'error'; passed: number; total: number; timeMs: number; stdoutTail: string; errorMessage?: string }`

- [ ] **Step 1: 装并确认 pyodide 可用**

Run: `npm ls pyodide`
Expected: 已安装（Task 1 Step 2 装的）。若当时失败，此处重试 `npm i pyodide`。

- [ ] **Step 2: 写 Worker 入口 `workers/pyodide-worker.mjs`**

纯 ESM `.mjs`（不经 Next 打包）。内容：`import { loadPyodide } from 'pyodide'` → 收到 `{ code }` 消息后 `loadPyodide()` → **删掉互操作入口**（`delete globalThis.js; delete pyodide.globals.get('js')` 等，注释不写，靠测试固定）→ `pyodide.setStdout(...)` / `setStderr(...)` 采集输出（上限 64KB，超限抛错终止）→ `runPythonAsync` 执行用户代码 → 把结果 postMessage 回主线程。**Worker 内不得读取 `process.env`、不得 require 任何项目模块。**

- [ ] **Step 3: 写主线程封装 `pyodide-worker.ts`**

`new Worker(path.join(process.cwd(), 'workers', 'pyodide-worker.mjs'), { workerData: {} })`，每个提交**新建** Worker，`setTimeout` 到 `timeLimitMs`（默认 **15000**）就 `worker.terminate()` 并返回 `{ status: 'timeout', passed: 0, total: tests.length }`。收到结果或异常后**立即 terminate**。`isolationLevel` 固定 `'wasm-worker'`。

- [ ] **Step 4: 写三个安全测试**

`lib/sandbox/pyodide-worker.test.ts`：

```ts
test('正常代码通过全部用例', ...)                     // 断言 passed === total
test('死循环被 terminate 截断，返回 timeout', ...)      // 断言 status === 'timeout' 且耗时 < 20s
test('代码读 os.environ 拿不到任何值', ...)             // 断言输出含 KeyError 且不含任何本机变量值
```

第三个测试是 Global Constraints 的直接断言，**不可删**。

- [ ] **Step 5: 实现 `backend.ts` 接口与 `getBackend()`**

`getBackend()` 读 `process.env.SANDBOX_BACKEND ?? 'pyodide'`；`'judge0'` 时动态 import `./judge0.ts`，**本任务不实现 judge0.ts**，该分支先抛 `new Error('judge0 backend 尚未实现')`，由 Task 10 处理。

- [ ] **Step 6: 跑测试确认通过**

Run: `npm test`
Expected: 全部 PASS（首次运行 Pyodide 加载需数秒）

- [ ] **Step 7: 提交**

```bash
git add lib/sandbox workers package.json package-lock.json
git commit -m "feat(sandbox): Pyodide WASM 沙箱（独立 Worker、禁互操作、terminate 硬止损）"
```

---

### Task 10: 硬战场与沙箱后端接线

**Files:**
- Create: `lib/arena/hard-code.ts`、`lib/arena/hard-code.test.ts`、`lib/sandbox/judge0.ts`
- Test: `lib/arena/hard-code.test.ts`

**Interfaces:**
- Consumes: Task 9 的 `getBackend()`；Task 5 的 `topics.ts`（`CODE_TOPICS`）
- Produces: `hardCodeArena: Arena`；`lib/sandbox/judge0.ts` 导出 `judge0Backend: SandboxBackend`

- [ ] **Step 1: 写代码块抽取的失败测试**

`lib/arena/hard-code.test.ts`：

```ts
test('取第一个代码块', ...)                       // 两个块时断言只取第一个
test('无代码块时返回 null', ...)                   // 关键：Review Focus #2
test('带语言标识的围栏也能取（```python）', ...)
test('围栏未闭合时仍尽力取出内容', ...)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `npm test`
Expected: FAIL —— `Cannot find module './hard-code'`

- [ ] **Step 3: 实现 `hard-code.ts` 的 `extractCode`**

正则取第一个 ``` 围栏块（允许带语言标识、允许未闭合兜底到结尾）。**没有代码块时，`judge` 直接写 `sandbox_results` 一行 `status='no_code'`、`passed=0`，并在 verdict 的 `rationale` 里写"未产出可执行代码"**——绝不把整段文本当代码提交。

- [ ] **Step 4: 实现 `hardCodeArena`**

`buildPrompt` 把 `CodeTopic.prompt` 和"只输出一个 Python 代码块"的要求写进 system。`judge` 遍历 submissions：无代码块直接判 0；有则调 `getBackend().run(...)`（timeLimitMs 传 15000）。**沙箱抛错/不可用时返回 `{ status:'unavailable' }`，写 `isolation_level='none'`、`degraded=1`，并让上层出横幅——不得写任何通过数。**

- [ ] **Step 5: 实现 `judge0.ts`**

`POST ${JUDGE0_BASE_URL}/submissions?base64_encoded=false&wait=false`，轮询 `GET /submissions/{token}` 直至 `status.id > 2`（完成），映射 `compile_output`/`stderr`/`time`/`memory`/`stdout`。限额按 Global Constraints。凭据只从 `process.env.JUDGE0_AUTHN_HEADER` 读。

- [ ] **Step 6: 跑测试确认通过**

Run: `npm test && npx tsc --noEmit`
Expected: 全部 PASS，tsc 无错误

- [ ] **Step 7: 手动验证硬战场**

Run: `npm run dev`，用内置浏览器开一场硬战场
Expected: 战报里每个选手显示 `passed/total`；Pyodide 后端下标注「WASM 语言级沙箱执行，非 OS 级隔离」。

- [ ] **Step 8: 提交**

```bash
git add lib/arena lib/sandbox
git commit -m "feat(arena): 硬战场代码抽取、Pyodide 判定与 Judge0 可选后端"
```

---

### Task 11: 投票与战报

**Files:**
- Create: `components/arena/vote-bar.tsx`、`components/arena/settlement.tsx`
- Test: 手动 + Task 12 浏览器验收

**Interfaces:**
- Consumes: `battle.completed` 事件载荷、`sandbox_results.isolation_level`、`judgements`
- Produces: 两个 client 组件

- [ ] **Step 1: 实现 `vote-bar.tsx`**

每个选手一行：显示票数（来自 `vote.tally`）、一个投票按钮。`voterKey` 在首次挂载时用 `crypto.randomUUID()` 存 `localStorage`。点投票调 `POST /api/battle/${id}/vote`，409 时提示"你已投过票"。

- [ ] **Step 2: 实现 `settlement.tsx`**

**第一行是一句话总结**（观赏性主体，自己写模板生成、不引 NLP 库）：如「deepseek 以 2:0 通杀，本场无人翻车」「三选一全员超时，本场无人生还」。句式至少覆盖：全胜/无翻车、有翻车、全部失败、单方碾压、势均力敌（票差 ≤1）。

其下三段并列：
1. **人类票数**：条形 + 票数 + `position_index`；
2. **裁判分**：logic/evidence/relevance 三项分 + 中文理由（**不与票数合成总分**）+ 一行「AI 裁判与观众票数：一致 / 不一致」（用 `judgements` 的两轮 `roundsAgreed` 与 `votes` 比对得出，**不算统计量**）；
3. **硬战场**：passed/total + 耗时 + 隔离级别标注。

**翻车榜**：底部单列最惨的若干瞬间——最短输出、最后完成、超时、空输出，各一行文字描述。这是观赏性的落点之一。

降级横幅三选一：「裁判不可用，本场仅人类投票」/「沙箱不可用，本次未真实执行」/「无 OS 级隔离：本场仅限可信环境使用」。每个失败选手也要有独立一行说明。

- [ ] **Step 3: 在 `battle-shell.tsx` 接入**

把 Task 8 Step 3 里的"终态渲染原始载荷"替换为 `<Settlement battle={snapshot} />`。

- [ ] **Step 4: 验证**

Run: `npm run dev`，用内置浏览器打一场软战场
Expected: 投票后票数实时更新；重复投票提示已投过；战报两栏并列且**没有合成总分**；裁判分项与理由可读。

- [ ] **Step 5: 提交**

```bash
git add components/arena
git commit -m "feat(ui): 投票条与双轨战报（票数/裁判分并列 + 降级横幅）"
```

---

### Task 12: 端到端浏览器验收与 README

**Files:**
- Create: `README.md`、`.env.example`
- Test: 内置浏览器端到端（本任务的"测试"就是验收动作本身）

**Interfaces:**
- Consumes: 全部前置任务
- Produces: 可复现的启动说明 + 验收截图

- [ ] **Step 1: 写 `.env.example`**

```
SANDBOX_BACKEND=pyodide
JUDGE0_BASE_URL=
JUDGE0_AUTHN_HEADER=
MELEE_DB=data/melee.db
```

- [ ] **Step 2: 写 `README.md`**

包含：项目一句话定位、快速启动步骤（`npm install && npm run dev`，说明默认 Pyodide 无需 Docker）、选手池与"用户自带 Key"的设计说明、**沙箱隔离级别与上线闸门的明确警示**（非 OS 级隔离不得公网开放）、目录结构、测试命令。

- [ ] **Step 3: 全量验证**

Run: `npm test && npx tsc --noEmit && npm run build`
Expected: 测试全绿、tsc 无错误、build 成功

- [ ] **Step 4: 内置浏览器跑两条端到端**

用内置浏览器：① 软战场一场（≥2 选手）→ 观察流式 → 投票 → 看战报双栏；② 硬战场一场 → 观察沙箱结果与隔离标注。把 `battle/[id]` 页面截图作为证据。

硬战场若无可用本地模型，**用 Task 3 的 fake server 作为至少一个选手**完成端到端验证，并在 README 注明该验证方式。

- [ ] **Step 5: 验证 Review Focus 六条逐条过一遍**

逐条手动确认：单 Key 失败仍能打完整场 / 无代码块判 0 并标注 / 裁判畸形输出降级不 500 / 沙箱读不到 env / 死循环被 terminate / 断线重连能看到已完成内容。任一不过就回到对应 task 修。

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "docs: README、.env.example 与端到端验收"
```

---

### Task 13: 里程碑推送

**Files:** 无新文件

- [ ] **Step 1: 确认工作区干净**

Run: `git status --short`
Expected: 无输出

- [ ] **Step 2: 打里程碑 tag**

```bash
git tag -a v0.1-phase1 -m "第一期 MVP：软战场双轨裁判 + 硬战场 Pyodide 沙箱"
```

- [ ] **Step 3: 推送（仅此一次）**

```bash
git push origin main --tags
```

- [ ] **Step 4: 在 commit message 或 README 记录后续推送节奏**

写明：只在里程碑推送（骨架 / 软战场 / 硬战场 / 收尾），日常改动不推。

---

## Self-Review 结果

**1. Spec 覆盖**：§1.1 四项目标 → Task 4~8（观战闭环）、Task 5（双轨裁判）、Task 9~10（沙箱执行）、Task 5+11（战报与 position_index 落库）。§2 依赖锚点 → Task 1。§4 全部组件 → Task 2~11 一一对应。§5 数据模型 → Task 2。§6 安全边界 → Key 流在 Task 3/6/7，沙箱在 Task 9，上线闸门在 Task 12 README。§7 降级矩阵 → Task 5/9/10/11。§8 测试 → 各 task 自带 + Task 12 端到端。§9 本地运行 → Task 1/12。无遗漏。

**2. Step 扫描**：已删掉所有"写适当的测试""处理边界情况"这类空步骤。唯一非确定步骤（AI SDK v7 的 `usage` 字段名）已写成"以 `tsc` 通过为准"的明确判据；Node 测试解析器偏离也已写成带触发条件和记录的确定动作。

**3. 类型一致性**：`Submission`/`Verdict`/`Arena` 由 Task 4 创建（在 `lib/arena/types.ts`），Task 5 只实现 `softArena`、Task 10 只实现 `hardCodeArena`——顺序已对齐，runner 不再引用尚未存在的类型。

**4. Review Focus**：六条分别落在 Task 4（#1 单选手失败、#7 事件 id 单调）、Task 10（#2 无代码块）、Task 5（#3 裁判畸形）、Task 9（#4 读不到 env）、Task 9（#5 死循环 terminate）、Task 8（#6 断线重连续传）。

**6. 产品定位复核（spec §0）**：全计划只有 Task 9/10（沙箱）和 Task 5（裁判）涉及"验证"性质的严谨性投入，其余 11 个 task 的验收标准都是**观感类**的（流式是否逐段增长、先手角标是否出现、赛况条是否在动、战报一句话是否好读、翻车榜是否有内容）。这与"观赏性优先"一致。

**5. 比例**：13 个 task、约 70 个 step，spec 约 400 行。plan 中代码块只出现接口签名与测试断言，无函数体转录。