# AI 大乱斗（AI Battle Arena）第一期设计文档

- 日期：2026-10-09
- 状态：待评审
- 范围：第一期（MVP 最小闭环）

## 0. 产品定位：观赏性优先

**这个项目的产品目的是观赏性与情绪价值，不是评测能力。** 用户要看的是"AI 自己对抗时有多好看"，评分体系、裁判、沙箱都只是让赛制可信的手段，不是产品目的本身。

这条定位对所有设计取舍有约束力：

- **观感预算高于工程严谨度**。同等效果下，优先选"看起来更爽"的方案。流式并排的节奏感、先手标记、翻车的高亮，比多一个精确指标更值钱。
- **不为了指标好看牺牲戏剧性**。不隐藏模型翻车——超时、限流、鉴权失败、输出为空、和稀泥的一律高亮，翻车本身就是观赏内容。
- **战报要能一句话讲完**。"deepseek 以 2:0 通杀、本场无人翻车"比三个分项分数更有传播力；分项分数是补充，不是主角。
- **裁判是解说员，不是考官**。它的价值在于给出可读的点评与站队，而不是提供可信的绝对分数。
- 评测严谨度只保留一条底线：**任何没真实执行 / 没真实发生的事，战报必须明说**（沙箱不可用、裁判不可用、模型失败）。这条不以观赏性为理由放宽。

## 1. 背景与目标

做一个"擂台"产品：多个不同的大模型作为参赛选手，在同一个题目上同场竞技，人类观众实时观战并投票，赛后产出结构化战报。核心价值是**把各家模型的真实水平、风格差异和翻车现场摆到台面上**。

### 1.1 第一期目标（按优先级）

1. 能真的开一场对战，并实时看到 N 个模型的输出并排刷新。
2. 软战场（辩论/创意）双轨判定：人类投票 + LLM 裁判技术分，两者分开存、分开显示。
3. 硬战场（代码）真实沙箱执行判定：默认 Pyodide WASM（语言级隔离），Judge0 留作可选的 OS 级隔离后端。
4. 赛后出可读战报；每票、每份裁判分都带 `position_index`，为二期偏好学习埋数据。

### 1.2 非目标（第一期明确不做）

- 账号体系与 API Key 加密保险库（二期，属高敏感安全面，单独一份 spec）。
- ELO / Bradley-Terry 战力建模（只存数据不算分）。
- 视频导出、长图战报、移动端原生。
- 沙箱多语言（只开 Python 3）。
- 投票真防刷（IP + localStorage 弱去重）。
- 排行榜跨设备持久化（排行榜从本地 SQLite 读，够用）。
- 装饰性动画、多主题、转场特效。

### 1.3 验收标准

- 本地 `npm install && npm run dev` 后（默认 Pyodide 沙箱，无需 Docker），选 ≥2 个模型 + 1 个题目，能在网页上看到流式并排输出。
- 单个模型失败不影响其余选手；全部失败时有可读的原因展示。
- 硬战场能真实执行模型产出的 Python 代码并给出通过数 / 耗时 / 内存。
- 投票写入 SQLite，裁判分写入 SQLite，均带 `position_index`。
- `npm test` 全绿（沙箱外部依赖用例可跳过）。

## 2. 技术选型与依赖锚点

| 项 | 选型 | 版本下限 | 备注 |
|---|---|---|---|
| 框架 | Next.js（App Router） | `>=16.3.6` | 16.3.3/16.3.6 修过多个 critical RCE（`next/og` ImageResponse、Image Optimization AVIF、Windows 宿主混合路由）。本期**不用** `next/og`、不用 Image Optimization |
| 运行时 | Node.js | `>=24.14` | 实测本机 24.14.0 |
| 模型调用 | Vercel AI SDK | `7.x`（`ai` + `@ai-sdk/openai` / `@ai-sdk/anthropic` / `@ai-sdk/google`） | 44 个官方 provider；流式、计量、取消开箱可用 |
| 数据库 | `node:sqlite`（Node 内置） | Node `>=22.5`，RC 级 | 零原生编译、Windows 零摩擦。**必须**经 `lib/db/client.ts` 薄封装隔离，二期换 Postgres 只改一个文件 |
| 沙箱（默认） | Pyodide WASM，跑在专用 `worker_threads` Worker 内 | npm `pyodide`（实测最新 314.0.7，MPL-2.0，周下载 ~90 万） | **语言级沙箱，非 OS 级隔离**；有已知逃逸事故，见 §6.2 |
| 沙箱（可选高强度） | Judge0 CE 自托管 | `judge0/judge0-ce` 最新 tag | 仅当 `SANDBOX_BACKEND=judge0` 启用；需自托管 Docker（官方公共实例已转付费） |
| 测试 | Node 内置 `node:test` | — | 纯逻辑层零额外框架依赖 |

### 2.1 已核查的环境事实（2026-10-09 实测）

- Node 24.14.0 / npm 11.9.0 / git 2.55.0：通过。
- Docker CLI 29.8.0 + Compose v5.5.1 已安装，**但 Linux 引擎未运行**（`dockerDesktopLinuxEngine` 管道连不上，本地 0 容器 0 镜像）。→ 默认后端是 Pyodide，**Docker 不再是开工阻塞项**；仅当切到 `SANDBOX_BACKEND=judge0` 时才需要启动 Docker Desktop 并预拉镜像。
- 磁盘 C 余 692GB：通过。

### 2.2 关键实现约束（易踩坑，提前固化）

1. **OpenAI 兼容端点**：`@ai-sdk/openai` 的 `openai()` 默认走 Responses API。接 DeepSeek / GLM / Kimi / 通义 / 硅基流动必须显式 `createOpenAI({ baseURL })` 或 `createOpenAICompatible(...)` 并确保走 chat completions，否则首批第三方模型直接 400。
2. **Next.js 16 动态路由**：`ctx.params` 是 `Promise`，必须 `await`。
3. **`node:sqlite` 不能在 Edge runtime / middleware 用**。所有 Route Handler 显式 `export const runtime = 'nodejs'`。
4. **长任务与 SSE**：只能自托管 Node 运行，不上 serverless。
5. **网络可达性**：国内直连 OpenAI / Anthropic / Gemini 基本不可用。第一期默认选手池为「国内可直连 + 本地」：Ollama（本地）、DeepSeek、GLM、Kimi、通义、硅基流动；海外模型标记为"需代理"的可选项，UI 显示可达性。
6. **Pyodide 沙箱硬性约束（每一条都对应一个真实逃逸事故，不许省）**：
   - **禁止在宿主进程内 `loadPyodide()`** —— n8n 的 CVE-2025-68668 / CVE-2026-0863 根因就是进程内 Pyodide 塌了信任边界；
   - **每个提交新建一个 Worker，结束后 `terminate()`**，实例不复用；
   - **Worker 内不得存在任何凭证、数据库连接、宿主回调**（不传 env、不传 DB、不传 Next 的任何上下文对象）；
   - **必须禁用 Python 侧的 `js` 模块与 `pyodide.globals` 互操作入口** —— Cohere Terrarium 的 CVE-2026-5752（CVSS 9.3）根因就是 Emscripten 导出没清干净；
   - Pyodide 的 wasm 运行时体积大（数十 MB），**只装在服务端，不下发浏览器**；`loadPyodide` 的 `indexURL` 指向本地 `node_modules/pyodide/`，**不走 CDN**（供应链可控）。

## 3. 架构总览

```
浏览器（擂台页）
  │  ① POST /api/battle/run            { arena, topic, contestants[{provider,model,apiKey}] }
  │  ② GET  /api/battle/:id/stream     SSE（multiplex 事件流）
  │  ③ POST /api/battle/:id/vote
  │  ④ POST /api/battle/:id/cancel
  │  ⑤ GET  /api/battle/:id/snapshot    断线重连后拉快照
  │  ⑥ POST /api/providers/ping         开打前预检
  ▼
Next.js Route Handlers（runtime=nodejs）
  ├── battle/runner.ts      编排：并行开流、超时、取消、写库
  ├── arena/*                赛场插件（soft / hardCode）
  ├── providers/*            provider 工厂 + 选手池注册表
  ├── judge/runner.ts        LLM 裁判（一次判整场 + 位置对调两轮）
  └── sandbox/backend.ts        后端选择（SANDBOX_BACKEND）
      ├── pyodide-worker.ts    默认：WASM 语言级沙箱
      └── judge0.ts            可选：OS 级隔离
  ▼
SQLite（node:sqlite，WAL）        沙箱运行时（Pyodide Worker / Judge0 CE 容器）
```

部署约束：自托管 Node 长进程（承载 SSE 与 Worker）。仅当启用 Judge0 后端时，Docker 与它同机或同内网。公开上线另需域名备案（上线锚点，非开发锚点）。

## 4. 组件设计

每个模块只回答三个问题：做什么、怎么用、依赖什么。模块之间只通过明确接口通信，可独立测试。

### 4.1 `lib/arena/types.ts` — 赛场抽象

```ts
export type ArenaId = 'soft' | 'hard-code'

export interface ContestantInput {
  slot: number            // 稳定座位号，全场不变，= contestants.slot
  providerId: string
  modelId: string
  apiKey: string          // 仅存在于本次对战的服务端上下文
}

export interface Submission {
  contestant: ContestantInput
  text: string            // 完整输出
  code: string | null     // 抽取出的代码块（软战场为 null）
  usage: { prompt: number; completion: number } | null
  latencyMs: number
  status: 'ok' | 'timeout' | 'rate_limited' | 'auth_failed' | 'provider_error' | 'empty'
  errorMessage?: string
}

export interface Verdict {
  contestantSlot: number
  scores: Record<string, number>   // 软战场：logic/evidence/relevance；硬战场：pass_rate/speed
  passed: number
  total: number
  rationale: string
}

export interface JudgeContext {
  topic: string
  judgeModelId: string
  judgeApiKey: string
}

export interface Arena {
  id: ArenaId
  buildPrompt(topic: string): { system?: string; user: string }
  extractCode(text: string): string | null
  judge(subs: Submission[], ctx: JudgeContext): Promise<Verdict[]>
}
```

### 4.2 `lib/arena/soft.ts` — 软战场

- 题库驱动：预置题库（辩论题/创意题/角色扮演）+ 用户自定义题目（本地长度与敏感词校验）。
- 判定双轨，**分开存储分开显示，不合成总分**：人类投票（`votes` 表）+ 裁判技术分（`judgements` 表）。
- 裁判策略：**一次调用判整场，位置对调跑两轮**（第二轮交换选手顺序后重新判）。第二轮的作用是压位置偏见；两轮名次不一致时，战报上给一个直观的「AI 裁判与观众票数是否一致」标记，**不计算也不展示统计置信度**（评测严谨性不是本产品的目的，见 §0）。
- 裁判模型独立于参赛模型（默认用一个便宜快的模型），裁判输出用 Zod schema 解析并带容错回退（剥围栏、夹带文本、缺字段填默认），彻底解析失败时降级为"裁判不可用，仅人类投票"并在战报标注。

### 4.3 `lib/arena/hard-code.ts` — 硬战场

- 题目结构：`{ id, title, prompt, tests: [{ input, expected }] , timeLimitMs }`，第一期内置 5~8 道 Python 题。
- 只取模型输出中**第一个** ``` 代码块；没有代码块 → `passed=0` 并标注"未产出可执行代码"，不做任何猜测性抽取。
- 判定委托 `lib/sandbox/`（由 `SANDBOX_BACKEND` 选择 Pyodide 或 Judge0 实现）。

### 4.4 `lib/sandbox/` — 可插拔沙箱层

沙箱做成接口 + 两个实现，用 `SANDBOX_BACKEND` 环境变量切换（默认 `pyodide`）。这样"不想用容器"是默认状态，同时不把 OS 级隔离能力永久删掉。

```ts
export interface SandboxBackend {
  id: 'pyodide' | 'judge0'
  isolationLevel: 'wasm-worker' | 'os-container'
  run(input: { code: string; tests: TestCase[]; timeLimitMs: number }): Promise<SandboxResult>
}
```

**`pyodide-worker.ts`（默认）**

- 在专用 `worker_threads` Worker 内 `loadPyodide()`，`indexURL` 指向本地 `node_modules/pyodide/`，不走 CDN；
- 每提交新建 Worker、跑完 `terminate()`，实例不复用；
- 禁掉 Python 侧 `js` 模块与 `pyodide.globals` 互操作入口；
- 时间限制走主线程 `setTimeout` → `worker.terminate()`，这是硬止损；默认 15s（WASM 比原生慢，时间线比 Judge0 的 5s 宽）；
- 内存：限制 stdout 采集长度，异常膨胀立即终止 Worker；
- **诚实标注**：战报里 `isolation_level` 写 `wasm-worker`，UI 显示「WASM 语言级沙箱执行，非 OS 级隔离」。

**`judge0.ts`（可选，`SANDBOX_BACKEND=judge0`）**

- HTTP 调自托管 Judge0 CE（`JUDGE0_BASE_URL` + `JUDGE0_AUTHN_HEADER`，只存服务端环境变量，不下发浏览器）；
- 固定边界：`language_id = Python 3`、`wall time 5s`、`memory 256MB`、`max_processes 32`、`max_file_size 1MB`、禁网、每次提交一次性容器。

**共同条款**

- 沙箱输入只有「题目 + 模型产出的代码」，环境变量零注入；
- 只取模型输出中**第一个** ``` 代码块（由 `arena/hard-code.ts` 负责），没有代码块直接判 0 分并标注"未产出可执行代码"，不做猜测性抽取；
- **降级（诚实）**：两个后端都不可用时，硬战场降级为"仅生成 + 不真实执行"，战报显式横幅「沙箱不可用，本次未真实执行」，并把 `isolation_level` 记为 `none`；绝不把未执行的代码写成通过。

### 4.5 `lib/providers/registry.ts` — 选手池

- 一个 `PROVIDERS` 注册表：`{ id, label, kind: 'openai-compatible' | 'anthropic' | 'google' | 'ollama', baseURL?, defaultModelId, region: 'cn' | 'global', needsProxy: boolean }`。
- 第一期默认选手池（国内可直连）：Ollama 本地、DeepSeek、GLM、Kimi、通义、硅基流动；海外（OpenAI/Anthropic/Gemini）标注 `needsProxy`，UI 显示为"需代理"。
- `createModel(providerId, apiKey)` 工厂：openai-compatible 走 `createOpenAI({ baseURL, apiKey })`，anthropic/google 走各自 provider 包。

### 4.6 `lib/battle/runner.ts` — 编排

职责：建对战、并行开所有选手的流、逐选手超时（默认 120s 可配）、单选手失败隔离、取消、写库、驱动 phase 流转。

phase 状态机：

```
pending → running → judging → scoring → completed
                    ↓           ↓
                 cancelled    failed
```

永不因单个选手失败而中断整场；全失败才 `failed` 并给出可读原因。

### 4.7 SSE 事件契约（`lib/battle/events.ts`，前后端共享类型）

| 事件 | 载荷 | 说明 |
|---|---|---|
| `phase.changed` | `{ phase }` | 阶段推进 |
| `contestant.started` | `{ slot, providerId, modelId }` | 选手开流 |
| `contestant.delta` | `{ slot, text }` | 增量文本 |
| `contestant.done` | `{ slot, usage, latencyMs }` | 完成 |
| `contestant.failed` | `{ slot, status, message }` | 失败（可读文案，不含 Key） |
| `judge.delta` / `judge.done` | `{ text }` / `{ verdicts, available }` | 裁判；不传统计置信度（见 §0） |
| `sandbox.result` | `{ slot, passed, total, timeMs, memoryKb }` | 硬战场 |
| `vote.tally` | `{ tally }` | 随 phase 事件定时携带（5s 节流） |
| `battle.completed` | `{ result }` | 终局 + 战报摘要 |
| `battle.failed` | `{ reason }` | 整场失败 |

### 4.8.1 视觉语言（前端硬约束）

产品目的是观赏性，界面就是观赏内容的一部分。以下几条是对"好看"的**具体**要求，不是模糊的审美偏好：

- **建立记分牌隐喻**。整个产品用"体育记分牌 / 赛事转播"的视觉语言：赛道有主色、选手有番号与色标、比分与进度以计分牌的方式呈现。这条隐喻本身就是差异化——市面上的 AI 产品清一色聊天框 + 紫蓝渐变 + 玻璃拟态，我们不做那个。
- **禁止 AI 味套路**：不用紫蓝渐变堆叠、不用玻璃拟态、不用满屏 emoji 当图标、不用 "✨ / 🚀 / AI-powered" 式标语、不用超大字号全大写英文标题、不用无意义的圆角+阴影堆叠。
- **要有自己的识别度**：定死一套色板（软战场 / 硬战场各一个主色 + 中性底色 + 语义色：成功绿、失败红、超时黄、降级琥珀），全站只用这一套。色值写进 `components/arena/theme.ts`，不散落在各处。
- **符合人的使用习惯**：默认状态就要能直接开打，不要强迫用户先配置一堆；关键动作（选赛道、预检、开打）在首屏且位置稳定；任何时候都能看出"现在发生了什么、下一步能做什么"。
- **少即是好**：能不点的按钮就不点，能自动补的（预检）就自动补，不要让用户填表填出 onerous 的表单。
- **多点可视化**：能用图形表达的不要用文字列表——
  - 票数用横向条形，同屏可比的项（各分项得分、响应速度、通过率）一律条形对比；
  - 观战页顶部赛况条用**分段进度**表达"已完成 N / 共 M"，不是纯文字"N of M"；
  - 响应耗时用细横条表达快慢对比（观赏性高、且信息真实）；
  - 硬战场通过率用进度环或分段条；
  - 软战场双轨（人类票 vs 裁判分）用**并列双条**呈现，视觉上就体现"两轨不合并"。

### 4.9 前端

- `app/page.tsx`：选题 + 选选手（localStorage 预设阵容）+ 开打；开打前调 `/api/providers/ping` 预检，不可达的选手直接置灰并标注原因。
- `app/battle/[id]/page.tsx`：观战页，N 张选手卡片流式刷新；翻车（超时/限流/空输出/鉴权失败）与精彩片段同样显眼。
- 流式渲染性能：delta 先进 **Zustand** store，按 `requestAnimationFrame` 批量 flush 到 DOM，避免每 chunk 一次 React 重渲染。
- **赛况条**：常驻顶部，显示当前 phase、已完成选手数 / 总数、本场已用时长——这是"比赛正在进行"的主要时间感来源。
- **先手标记**：按完成先后给卡片标注「先手」制造节奏感；仍在流的卡片用打字机光标提示"还在写"。
- **翻车榜**：战报底部单列最惨的若干瞬间（最短输出、最后完成、超时、空输出）。
- 断线：`EventSource` 自动重连，浏览器自动带 `Last-Event-ID`，服务端只补发该 id 之后的事件；仍不完整则回退 `/snapshot` 拉快照。
- 赛后 `settlement`：**一句话总结**（如"deepseek 以 2:0 通杀，本场无人翻车"）置顶；其下软战场显示人类票数 + 裁判分项与理由（两栏并列，不合成总分）、硬战场显示通过率/耗时/内存；裁判或沙箱降级时显示对应横幅。

### 4.9 目录结构

```
app/
  page.tsx                         擂台首页
  battle/[id]/page.tsx             观战页
  api/battle/run/route.ts
  api/battle/[id]/stream/route.ts
  api/battle/[id]/vote/route.ts
  api/battle/[id]/cancel/route.ts
  api/battle/[id]/snapshot/route.ts
  api/providers/ping/route.ts
lib/
  arena/types.ts  soft.ts  hard-code.ts
  battle/runner.ts  events.ts
  providers/registry.ts
  judge/runner.ts
  db/client.ts  schema.sql  repo.ts
  sandbox/backend.ts  pyodide-worker.ts  judge0.ts
components/arena/  (contestant-card, battle-shell, vote-bar, settlement)
docker/judge0/docker-compose.yml     仅 SANDBOX_BACKEND=judge0 时使用
test/              node:test 单元 + 集成
```

## 5. 数据模型（SQLite）

`PRAGMA journal_mode=WAL;`，全部写在 `lib/db/schema.sql`。

```sql
battles(id TEXT PK, arena TEXT, topic TEXT, status TEXT, phase TEXT,
        created_at INTEGER, completed_at INTEGER, error TEXT)

contestants(id TEXT PK, battle_id TEXT, slot INTEGER, provider_id TEXT,
            model_id TEXT, position_index INTEGER,
            prompt_tokens INTEGER, completion_tokens INTEGER,
            latency_ms INTEGER, output_text TEXT, code TEXT,
            status TEXT, error_message TEXT)

-- 语义约定：slot = 选手稳定座位号（全场不变）；
-- position_index = 该选手在"被呈现给裁判/观众那一刻"的排位。
-- 软战场裁判跑两轮位置对调，故才有 position_index 与 slot 分离的必要；
-- 人类投票的 position_index = 投票时该选手在页面上的展示顺序。
-- 人类投票：一局一人一票（弱去重）
-- voter_key = sha256(ip) + ':' + localStorage 中的随机 uuid
votes(id TEXT PK, battle_id TEXT, contestant_id TEXT, voter_key TEXT,
      position_index INTEGER, created_at INTEGER,
      UNIQUE(battle_id, voter_key))

-- 裁判分：每轮每选手一行；round=1/2 对应位置对调两轮
judgements(id TEXT PK, battle_id TEXT, contestant_id TEXT, round INTEGER,
           judge_model TEXT, position_index INTEGER,
           scores_json TEXT, rationale TEXT, created_at INTEGER)

sandbox_results(id TEXT PK, contestant_id TEXT, backend TEXT,
                isolation_level TEXT, language TEXT,
                status TEXT, passed INTEGER, total INTEGER,
                time_ms INTEGER, memory_kb INTEGER,
                compile_output TEXT, stdout_tail TEXT, degraded INTEGER)
```

写并发：SQLite 单写者，`lib/db/repo.ts` 内所有写操作经一个内存队列串行化，读不排队。

## 6. 安全边界

### 6.1 API Key 流（第一期）

- Key 只出现在 `POST /api/battle/run` 与 `/api/providers/ping` 的请求体，前端从 sessionStorage 取。
- 服务端仅在这一次对战的上下文（内存）中持有，不写库、不写日志、不随错误堆栈外泄。
- 日志脱敏：所有日志输出经过 `redact()`，对 `sk-` 等前缀串与请求体中的 key 字段统一替换为 `***`。
- 对战结束即随上下文回收，无持久化。二期做账号 + 信封加密时，本表的 contestants 结构与 Key 流分离，不受影响。

### 6.2 沙箱

见 4.4。另有硬性约束：沙箱输入只有题目 + 模型代码，环境变量零注入；所有后端凭据只走服务端环境变量，永不进 Worker 或容器。

**已知事故（作为设计依据，不是我在吓唬）**

| 事故 | 影响 | 本设计的对应缓解 |
|---|---|---|
| CVE-2026-5752（CVSS 9.3，Cohere Terrarium） | "让模型写 Python 并安全执行"的参考实现；Emscripten 导出未清理，逃逸到宿主 JS。2026-02-19 报 CERT，2026-04-21 公布，超期 61 天无补丁，厂商发 1.0.1 后**仓库归档、宣布不再修** | 禁用 `js` 模块与 `pyodide.globals`；Worker 无宿主回调 |
| CVE-2025-68668 / CVE-2026-0863（n8n） | 进程内 Pyodide 信任边界塌陷，沙箱逃逸拿下 n8n 运行时；修复方式是**移除进程内 Pyodide** | 禁止宿主进程内加载；每提交独立 Worker 并 terminate |
| Pyodide 逃逸 RCE（Grist-Core） | 可读环境变量、配置文件与凭证，并横向移动 | Worker 内零凭证、零 DB 连接 |
| Judge0 沙箱逃逸报告 | 容器隔离也不是绝对安全 | Judge0 仅作可选后端；启用时仍要求禁网、一次性容器、宿主机专用 |

**残余风险（写在 spec 里，不藏着）**：Pyodide 与容器都存在已公开的逃逸路径。默认后端是语言级沙箱，其安全模型弱于 OS 级隔离。

**上线闸门（硬约束）**：运行在**非 OS 级隔离**后端时，硬战场**只允许本地 / 自用 / 小范围可信用户**使用。将来若要在公网开放给陌生用户写代码，**必须先启用 `SANDBOX_BACKEND=judge0`（或等价的 OS 级隔离后端）**，否则不得开放。第一期产品定位即按此写死。

### 6.3 内容与滥用

- 用户自定义题目做长度与敏感词本地校验，不通过不进对战。
- 第一期无账号 → 无速率限制体系，仅在 `run` 入口加每 IP 每分钟 5 场的粗限流（内存计数），防无意打爆。
- 二期补：对战结果页举报、题目审核。

## 7. 错误处理与降级矩阵

| 故障 | 行为 | 用户可见 |
|---|---|---|
| 单模型超时（>120s） | 该选手 `timeout`，其余继续 | 卡片显示"超时未完成" |
| 单模型限流 / 欠费 / Key 无效 | 该选手对应状态，其余继续 | 卡片显示具体可读原因（不含 Key） |
| 全部选手失败 | `battle.failed` | 整场失败原因（如"所有模型均不可达，请检查 Key 与网络"） |
| 开打前预检失败 | 该选手不登场 | 选手置灰 + 原因 |
| 裁判解析失败 / 裁判模型不可用 | 跳过裁判轨 | 横幅"裁判不可用，本场仅人类投票" |
| 沙箱后端不可用 / Worker 崩溃 | 不真实执行，`isolation_level='none'` | 横幅"沙箱不可用，本次未真实执行" |
| SSE 断线 | EventSource 重连 + snapshot 补齐 | 无感 |
| 输入无代码块 | `passed=0` | 标注"未产出可执行代码" |

## 8. 测试策略

原则：纯逻辑层放 `lib/`，不 import Next，用 Node 内置 `node:test` 直接跑（Node 24 原生 TS 类型剥离），零额外测试框架依赖。

- **单元**：`arena/soft.ts` 的 prompt 构建与裁判 JSON 宽松解析（含畸形输出）；`arena/hard-code.ts` 的代码块抽取（无块 / 多块取第一 / 围栏语言标识混杂）；`providers/registry.ts` 的 provider 工厂分支；`db/repo.ts` 的投票唯一约束与 `position_index` 落库。
- **集成**：`battle/runner.ts` 用注入的 fake provider（脚本化流）跑完整状态机：全成功 / 单失败 / 全失败 / 取消四条路径，不真调外部 API。
- **沙箱**：默认后端（Pyodide Worker）跑三个真实用例——正常通过、死循环被 `terminate()` 截断、尝试读环境变量拿不到任何值；`SANDBOX_BACKEND=judge0` 时额外跑 hello world / 超时 / 禁网三例，标为可跳过。
- **端到端（内置浏览器）**：用内置浏览器打开 dev server，真打一场软战场与一场硬战场，核对 SSE 事件顺序、流式渲染、投票落库、战报内容，并留截图。这条替代大部分手工验收。
- **手动验收**：真实选手池打一场软战场 + 一场硬战场，核对 SSE 事件顺序与战报内容。

## 9. 本地运行

```
# 1. 起应用
npm install && npm run dev
```

沙箱默认走 Pyodide（装 npm 依赖即可，无额外外部服务）。只有切到 OS 级隔离后端才需要 Docker：

```
$env:SANDBOX_BACKEND = "judge0"     # 可选路径
docker compose -f docker/judge0/docker-compose.yml up -d
```

环境变量（`.env.local`，全部只存服务端）：

```
SANDBOX_BACKEND=pyodide        # pyodide | judge0
JUDGE0_BASE_URL=               # 仅 judge0 模式需要
JUDGE0_AUTHN_HEADER=           # 仅 judge0 模式需要
```

模型 Key 不进 `.env`，由前端在对战请求里带。

## 10. 风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| 最大的未验证假设：这个产品"好看不好看"，赛制立不立的住 | 高 | 第一期只做观战闭环 + 预置题库，最快看到一场对打；不做账号体系 |
| **默认沙箱是语言级隔离，弱于 OS 级隔离**（Pyodide 有 CVSS 9.3 逃逸且上游已归档停止修复） | 高 | 硬性约束全上（禁互操作、独立 Worker、零凭证、terminate）；`sandbox/` 做接口留 Judge0 后端；**上线闸门**禁止公网开放无 OS 级隔离的硬战场 |
| Docker 引擎当前未运行 | 低 | 默认后端不用 Docker，已不阻塞开工；切 judge0 时才需处理 |
| 国内网络直连海外模型不可用 | 中 | 默认选手池用国内可直连 + Ollama；海外标 needsProxy |
| OpenAI 兼容端点走错 API（Responses vs chat completions） | 中 | 显式 `createOpenAI({ baseURL })`，接第一个第三方模型时立刻实测 |
| `node:sqlite` 仍为 RC | 低 | `lib/db/client.ts` 接口隔离，二期可无痛换 Postgres |
| Next.js 16 近期多个 critical RCE | 中 | 锁 `>=16.3.6`；不用 `next/og`、不用 Image Optimization |
| SSE 长连接 + 自托管 | 低 | 自托管 Node；明确不上 serverless |
| 无账号导致无法防刷 | 低（一期） | 每 IP 每分钟 5 场粗限流；二期随账号体系补 |

## 11. 第二期路线（不在本 spec 内）

1. 账号体系 + API Key 信封加密保险库（高敏感安全面，独立 spec）。
2. **Python 沙箱扩语言**（每种语言单独评审；若涉及编译型原生执行，优先走 Judge0 这类 OS 级隔离后端）。
3. 偏好学习：用已存的 `position_index` + 双轨分数做位置偏见校正与 Bradley-Terry 战力拟合。
4. 排行榜与模型档案页、跨设备同步。
5. 投票真防刷、题目审核、举报。

## 12. 工作量估算（第一期，单人 + AI 协作）

- 脚手架与依赖锚点固化：0.5 天
- 数据层（schema + repo + 封装）：1 天
- provider 注册表 + 适配（含第一个第三方模型实测打通）：1 天
- 编排 runner + SSE 契约：1.5 天
- 软战场 + 裁判（含双轮对调）：1.5 天
- 硬战场 + 沙箱层（Pyodide Worker，Judge0 后端留接口不实现）+ 降级：2.5 天
- 前端擂台/观战/战报：2.5 天
- 测试与手动验收：1.5 天

合计约 12 个工作日。最大两个变量：**Pyodide 在 worker_threads 里的实际执行开销**（决定题目时间线与可用题量）和**第一个第三方 OpenAI 兼容模型打通**。
