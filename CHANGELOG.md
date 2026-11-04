# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [1.8.0] — 2026-11-04

### 新增

- **CLI 与 exe 交付**：`services/match/cli.py` 一个二进制驱动全部产品面（serve/games/stats/archive/health/match create|get|events|export|import|open/seasons/season-run/mcp），全局旗标 `--api/--token/--lang/--json` 顶层与子命令级都可用；PyInstaller onedir 打包 `melee.exe`，`scripts/build_exe.py` 一键产出便携 zip。
- **管理后台**：新增 `admin.html`（双语）：服务健康、运维快照、任务队列、配额用量、赛季调度器（可手动 tick）、站点合计；导航入口加入全部页面。
- **Agent harness**：官方 `mcp` SDK（锁定 1.x）实现 `melee mcp` stdio 服务器（7 个工具）；A2A `Agent Card`（`/.well-known/agent-card.json`，路由冻结测试覆盖）；比赛终局 webhook（HMAC 签名 + 退避重试 + 回环防护）。
- **可移植对局文件**（Lichess PGN 类比物）：`GET /matches/{id}/export` 单文件事件流快照（含 16 位可复现指纹）；`POST /matches/import` 导入为只读回放场次；空库可导入；CLI `match export/import`。
- **赛季调度自动发现**：建赛季给了 `startAt` 现在会到点自动开赛——不再要求 `MELEE_SCHEDULE` 点名；点名退化为过滤，`off` 显式关闭。
- **凭据格式宽容**：`MELEE_API_TOKENS` 接受裸 token（自动命名 token-1/2）。
- **注册时实测探测**：每个 TextArena env 注册前 `ta.make` 一次，装配失败的赛制不再进入目录。

### 变更

- `docs/RESEARCH-2026-11-04.md`（17 项目深度借鉴映射）、`docs/AGENT-HARNESS.md`（协议接入面与边界）。
- 新增 4 个测试模块（cli/harness/webhooks/portable）；双语巡检覆盖 admin 页。
- exe 打包：动态收集 openskill mypyc 模块与 TextArena 全部子模块。

### 修复

- CLI `serve` 用 `uvicorn.Server(config).run()`（曾把 Config 误当 app 传给 `uvicorn.run()`，exe 绑到 8000）。
- exe 缺文件三连修：契约 schema 入 datas；mypyc ABI 后缀模块按 import 名收集；TextArena 动态 env 全量收集。
- `admin.html` 未闭合标签。

## [1.7.0] — 2026-11-02

### 新增

- **双语界面（英文为默认，中文可切换）**：五个页面全部走统一字典，
  顶栏提供语言切换并记住选择；`?lang=en|zh` 可把语言钉在链接上。
- **语言无关的事件流文案**：解说、战报一句话、高光标题、导播触发器与终局原因
  改为存 `msg` + `params`（英文 `text` 作为老客户端兜底），读取时按请求语言
  重新渲染。今天录的比赛以后仍可用任一语言重读。
- **API 全面对语言敏感**：`/games`、`/archive`、`/stats` 的赛制名跟随
  `?lang=` 与 `Accept-Language`；`/matches/{id}/events` 的解说与切镜触发器、
  `/recap`、`/share` 同样按请求语言渲染。界面语言还会探测浏览器偏好。
- **双语截图库**：`docs/screenshots/{en,zh}/` 各 12 张，全部在真实服务与真实
  比赛上拍摄；`scripts/capture_gallery.py` 支持一次拍两种语言，页面报错即失败。
- **`scripts/audit_i18n.py`**：双语界面巡检，覆盖 5 页 × 2 语言 + 2 个移动端视图。

### 变更

- **README 改为英文主版**，中文版移到 `README.zh-CN.md` 并双向互链；
  两版都改为对外展示语气，截图与视频入口放在首屏。
- `services/match/api.py` 的 `version` 与 `CHANGELOG` 顶部条目现在由
  `test_architecture.py` 强制一致。
- 新增 `docs/VERSIONING.md`：语义化版本口径、发布流程、变更日志规则。
- `docs/ROADMAP.md` 重写：每个条目都带验收标准，1.3–1.7 的已完成项回填。

### 修复

- 顶栏在 1440px 下把「剧场」按钮挤到第二行、压住舞台：改为强制单行，
  工具栏在自身内部横向滚动。
- 导播台在未打开任何比赛时显示绿色「已连接」：连接状态改为跟随是否正在导播。
- 「最近 14 天」图表里零值柱几乎不可见，被读成图表没渲染：零值改为可见矮桩。
- 英文界面胜者榜单复数错误（`1 wins` → `1 win`）。
- 战报「最慢一手」里的动作类型显示为原始枚举（`ask`）：改为按语言输出动词。
- 旧 README 引用的截图路径在重构后失效，已与双语截图库同步。

### 文档

- `docs/screenshots/README.md` 改为英文，并说明双语目录结构与复现步骤。
- `docs/STATUS.md`、`docs/QUALITY.md` 与当前门禁保持一致。

## [1.6.0] — 2026-11-02

### 新增

- **选手能力面**：`ModelSpec` 新增 `systemPrompt`、`topP`、`seed`、`stop`、
  `frequencyPenalty`、`presencePenalty`、`stream`、`includeHistory` 与 `protocol`
  （OpenAI-compatible / Anthropic / Gemini）；`maxTokens` 上限从 4000 提升到 32000。
- **回合墙钟**：`config.turnTimeoutMs`（1–600 秒）由 runner 真实强制，超时按弃权处理；
  HTTPAgent 请求体携带真实 `deadlineMs`，`timeoutS` 上限放宽到 600 秒。
- **凭据引用**：`apiKey` 支持 `{env:VAR}`，由 worker 在解封后解析；缺失按结构化 player id 弃权。
- **前端高级参数**：快速开赛弹窗可配置协议、采样参数、自定义提示词、自记忆与回合时限。

### 安全

- 缺少 `MELEE_SECRET_KEY` 时，带外部 API Key 的建场直接返回 503，不再明文写入队列。
- `baseUrl` / `endpoint` 拒绝 URL userinfo、query 与 fragment，避免凭据经队列与管理接口回显。
- 422 校验响应过滤 `apiKey`、authorization、token、password 等敏感输入。
- LLM 成功输出、失败输出与流式输出在写入事件前统一脱敏。

### 修复

- `PacedAgent` 现在透传 `set_deadline` 与 `record_history`，`delayMs` 与回合墙钟组合不再失效。
- `judge_missing_key` 使用结构化 player id，重叠 ID 不再误判弃权方。
- 导播台在直接打开场次时不显示「没有正在进行的比赛」，消除空态与已连接状态矛盾。

### 文档

- README 中英文改为品牌首屏 + 真实截图 + 演示视频入口；重写截图说明与复现步骤。
- 更新 API、质量、状态与能力审计文档，移除过期测试数字和无证据的性能承诺。
- 社交卡移除旧「433 tests / 91.1% / 8 gates」，改为当前品牌主张。

## [1.5.0] — 2026-10-29

### 新增

- **多 AI 编队（不止红蓝两端）**：`POST /matches` 新增 `config: {teams, teamSize}`。
  代号支持 2–6 队 × 每队 2–4 人（4–16 个 AI），默认仍是经典 4 人。
  3 队及以上各自分到自己的词（旧实现只给红蓝上色，第三队永远没有自己的词）。
- **`roster.py` 编队层**：严格区分 player（身份）/ seat（位置）/ team（阵营），
  11 条纯函数测试；取代原来"players[0] 就是红队队长"的隐含约定。
- **公平性门禁**（`test_fairness.py`，52 条参数化）：信息隔离、先手轮转、
  回合对称、资源独立、多队可重放、自然能力完成度。
- **`MinimaxAgent`**：只吃 `observe()` 的极小极大选手，用来证明"同等强度下平台无偏袒"。
- **赛后摊牌**：终局后 `public_state` 恢复完整隐藏信息（扑克转播的收尾时刻）。

- **多队展示层三处缺陷**：第 3 队获胜时战报自检误报「胜者不在参赛名单里」
  （`verify_summary` 白名单只认红蓝）；一句战报显示原始字符串 `team3`；
  战报/解说/分享卡/视频/前端五处各自维护队名表。统一收敛到
  `codenames.team_label`（前端 `teamLabel` 同规则），观战页新增编队入口
  （队数/每队人数，仅代号显示），棋盘支持 N 队配色（8 色板自动编号）。

### 修复（全部为实测复现的真实缺陷）

- **隐藏信息泄露 3 处**（High）：
  - Kuhn Poker 的 `gameState.player_cards` 含双方手牌；
  - GOPS 的 `player_hands` 含双方手牌；
  - GOPS 的 `pending_bids` 让后手看到先手的秘密出牌——赛制竞技性直接归零。
  现在按赛制声明的 `hidden` 表裁剪：观察只留自己那份，公开状态整个删掉。
- **先手固定**（High）：上游先手永远坐 0 号位，六种子实测全同一人先手。
  现在 `seats` 按 seed 轮转，40 局严格 20/20。
- **二十问回合不对称**（High）：`maxQuestions=21` 让 A 问 11 次、B 问 10 次；
  且"问满上限判给提问者"在偶数预算下永远偏袒后手。现在预算取人数整数倍、问满一律平局。
- **二十问模型永远不可能合法猜中**（High）：`legal_moves` 只给
  `guess:<candidate-name>` 占位符，观察里没有候选名单。现在候选名单进观察与提示词。
- **Othello 第 18 步卡死**（High）：无子可下时上游自动 skip，但适配器不给 `[pass]`。
- **GOPS 第 18 步被迫弃权**（High）：手牌快打完时观察不再印 Available Moves。
  新增 `_hand_moves()`：合法动作 = 自己手里尚未打出的牌。
- **Connect-four 第 6 步非法**（Medium）：从观察文本正则抠棋盘，`rows[0]`
  不保证是顶行。改用结构化 `gameState.board`。
- **多队编队三处缺陷**（High）：
  - 3 队时第三队没有自己的词（配色只按红蓝分配）；
  - 每队多队员时只有第一个队员出过场（索引用了全场步数 `turnIndex`，取模跳号）；
  - 编队参数没写进 `match.created`，重放退回默认 2 队，胜负与原局不一致。

### 变更

- 代号 `player_range` 从 `(4,4)` 扩到 `(4,16)`。
- `CreateMatch.players` 上限从 8 扩到 16。
- 测试 629 → **729 passed / 1 skipped**（+100 条）；行覆盖 94.13%、分支 86.35%。

## [1.4.0] — 2026-10-29

### 新增

- **身份化多视角回放**：`GET /matches/{id}/view/as/{player}` 返回该选手当时可见的
  事件子集与折叠视图。`visibility` 契约此前只用于"隐藏"，现在能按身份重建视角。
- **赛后揭秘**：`GET /matches/{id}/events?reveal=1` 在比赛结束后解锁私有事件
  （队长内心戏等）；**直播期返回 403**——隐藏信息赛制的看点就是直播期看不到。
- **逐手耗时与用量**：`action.applied` 新增 `elapsedMs` / `usage`（真实观测，不编数），
  战报新增 `timing` 聚合（累计、最慢一手、tokens）。LLM 选手报真实墙钟；
  脚本选手无网络延迟时不显示 0 值行。
- **回放时间轴按真实时间缩放**：新增 `apps/web/src/watch/timeline.js`（纯函数）。
  旧实现按事件序号排布，一局二十问里提问与思考片段差一个数量级，
  拖动条前 90% 原地打转、最后 10% 跳完整局。时间戳缺失时退化为按序号插值。
- **时间轴切镜点可点击跳转**：橙点标出 `director.cue` 与终局，像素级重叠时合并
  （保留终局），避免按钮互相 intercept。
- **前端单测**：`apps/web/test/` 9 条 `node --test` 用例，覆盖时间映射、逆运算、
  退化路径、重叠去重、空流；已接入 `verify_all.sh` 第 6b 项。
- **夹具样式图标**：`favicon.svg`，消除每个页面的 `/favicon.ico` 404。

### 修复

- **十个棋类赛制的高光只有"比赛结束"**（High）：`recap.py` 的 `_EFFECT_HIGHLIGHTS`
  缺少 `action` 分支，而 TextArena 适配器统一发 `kind=action`。README 宣称
  "十二种赛制都有高光剪辑"，实测 10/12 剪不出过程镜头。现在按开局/中后盘/决胜手
  三档权重生成 `board_move` 高光，实测每场 6 条高光 / 2 段剪辑。
- **`/state` 在建场窗口内 404**（High）：worker 尚未写下第一条事件时返回 404，
  而同一时刻 `/events` 是 200 空列表。现在返回 200 + `pending: true`。
- **终局弹窗把键盘用户锁死**（High）：`trapFocus` 关住焦点，但弹窗无关闭按钮，
  Escape 又因"焦点在按钮上"被提前 return。现在有 × 按钮，Escape 优先处理且不受焦点位置影响。
- **代号默认选手整局重复同一线索**（Medium）：固定取 `mine[0]`，该词翻开后仍给
  「XX相关 1」，实测 15 条线索只有 5 个唯一值、318 事件才终局。现在跳过已翻开目标
  并保证线索唯一，同种子事件数降至 107–253。
- **契约校验热路径 21.7 倍慢**（Medium）：`jsonschema` 每次重新遍历 schema。
  改用 `fastjsonschema` 编译版（1.769ms → 0.082ms/次），失败回退参考实现并保留
  带字段路径的报错。
- **契约 `--check` 漏检 payload 字段漂移**（Medium）：旧检查只比对事件类型与
  `action.kind` 枚举，新增 `elapsedMs` 时生成物没跟上仍全绿。现在逐个 payload 属性比对。
- **浏览器审计内核缺失时静默失败**（Medium）：`audit_ui.py` 在未安装 firefox/webkit
  时抛异常崩溃，`verify_all.sh` 用 `tail` 掩盖成"看起来通过"。现在明确 SKIP 并区分
  "跑过且通过"，一个内核都跑不起来才返回非零；chromium 可用系统 Edge 兜底。

### 变更

- 契约新增 `elapsedMs` / `usage`（可选字段，向后兼容）；`battle_event.py` 与
  `battle-event.zod.ts` 同步生成。
- `verify_all.sh` 从 10 项扩为 11 项（新增前端单测）。
- 测试 611 → 629 passed / 1 skipped；行覆盖 93.8%、分支 85.4%（门禁 91% / 85%）。
- 浏览器审计 35/35 通过。

## [1.3.0] — 2026-10-27

### 新增

- **赛制 9 → 12**：Simple Tak、狂野井字棋、十字军。入选依据是把 TextArena
  全部 105 个环境用适配器实跑一遍，只留"零犯规、能终局、观察直接给合法动作"的三种，
  各用 5 个种子验证。排除项也写进代码注释（Chess 不终局、HighSociety 重拍无动作）。
- **`visibility` 私有事件落地**：`events` 表新增 `visibility` 列（旧库自动迁移），
  `Game.thought_visibility()` 决定内心戏能否公开。代号队长思考标记为本人可见，
  `/events` 与 SSE 出口按观看者过滤。此前队长的配色推理会直接播给所有观众。
- **跨副本在线人数**：Redis 计数器 + TTL，`/presence` 与 `/stats` 增加 `scope`
  字段（global / process），读失败退回本进程订阅数且不显示 0。
- **高光三套模板**：`broadcast`（信息最全）/ `spotlight`（一句话+大数字）/
  `scorecard`（双方记分卡），`POST /clips/render?template=` 选择。战报页有选择器。
- **运维健康**：`/ops/health`（任务积压、最老 pending 等待、失败率、卡住的直播，
  超阈值 503）与 `/ops/metrics`（Prometheus 文本）。
- **无障碍**：`a11y.js` 的 `trapFocus`（弹层 Tab 循环 + 关闭归还焦点）、
  `activateOnKey`（div[role=button] 补 Enter/Space）；首页/赛季/观战终局卡接入；
  初始焦点给表单控件；对战状态加 `role=status aria-live`。
- **卡片化信息架构**：赛季页新增 4 张 KPI 卡、赛程改一场一卡并标出胜者；
  导播台统计改 2×2 KPI 卡；战报页增加版式选择器。

### 修复

- **Redis 连不上时不退回内存广播**（High）：旧实现只判断"包能否导入"，
  连接失败时监听线程静默死掉，跨副本事件永久不再到达，而 `/health` 仍 200。
  现在建连时 `ping()` 探测，失败即抛，由 `global_broadcaster` 退回内存。
- **Redis 订阅线程一次异常就永久退出**（High）：改为指数退避重连（1s→30s 上限），
  并关闭旧 pubsub 再新建。
- **`render_clip` 的 ffmpeg 语义**（Medium）：`ffmpeg or find_ffmpeg()` 让显式传空串
  被系统 ffmpeg 接管，调用方想禁用渲染时静默生效。改为只在 `None` 时回落 PATH。
- **事件写入重试耗尽时漏出 `sqlite3.OperationalError`**（Medium）：上层按
  `EventLogError` 捕获会接不住。统一成 `EventLogError` 并保留异常链。
- **移动端赛季页横向溢出 411px**（Medium）：新增 KPI 卡后网格轨道被 min-content 撑开；
  给轨道加 `minmax(0,...)`，并把窄屏列宽覆盖移到 `.rank` 规则之后（同优先级下后写生效）。
  修复前选手名被挤成一条竖线（118px → 6px）。
- **导播台初始文案「未连接」是假的**（Low）：连接正常、只是没选比赛，改为明确空态。
- **赛程卡片把双方都标成胜者色**（Low）：只高亮胜者。

### 工程

- **分支覆盖率 81.7% → 85.5%**（合入全部修复后的最终实测）：新增 `test_resilience.py`（68 条）专攻外部依赖
  失败路径，不 mock 业务逻辑——真实制造 Redis 断连、事件循环关闭、锁冲突、
  编码失败、坏游标、租约丢失等条件。
- **覆盖率门禁升级**：`scripts/check_coverage.py` 分别断言行 ≥91%、分支 ≥85%。
  pytest-cov 的 `--cov-fail-under` 只卡行+分支的混合值，单独的分支覆盖率它拦不住。
- **路由冻结测试**按设计拦住新端点，`/ops/*` 已登记进契约清单。

## [1.2.0] — 2026-10-27

### 重构（行为不变，测试从 479 → 485）

- **HTTP 路由按域拆分**：`api.py` 从 1160 行降到 152 行，只留组合根（CORS、安全头、
  指标鉴权、生命周期、挂载）。33 个端点分到 `routes/`：matches / season / media /
  director / share / system，共享前置逻辑收敛到 `routes/common.py`。
- **删除死代码**：`MatchHandle` 与 `MATCHES` 从未被写入却仍被 4 处业务逻辑与 10+ 测试
  引用；`Bus` 两个方法实现相同；`_AUTH_CACHE` 定义后未使用。全部移除。
- **观战页三层拆分**：`main.js` 从 673 行降到 400 行；播放状态机抽到
  `watch/player.js`，渲染抽到 `watch/render.js`；`watch.html` 的 233 行内联 CSS
  抽到 `src/watch.css`。
- **前端 HTTP 客户端合并**：`sse.js` 原先第二套 fetch（ticket / 媒体票据 / clipUrl）
  统一走 `api.js`，超时与错误格式只剩一份。
- **测试夹具收敛**：`client` / `wait_finished` / `create` 在 6 个文件里各写一份，
  现由 `tests/conftest.py` + `tests/support.py` 提供；新增 `local_client`
  专门处理 BYOK 需要"先允许本机端点再建 app"的顺序约束。
- **依赖分层**：`requirements.txt` 只留运行时；pytest / datamodel-code-generator
  等移入 `requirements-dev.txt`，镜像不再装测试工具。

### 修复

- **`/scheduler` 端点 500**（High）：拆分路由时它调用了不存在的 `scheduler.current()`。
  旧测试只断言 token 模式 401，正常路径从未覆盖。现补 `current()` / `stop_current()`
  与 200 正常路径测试。
- **事件日志共享类级锁**（Medium）：`lock: threading.Lock = threading.Lock()` 是可变默认值，
  所有 `EventLog` 实例共用一把锁，把并发写退化成全进程串行。改为 `field(default_factory=...)`。
- **WAL 每次 open 重切**（Low）：`journal_mode=WAL` 是库级持久设置，但每次打开连接都重切
  一次、要抢写锁（SSE 每 15 秒回查最热）。现只在首次建表时切。
- **Redis 广播内存泄漏**（Medium）：`_pending` 每场缓存 200 条却从未被读取，长跑无界增长。
  删除；订阅者改用 `asyncio.Event` 唤醒，不再 50ms 空转轮询。

### 工程

- **事件类型单一来源**：`eventlog.EVENT_TYPES` 从 JSON Schema 派生（`contract.event_types()`），
  不再与 schema 各写一份。
- **Lint 规则扩容**：从 5 条白名单改为 `F` / `E9` / `I` / `B` 四组，修掉 25 处 import 乱序、
  7 处 `zip()` 缺 `strict`、1 处 `raise ... from`。
- **新增架构守卫** `test_architecture.py`：事件类型来源、每实例独占写锁、33 条路由清单冻结。

### 文档

- `services/match/README.md` 从 334 行"第 N 批"历史堆叠重写为当前模块清单与扩展指引，
  历史统一由 CHANGELOG 承载。
- `docs/ARCHITECTURE.md` 更新接入层结构与前端三层拆分。

## [1.1.0] — 2026-10-27

### 新增

- **赛制扩到九种**：接入反井字棋（`ReverseTicTacToe-v0`）、库恩扑克（`KuhnPoker-v0`）、
  纯策略博弈（`GameOfPureStrategy-v0`）。三种均为双人确定性环境、无需 LLM 主持，
  每种用 5 个种子连跑验证能正常终局。`test_games_parity.py` 改为对
  `registry.available()` 全量参数化（53 条），新赛制必须同时通过解说/镜头/战报/渲染。
- **赛事库搜索**：`GET /archive?q=` 同时匹配选手名与 matchId，200 场实测 3–4ms。
  首页赛事库新增搜索框（250ms 防抖）。
- **`deploy/Caddyfile`**：compose 形态下前端与 API 同源反代，SSE 单独关缓冲。

### 修复

- **赛季重跑写坏事件流**（High）：`run_season` 对同一 fixture 无条件 `game.init + play()`，
  结果丢失后重跑会追加第二条 `match.created`，`rehydrate` 永久失效（实测：64 条 → 127 条、
  `match.created` 2 条）。现在先读既有日志：已跑完的只补记分，有前半段的从重放状态续跑。
- **推进赛季绕过配额**（High）：`/seasons/{id}/run` 不传 `maxFixtures` 时固定扣 1 格，
  一次请求可跑完整个赛季（6 场只记 1 次用量）。改为按剩余场次扣，配额不足直接 429。
- **SSE 断线永久失联**（High）：EventSource 自动重连复用旧 URL，60 秒票据过期后
  服务端 401、浏览器持续退避重试同一个废票。前端改为自管重连（每次换票 + 显式
  `lastEventId` 续传），后端 `/stream` 同时接受该查询参数。
- **compose 部署全站 404**（High）：Caddy 只做静态文件、API 另开 8099，
  页面上 `/stats`、`/archive`、SSE 全部打在静态站。改为同源反代。
- **赛季内赛制混用**（Medium）：`seasonId` 所属赛季的赛制与建场赛制不一致时返回 400。
- **`seasonId` 未校验字符**（Medium）：它会拼进对局 id 与文件路径，现限制为 `[A-Za-z0-9_.-]{1,64}`。
- **DDL 每次调用都执行**（Medium）：`EventLog.open`/`read_match`/`workqueue`/配额每次建表，
  导播台 1.5 秒轮询反复抢写锁；新增 `db.ensure_schema` 按库缓存。
- **worker 并发线程无法回收**（Medium）：`start()` 只记第一个线程，`stop()` 只 join 一个。
- **SSE 游标不前移**（Low）：backlog 发完后 cursor 未更新，每 15 秒重复从库里读同一批事件。
- **分享卡全表扫描**（Low）：`card._find` 遍历最近 500 场找一条，改为单行查询 `archive.find_match`。
- **赛事库 N+1**（Low）：每行单独查一次终局 payload，改为批量查询。
- **观战页在线人数不显示**（Low）：空状态隐藏人数后未恢复。
- **回放循环可叠加**（Low）：只 `cancelAnimationFrame`，已排队的 `setTimeout` 未清。
- **库恩扑克合法动作退化**：观察里的 `Your available actions are` 句式未被解析，
  `legal_moves` 只回格式提示，模型必然犯规。已补进两处解析器。

- **单场 token 预算**（ROADMAP 1.1 欠账）：`maxTotalTokens` 默认 60000，
  prompt+completion 累加、重试与流式请求一并计入；超限时整场写 `match.ended`，
  reason 为「token 预算用尽」，不再让免费额度被慢速耗尽。
- **契约漂移**（High）：schema 的 `action.kind` 只允许 ask/guess，而 runner 弃权时写
  `forfeit`；`contract.py` 的校验器从未接在写路径上，433 条测试全绿也发现不了。
  schema/zod/pydantic 三处补齐 `forfeit`，`EventLog.append`/`append_terminal` 落库前校验。
- **`codegen --check` 是假门禁**（High）：原先只判断生成物文件是否存在，
  schema 改了照样绿。改为逐项比对事件类型与 `action.kind` 枚举，人为回退后实测 exit=1。
- **预算终止时回合数报 0**（Low）：`play_with_budget` 原先把 `turns=0` 写进结果，
  日志看不出跑到第几手；改为记录实际完成回合数。
- **token 模式下分享链接打不开**（High）：`/share/{id}` 与 `/card.png` 都要求 Bearer，
  而社交平台爬虫带不了请求头，OG 预览 100% 返回 401。新增 `MELEE_PUBLIC_SHARE`（默认 on），
  只公开战报摘要与分享卡；`off` 可恢复鉴权。

### 性能

- 字体按字号缓存：30 帧高光渲染 335ms → 158ms。
- 分享卡单行查询、赛事库批量终局查询（见上）。

### 重构

- `run_season` 抽出 `_play_fixture`，圈复杂度 14 → 5；`radon` 全库超 10 的函数从 10 个降到 9 个，
  其中 5 个是测试函数。

### 界面（全部五个页面，三内核实测）

- **导播台缺 h1**：三内核审计都报「h1 数量 0」，补 visually-hidden 标题，读屏可用。
- **首页 KPI「赛制」显示 2**：取的是"已开过赛的赛制数"，实际可用 9 种；改为读 `/games` 的数量。
- **首页筛选条挤压搜索框**：9 个赛制 chips 与搜索框挤在同一 flex 行，搜索框被压成竖条；
  拆成独立 `.filter-bar`，移动端搜索与状态各占整行。
- **搜索无命中时提示错误**：原先直接复用"还没有比赛"，用户会以为赛事库空了；
  现在区分"没有命中"并给「清空搜索」按钮。
- **最近 14 天柱状图无日期轴**：14 根柱子读不出哪根是哪天；补首/中/末三个刻度，
  零值柱用低对比色与 3% 高度区分。
- **赛季页无赛季时永远「加载中…」**：`loadSeason()` 直接 return，积分榜与赛程停在占位文本；
  补 `renderNoSeason()` 明确空状态。
- **镜头显示英文枚举**：观战页直接显示 `wide`/`close`/`slowmo`；补 `shotLabel()` 中文映射，
  导播台的人工记录与「已切」提示同步中文化。
- **移动端顶栏挤成三行**：观战页工具栏 5 个控件在 360–390px 换行成 4 行（实测 171px 高）；
  包成 `.tools` 容器独占一行横向滚动，顶栏回到 3 行、无横向溢出。

### 验证

- **Redis 集成测试不再靠外部服务**：conftest 用 fakeredis 的 TcpFakeServer
  在进程内起 RESP 服务端并注入 `MELEE_TEST_REDIS_URL`，原先两条长期 skip 的用例
  现在真跑（广播解析、限流后端选择）。只剩 1 条 skip：`limits` 的 MovingWindow
  依赖 Lua `EVALSHA`，fakeredis 的 TCP 层未实现——原因写进 skip 消息，不伪装成通过。
- **新增第 9 道门禁：镜像静态校验**（`scripts/verify_dockerfile.py`）。
  本机只有 docker CLI、没有 daemon，无法 `docker build`；把 Dockerfile 每条指令
  翻成可执行断言：COPY 源存在、运行时 import 全在 requirements、入口可解析、
  最终 USER 非 root、HEALTHCHECK 路由存在、MELEE_DB 落在 /data、compose 挂载路径存在、
  match 不重复开 ports（否则前端同源请求会打错地址）。人为删掉 USER / 改错 COPY 源
  后实测 exit=1；并在文档里明确「这不等于镜像能构建，CI 的 docker job 才是判据」。
- **新增第 10 道门禁：浏览器 UI 审计**（`scripts/audit_ui.py`）。自己起临时服务，
  用 Playwright 三内核（chromium / firefox / webkit）跑 35 项/内核：
  页面无 pageerror、关键内容真的渲染、无横向溢出、h1/alt/按钮可读名、
  搜索与赛制筛选、拖动进回放、导播台真实切镜写库。
- Playwright 未安装时打印 `SKIP ... 不是通过`，不伪装成绿。

### 依赖

- `requirements.txt` 显式声明 `prometheus-client`（`api.py` 直接 import，原先靠传递依赖）。

### 文档

- README / README.en / STATUS / QUALITY / API / DEPLOY / services README / apps README
  全部对齐到实测值（479 passed、91.2% 行、81.6% 分支、九种赛制、53 条赛制契约测试、10 道门禁）。

## [1.0.1] — 2026-10-24

### 修复

- CI 冒烟脚本的两条断言：首页文案已改为「把 AI 对抗做成节目」，原 grep「转播台」不再命中；
  `recap` 的 `problems` 是紧凑 JSON，`grep` 需用 `-F` 否则 `[]` 被当作字符类。
- 产品行为无变化。

### 文档

- 新增 `docs/BENCHMARK.md`：7 维度 × 6 项目的完整对标矩阵与采集命令。
- 新增 `docs/assets/social-card.png`（1280×640）与生成脚本 `scripts/make_social_card.py`。
- `apps/web/README.md`、`services/match/README.md`、`services/match/DESIGN.md` 同步到五页结构与当前模块。

## [1.0.0] — 2026-10-24

v1.0 的目标是把项目从"能演示"推到"能对外展示"：补齐观众入口、分享、统计与运维缺口，
修掉真实模型联调暴露的判罚缺陷，把所有声明对齐到实测值。

### 新增

- **首页与赛事库**：`GET /archive`（分页、按赛制/状态筛选）+ 五页统一导航与设计系统。
- **全站统计**：`GET /stats`（比赛数、事件数、赛制分布、胜者榜、近 14 天活动、在线人数）。
- **分享能力**：`GET /matches/{id}/card.png` 生成 1200×630 PNG；`GET /share/{id}`
  输出带 OG / Twitter 标签的落地页；前端「分享」按钮优先走 Web Share API。
- **真实在线人数**：`GET /matches/{id}/presence`，读数来自广播后端的订阅计数。
- **赛季赛制**：`POST /seasons` 支持 `game` 字段，赛季内所有对局使用同一引擎；
  旧库自动 `ALTER TABLE` 补列。
- **赛季操作**：`GET /seasons` 列表、前端「新建赛季」「推进赛程」。
- **开赛面板**：首页可直接选赛制、改选手名、填种子、接入 BYOK 真实模型。
- **剧场模式**：观战页 `F` 键隐藏侧栏与控制区。
- **分享与连播**：终局卡新增「看战报」「下一场」。
- **截图与录屏脚本**：`scripts/capture_gallery.py`（12 张）、`scripts/record_walkthrough.py`（46 秒 mp4）。

### 安全

- **剪辑下载路径穿越**：`match_id` 参与文件路径拼接，`..` / 盘符 / 分隔符可读走 SQLite 主库与系统文件；现由 `_safe_match_id` 拒绝（400）。
- **SSRF 漏 CGNAT / ULA**：`100.64.0.0/10` 与 IPv6 `fc00::/7` 现在同样拒绝，阿里云元数据 `100.100.100.200` 单独列出。
- **token 模式下 `/metrics` 公开**：指标端点纳入鉴权。
- **缺 CSP**：新增 `Content-Security-Policy`。

### 修复

- **赛季并发跑同一 fixture**：`run_season` 加数据库级赛季锁，冲突返回 409；调度器遇到锁跳过。
- **终局事件 TOCTOU**：`EventLog.append_terminal` 用 `BEGIN IMMEDIATE` 把「查重 + 插入 + 标记 ended_at」放进一个事务。
- **`mark_ended` 非幂等**：改为 `COALESCE(ended_at, ?)`，重试不再把结束时间往后推。
- **`create_match` 用 `INSERT OR REPLACE`**：重跑同一场会抹掉 `ended_at`，已结束比赛变回"直播中"；改为 `INSERT OR IGNORE`。
- **worker 轮询线程遇异常即死**：`_loop` 加自愈，`claim` 撞锁不再让 4 个线程全部退出。
- **续租线程遇锁冲突即死**：`renew` 加 3 次重试，`renew_loop` 捕获异常后下周期继续。
- **失租者写 `complete`/`fail`**：改为只允许当前 `lease_owner` 更新，避免把别人正在跑的任务标成 done。
- **重试次数用尽的 running 任务永久卡住**：新增 `workqueue.reap()`，每次轮询前回收。
- **赛季 `results` 顺序不稳定**：读取按 `fixtures.round` 排序，OpenSkill 评分不再因 `match_id` 字典序而变。
- **同 id 重建赛季混入旧选手**：`persist(..., replace=True)` 先清 roster/fixtures/results。
- **配额先扣后校验**：参数校验通过后才扣；渲染在确认有可剪内容后才扣；`/seasons/{id}/run` 按场数扣。
- **`inlineWorker` 绕过 `MELEE_INLINE_WORKER=off`**：服务端配置优先。
- **非法 JSON / 非法 startAt 返回 500**：改为 400。
- **赛季 404 留下孤儿比赛**：先校验赛季再写库。
- **直播中生成的分享卡被永久缓存**：结束后重新生成。
- **代号：对手翻掉你最后一个词不判胜**：现在该队直接获胜。
- **Redis 广播异常抛进事件写入**：广播失败降级为告警，事件照常落库。
- **`quotaRemaining` 语义错误**：改名为 `rateRemaining`（它是每分钟限流余量，不是每日配额）。
- **TextArena 观察一次性队列**：`observe()` 读空后 `legal_moves` 退化为格式提示，
  真实模型两次犯规弃权。改为在 `init` / `apply` 时抓取并缓存观察。
- **裸动作被判犯规**：棋类模型只回 `[4]` / `[col 3]` / `[0 2]` 时，现按"与 legal_moves
  逐字符相同"接受；其余仍严格拒绝。
- **取石子动作枚举不全**：只给"每堆取 1 个"，现枚举每堆 `1..n`。
- **弃权局战报自检误报**：`perPlayer` 只从 `action.applied` 推导，胜者可能不在名单里；
  改为以 `match.created` 的参赛名单为准。
- **崩溃后比赛卡在"直播中"**：worker 重试时若重放状态已 `finished` 但缺 `match.ended`，
  自动补写终局事件并标记 `ended_at`。
- **终局事件重复**：`Runner._finish` 先查后写，任务重试不再产生两条 `match.ended`。
- **429/5xx 直接记犯规**：LLM 选手加有限退避重试（默认 2 次，0.8s / 1.6s）。
- **token 模式下 `/metrics` 公开**：指标端点纳入鉴权。
- **缺 CSP**：新增 `Content-Security-Policy`（同源脚本、内联样式、`frame-ancestors 'none'`）。
- **移动端布局**：`body` 固定高度导致选手卡被裁；`display:contents` 后卡片均分高度留白。
- **已结束比赛无限重连**：前端在收到 `match.ended` 或快照已结束时主动关闭 SSE。
- **导播台空列表**：从 URL 打开的比赛会补进左侧列表；事件数改用总数而非最近 12 条。
- **赛事库伪随机柱状图**：换成"本场事件数 / 本页最大值"的真实比例条。
- **胜者榜混入团队名**：`red` / `blue` 不再进个人胜者榜。

### 变更

- 前端从 `index.html` 单页拆为 `index.html`（首页）+ `watch.html`（观战）；
  `?match=` 旧链接自动跳转，不破坏已有书签。
- 页面渲染统一走 `paint.js`：首次绘制前清空静态占位，之后走 lit-html 增量。
- 观战页顶栏重排，`matchId` 输入框与按钮不再撑破 52px 高度。

### 文档

- 重写 README：入口、截图、能力表、BYOK、API、质量、对标、边界。
- 新增 `docs/ARCHITECTURE.md`、`docs/API.md`、`docs/QUALITY.md`、`docs/ROADMAP.md`。
- `docs/screenshots/README.md` 更新为 12 张新截图与复现命令。

### 移除

- 旧的 10 张截图（被 `capture_gallery.py` 重新生成的一组替代）。

## [0.9.0] — 2026-10-23

### 变更

- 用成熟库替换手写实现：`limits`（限流）、`itsdangerous`（票据）、
  `prometheus-fastapi-instrumentator`（指标）、`APScheduler`（调度）、
  `pydantic-settings`（配置）、`lit-html`（模板）。
- worker 支持并发（默认 4 线程），慢对局不再阻塞新场次。

### 修复

- worker 长任务不续租导致同一场被两个副本重跑（出现两条 `match.ended`）。

## [0.8.0] — 2026-10-22

### 新增

- Dockerfile / compose / 健康检查 / Prometheus 指标。
- 许可证台账（NOTICE）与 `check_licenses.py`。
- GitHub Actions：test + docker 两个 job。

### 修复

- 存储型 XSS（选手名未转义）。
- `X-Forwarded-For` 轮换绕过限流。
- SQLite 并发写 `database is locked`。
- 管理接口未鉴权且回显 API Key。
- 视频下载无鉴权（改为媒体票据）。
- SSE 假直播（事件要等 15s 心跳才到）。
- 棋类零解说零镜头；代号战报自检恒失败；高光面板硬编码二十问语义。

## [0.7.0] — 2026-10-21

### 新增

- 赛制从 1 扩到 6：自研代号（4 人隐藏信息）+ TextArena 井字棋/四子棋/黑白棋/取石子。
- 赛季：OpenSkill 积分、循环赛程、宿敌。
- 赛程到点开赛（`scheduled_at`）。
- 前端按 `gameKind` 切换中央面板（二十问 / 代号 / 棋盘 / 堆条）。
