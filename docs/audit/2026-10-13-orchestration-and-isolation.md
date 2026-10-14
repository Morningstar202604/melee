# 多智能体编排与隔离策略审计

- 日期：2026-10-13
- 触发：本项目（N 个模型同场竞技 + 裁判 + 确定性环境）本质是一个多智能体编排系统——按编排框架的标准重新审视底层
- 判据来源：AI SDK v7 官方定义（`node_modules/ai/docs/03-agents/01-overview.mdx`、`07-workflow-agent.mdx`）、各框架公开定位、本项目测试证据

## 一、先说定性：我们是 workflow，不是 agent 框架的目标场景

AI SDK 官方定义：**"Agents are LLMs that use tools in a loop"**（LLM 决定下一步动作、工具扩展能力、循环负责上下文管理与停止条件）。本项目：

- **控制流是确定性的**：谁在第几轮对谁说话、何时进入裁判、何时结束，全部由代码规定，模型无权改变（这是赛制的信任锚点）
- **模型没有工具**：比的是裸模型单次作答能力（运动规则）；唯一"工具"是沙箱——它是**判定环境**，不是选手的可调用能力
- 官方对这类"预定义工作流"的指引就是：**用宿主语言的普通控制流 + AI SDK 原语**（消息数组、`Output.object`、`reasoning`、`pruneMessages`）——本仓库已逐项对齐（见 `2026-10-13-official-conformance.md`）

所以结论不是"要不要上框架"，而是"把框架级的编排模式与隔离策略逐条对齐"。下面两部分就是这件事。

## 二、编排框架逐项对照（判决）

| 框架 | 定位 | 判决 |
|---|---|---|
| AI SDK `ToolLoopAgent`（`ai` 包） | 内存内"LLM+工具+循环" | **不适用（本期）**：我们的循环是确定性的赛事流程。二期"工具场"（让选手用工具解题）直接挂 `tool()` + `stopWhen` |
| AI SDK `WorkflowAgent`（`@ai-sdk/workflow` + `workflow@5`） | 耐久/可恢复的 agent（跨重启续跑、step 级重试与审批） | **Defer（官方指定挂载点）**：当"对战要跨服务重启续跑"成为需求时用它；当下的"内存注册表 + SQLite 快照 + Last-Event-ID 重放"是赛事的自觉选择（观赛断线恢复已覆盖） |
| LangGraph.js | 图状态机 + checkpointing | **不采用**：以图节点建模 agent，为"LLM 自主路由"设计；我们的相位流程 7 个状态且线性，入图不换取任何缺失能力；其 checkpoint 与我们已有的 SSE 事件重放重叠 |
| Mastra | TS 原生 agent 产品框架（workflow/memory/RAG/evals 全家桶） | **不采用**：面向"构建带工具与记忆的 agent 产品"；本项目刻意不给选手工具/记忆（运动规则），引入是反向依赖 |
| OpenAI Agents SDK / Google ADK / AutoGen / CrewAI | 厂商 SDK 或 Python 生态编排 | **不采用**：厂商绑定或跨语言运行时；选手层已由 AI SDK 统一 9 家 provider（44+ 官方 provider 生态），换框架=丢掉"多厂商同场"这个核心能力 |
| XState | 前端/服务端状态机运行时 | **不采用（记录）**：赛道相位是 7 状态线性流程，不变式已由 runner 测试矩阵覆盖；为一个状态机引入运行时违背最小依赖 |
| Temporal / Inngest 等 | 分布式工作流引擎 | **不采用**：需要独立服务与运维面，与"本地一条命令起"的定位冲突 |

## 三、编排模式映射（我们已实现的框架级能力）

| 模式 | 本仓库实现 | 证据 |
|---|---|---|
| Orchestrator–Worker | `runner.ts` 编排 + 每选手独立 run 状态 | `runner.test.ts` 全矩阵 |
| Parallelization（赛跑） | 轮内 `Promise.all`，单选手失败不连坐 | 同上 |
| Pipeline（多轮） | `TurnPlan`：轮间串行、轮内并行、回合事件 `round.changed` | 多轮编排测试 |
| Supervisor / 评分者 | 裁判 agent：匿名标签 + 三轮位置对调 + 分轴评分 | `judge/runner.ts` |
| 确定性环境（Environment） | Pyodide 沙箱 / Judge0 / 棋规校验——"动作→环境判定"三元组 | `chess.test.ts` 题库自检；`judge0.test.ts` 9 例 |
| 事件契约 / Blackboard | zod discriminated union + SSE + Last-Event-ID 重放 | `events.ts`、stream 路由 |
| 背压 | 客户端 rAF 帧合并 + store 单次提交 | store 节流测试 |
| 失败与预算隔离 | 单轮超时 / 全场超时 / 输出预算帽 | `orchestration-contract.test.ts`（本轮新增） |

## 四、隔离矩阵：agent 之间到底怎么"分隔开"

| 隔离面 | 机制 | 证据/测试 |
|---|---|---|
| 上下文隔离 | 每选手独立 `ModelMessage[]` 历史；对手可见性规则 `turn.round < 当前轮`，本轮在飞内容不进他方提示词 | 多轮编排测试断言"第二轮可见对手首轮、不含本轮" |
| 内心话隔离 | `reasoning` 只进观测层（UI/DB），**不进裁判**（`Submission` 不含 reasoning） | `runner.ts` finalizeRun；reasoning 行为锁 |
| 匿名隔离 | 裁判只见 `A/B/C` 标签，不知 provider/model；位置对调三轮消偏 | `judge/runner.test.ts` |
| 凭证隔离 | Key 仅存在于当次请求内存；全链 redact（含 AI SDK `onError` 脱敏）；不进提示词、不进日志、不进沙箱 | pyodide worker "不读 process.env" 测试；redact 测试 |
| 执行隔离 | 沙箱 Worker：无 process.env、无 JS 互操作、输出 64KB 限额、`terminate` 硬止损；Judge0：`enable_network=false`、内存/进程/文件限额 | `pyodide-worker.test.ts`、`judge0.test.ts` |
| 失败隔离 | 单选手失败→其余照常；逐轮超时（debate/banter 独立预算）；全场超时 abort 剩余回合；取消竞态有守卫 | `runner.test.ts`（含对战级超时） |
| 预算隔离（本轮新增） | 每赛场 `maxOutputTokens` 帽：soft 2500 / debate 2000 / banter 800 / hard-code 4000 / chess 64——只防失控流，数值远高于正常输出 | `orchestration-contract.test.ts` |
| 观测者隔离 | 观众只有只读流 + 独立写路径（投票/押注各自路由，`UNIQUE` 去重，押注终态收盘） | `prediction.test.ts`、vote 路由测试 |
| 数据面隔离 | 赛事数据与模型身份脱敏：战报不含 Key/URL；方向性数据（position_index）为二期偏好学习埋点 | repo/redact 测试 |

## 五、本轮变更与验证

- 新增：每赛场输出预算帽（上表"预算隔离"），并把"判定来源 / 人数区间 / 回合与超时预算 / 输出帽"固化成 **编排契约测试**（3 条，全绿）
- 无框架引入（判决见二）；无对新依赖
- 全量回归：`npm test` 143 全绿、lint / tsc / build 全清

## 六、留给二期的官方挂载点（当需求出现再上，不预先付成本）

1. `@ai-sdk/workflow` WorkflowAgent —— 对战跨重启续跑
2. `ToolLoopAgent` + `tool()` + `stopWhen` —— "工具场"新玩法
3. `pruneMessages` —— 轮次从 3 提到 6+ 时的上下文修剪
4. `LanguageModelV4Middleware` —— 遥测/缓存/脱敏的统一入口（现在是散点式）
