# 官方生态深挖：可直接拿的完整件与点睛物件

- 日期：2026-10-13
- 方法：把 `node_modules` 内 AI SDK v7 全量文档 + Next 16 文档 + 已装/可装官方包再过一遍，按"完整生态级迁移 / 观感大件 / 点睛小件 / 明确不拿"四档归档
- 纪律：能拿就拿，拿之前评估"官方件替换掉多少自研代码"，拿之后必须有可离线验证的测试

## 一、本轮已吃下（2 件，测试全绿）

| 件 | 官方出处 | 落点 |
|---|---|---|
| `smoothStream`（官方流平滑，支持 `Intl.Segmenter` 中文按词切分） | `ai` 导出；`streamText` 的 `experimental_transform` | 选手流 12ms/片：观感从"突发大块"变"逐词浮现"，翻车现场也更可读 |
| `simulateReadableStream`（官方测试流工具） | `ai` 导出（官方缓存文档同款用法） | 替换 runner 测试里手搓的 `ReadableStream` 夹具 |

## 二、已执行（2026-10-14 补充批次，全部完成）

| 件 | 落点 | 证据 |
|---|---|---|
| Next `after()` | `run` 路由注入 `schedule: (task) => after(task)`；runner 开放 `schedule` 缝（测试/无请求域回退 setTimeout，行为等价） | 行为锁"注入调度器时由它启动对战"；`api.test` 7/7 |
| `zustand/persist` | 阵容/题目选择迁到 `useLineupStore`（`lib/client/lineup-store.ts`），替换手写 localStorage；**Key 与阵容分流**：Key 只进内存 + sessionStorage，persist 只序列化阵容 | 4 条行为锁（含"persist 内容不含任何 Key"、旧格式一次性迁移、幂等） |
| `useChat` + 官方流 helpers | 新增 `/solo` 单挑台：`useChat` + `DefaultChatTransport` + `convertToModelMessages` + `toUIMessageStream`/`createUIMessageStreamResponse`（`toUIMessageStreamResponse` 已 deprecated，改用官方推荐拆分式） | 路由 3 条行为锁；HTTP 探针实测：`start → error(可读) → [DONE]`，Key 不泄漏；页面渲染实测（10 provider 含 Gateway） |
| `pruneMessages` | **按证关闭**：官方签名只处理 reasoning/tool/empty 消息，不裁剪普通对话文本——对我们的固定 3 轮无调用价值，轮次 6+ 时再评估 | `ai` d.ts:6959 |
| provider baseUrl 环境变量覆盖 | `MELEE_BASE_URL_<ID>`（relay/自托管/测试缝） | registry 测试 |
| `ARENA_IDS` 单一来源 | 从组件层下移到 `lib/arena/types.ts`（修 lib→组件反向依赖） | 类型检查 + 全量测试 |

## 三、一号迁移（完整生态级）：UIMessage 数据部件流协议

- **官方件**：`createUIMessageStream` / `createUIMessageStreamResponse` / `pipeUIMessageStreamToResponse` / `readUIMessageStream`，以及 `UIMessage<metadata, DataParts>` 的**类型化数据部件**（`04-ai-sdk-ui/20-streaming-data.mdx`、`24-reading-ui-message-streams.mdx`、`50-stream-protocol.mdx`）
- **映射**：我们的战斗事件正是"自定义数据部件"的标准用例——13 种事件 → `data-battle` 部件（zod schema 直接变部件 schema）；SSE 组帧、`[DONE]`、错误部件交给协议；事件重放对接官方 resume（`03-chatbot-resume-streams.mdx`，`streamId` + 部件持久化）
- **收益**：生态组件（`useChat` 等）可直接消费战况流；协议级断线恢复；不再自维护 SSE 组帧细节
- **成本**：`stream` 路由重写 + 客户端解析适配 + `api.test.ts` 的 SSE 断言重写（约一天量级）
- **实现前必须实测**：N 选手 + 裁判**多生产者并发写单流**在 `createUIMessageStream` 的 writer 语义（官方示例以单生产者为多）

## 四、二号迁移（观感大件）

| 件 | 用途 | 成本/备注 |
|---|---|---|
| **ECharts 雷达图** | 结算面板：裁判三轴 × N 选手叠加雷达（与现有条状图并列，不做替换） | 符合既定规则"图表用 ECharts"；按需引入 radar/tooltip/graphic 组件可把增量压到 ~60-100KB gzip |
| **shiki 代码高亮** | 硬战场输出与裁判点评里的 Python 代码块 | VS Code 同源；用 `createHighlighterCore` + python grammar，需评估 oniguruma wasm 体积 |

## 五、点睛小件（各自独立、随时可上）

| 件 | 官方出处 | 用途 | 代价 |
|---|---|---|---|
| `@upstash/ratelimit` 类 | 官方缓存文档推荐生态 | 多实例滑动窗口限流 | 需 Redis；单实例演示不急（`ratelimit.ts` 文件头已留升级路径） |
| Gateway `getAvailableModels()` | `@ai-sdk/gateway` | 模型目录 | **已接入** ✓ |

以上小件中 `after()` / `pruneMessages` / `zustand/persist` / `useChat` 已于 2026-10-14 处置完毕（见第二节）。

## 六、明确不拿（防反复）

- `@ai-sdk/rsc` 的 `createStreamableUI`（`05-multiple-streamables.mdx`）：RSC 专用，"流 UI 组件"比赛事事件流重且不匹配多选手模型
- `ToolLoopAgent` / `WorkflowAgent` / LangGraph / Mastra：见 `2026-10-13-orchestration-and-isolation.md`（运动规则 + 运行时成本判决）
