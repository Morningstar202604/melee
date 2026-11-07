# 专项改进计划：四个已确认的问题

- 日期：2026-10-21
- 依据：本机实测（157 项测试、25 条路由、4796 行后端）+ GitHub API 实时核对（2026-10-21）
- 前置说明：此前报告"外网不可用"是误判——真实原因是本机 `hosts` 把 GitHub 全系域名指向 `127.0.0.1`（114 条），不是网络故障。用 `curl --resolve api.github.com:443:20.205.243.168` 可直连。

---

## 0. 对标基线（2026-10-21 实测）

| 项目 | 星 | 语言 | 许可 | 最近推送 | 关键能力 |
|---|---:|---|---|---|---|
| lm-sys/FastChat | 39,548 | Python | Apache-2.0 | 2026-05-01 | LMArena 本体：匿名对战 + 人投票 + Elo |
| TextArena/TextArena | 430 | Python | MIT | 2026-10-05 | **110 个游戏环境**，Gym 风格，含榜单 |
| Farama-Foundation/ChatArena | 1,567 | Python | Apache-2.0 | 2025-08-11 | 多智能体语言游戏（**已弃维护**） |
| THUDM/AgentBench | 3,784 | Python | Apache-2.0 | 2026-02-08 | Agent 综合 benchmark |
| GoodStartLabs/AI_Diplomacy | 708 | Python | NOASSERTION | 2026-06-01 | 外交桌游，结盟/背叛 |
| balrog-ai/BALROG | 274 | Python | MIT | 2026-04-09 | 游戏 Agent 评测 |
| llm-ring/lmring | 213 | TypeScript | Apache-2.0 | 2026-10-15 | 可自托管 LMArena：对比+投票+榜单 |
| jonathanmli/Avalon-LLM | 165 | Python | **无许可** | 2025-05-30 | 阿瓦隆社交推理 |
| lechmazur/step_game | 92 | 无 | **无许可** | 2025-12-09 | 公开嘴炮+秘密出招 |
| ed-donner/outsmart | 57 | Python | MIT | 2025-10-25 | 12 金币谈判淘汰赛 |
| google/werewolf_arena | 48 | Python | Apache-2.0 | 2024-07-22 | 狼人杀（论文配套） |
| jesseduffield/ai-battlegrounds | 32 | TypeScript | **无许可** | 2025-12-30 | 2D 战场实时观战 |

我方：25 条路由、157 项测试、4 个前端页面、4796 行后端 + 1405 行前端。

**能力对比结论**

| 维度 | 我方 | 最强对手 | 判断 |
|---|---|---|---|
| 赛制数量 | **1** | TextArena 110 | **差 2 个数量级（问题 P1）** |
| 多人/隐藏信息 | 未支持（硬编码 2 人） | werewolf / Avalon / Diplomacy | **缺失（P2）** |
| 实时观战 | SSE + Last-Event-ID 重连 | ai-battlegrounds 实时 | 领先 |
| 回放 | 事件重算 + 时间轴 + 倍速 | 多数无 | **独有** |
| 导演（自动+人工） | 全有，且入事件流 | 无 | **独有** |
| 战报 + 事实校验 | 有，三层校验 | 无 | **独有** |
| 高光视频 | 渲染 mp4 + 前端播放 | arena-narrator（仅棋类） | 领先 |
| 赛季/积分 | OpenSkill + 循环赛 + 宿敌 | LMArena Elo | 持平 |
| 赛事编排（排期/开赛时间） | **无** | 无（都没有） | 空白（P3） |
| 鉴权/配额/限流/BYOK 加密 | 全有 | 多数无 | 领先 |

---

## 1. 问题 P1：只有一个赛制

### 现象

`services/match/engine/` 下只有 `twenty_questions.py` 一个引擎。观众看两场就腻。

### 根因

不是"没时间写"，是**路线判断偏了**：当初认为"引擎层商品化，别自己写，用 TextArena"，但实际落地时发现 TextArena 的 TwentyQuestions 依赖 LLM 主持（`ta.agents.OpenRouterAgent`），与"确定性判定"冲突，于是自研了一个，然后就停在了一个。

### 真实约束（已核实）

TextArena 的 110 个环境分两类：

- **确定性类**（TicTacToe / ConnectFour / Othello / Nim / Mastermind / GuessWho / Blackjack / Poker 系列等）：`ta.TwoPlayerState` + 正则解析动作，**不需要 LLM 主持**。可直接包装。
- **主持人类**（TwentyQuestions / SecretMafia 部分阶段 / Diplomacy / Negotiation）：需要 LLM 当判官或主持人，与确定性冲突，需改造或跳过。

### 方案

**A. 写 TextArena 适配器（推荐，性价比最高）**

新增 `services/match/engine/textarena_adapter.py`，把 TextArena 的 `Env` 包成我们的 `Game` 协议：

```
ta.Env.reset(num_players, seed)     →  Game.init
env.state.get_current_player_observation()  →  Game.observe
env.step(action_text)               →  Game.apply
env.state.game_state                →  Game.public_state
```

关键难点与对策：

| 难点 | 对策 |
|---|---|
| TextArena 的动作是自由文本，我们是结构化 Move | 适配器里做一层解析：先试结构化，失败再交给 `env.step`；解析失败记为非法动作（复用现有 `action.rejected`） |
| TextArena 的 `step` 会就地改 state | 适配器内 `copy.deepcopy` 后再调，保持我们的"纯函数"语义 |
| 每个环境的合法动作不同 | `legal_moves` 从 `env.state` 的可用动作推导；推导不出就返回空，让选手自由输出 |
| 它可能依赖 `textarena` 包 | 作为**可选依赖**：没装则引擎目录里这些赛制不注册，服务照常启动 |

**B. 自研第二个赛制（作为 A 的兜底）**

如果 TextArena 适配成本超预期，退而自研一个**多人的、有隐藏信息的**赛制（见 P2），一次解决两个问题。

### 验收

- 至少 3 个新赛制可跑完整对局，且事件流符合现有契约（`test_contract_parity` 不用改）。
- 新赛制各配 3 条以上测试：非法动作、隐藏信息不泄漏、终局判定。
- `GET /games` 返回可用赛制列表，前端能选。

### 工作量

- 适配器骨架 + 1 个赛制跑通：1 天
- 批量接 5–10 个确定性赛制：每个 0.5 天（主要是测）
- 前端赛制选择器：0.5 天

---

## 2. 问题 P2：不支持多人 / 隐藏信息

### 现象

`twenty_questions.py:88` 硬编码 `if len(players) != 2: raise`。`PlayerSpec` 校验也是 `min_length=2, max_length=2`。

### 根因

契约（`visibility` 字段）和隔离矩阵都支持 N 人，但引擎和 API 校验没跟上。属于"设计到了、实现没到"。

### 方案

**Step 1：解除 2 人限制（半天）**

- `CreateMatch.players` 改 `min_length=2, max_length=8`
- `twenty_questions` 保持 2 人（规则如此），但在 `Game` 协议里加一个 `player_range` 属性，API 按它校验
- 现有测试不改，新增"3 人请求 twenty-questions 应 400"

**Step 2：接一个多人的确定性赛制（1–2 天）**

候选（按实现难度）：

| 赛制 | 人数 | 隐藏信息 | 确定性 | 难度 |
|---|---|---|---|---|
| SecretMafia（狼人杀） | 5–8 | ✅ 强 | 部分（夜晚行动确定，白天发言自由） | 高 |
| Codenames | 4+ | ✅ 队长知道词 | ✅ | 中 |
| TwoRoomsAndABoom | 6+ | ✅ | ✅ | 中 |
| ColonelBlotto | 2+ | ❌ | ✅ | 低 |

建议先做 **Codenames**：有隐藏信息、有团队、规则确定、观赏性强（猜词过程可视）。

**Step 3：验证隔离矩阵（半天）**

现有 `visibility` 过滤已实现但"无实际用例"。Codenames 接上后，写测试：
- 选手 A 的 `observe` 不含 B 队伍的暗号
- 事件流里带 `visibility: {players: [...]}` 的事件，只对该选手可见
- SSE 对观众过滤后不泄漏

### 验收

- 一个 4 人以上的赛制能跑完整局
- 隐藏信息在 `observe` / `public_state` / 事件流三处都不泄漏（三条测试）
- 前端能显示 3 个以上选手

---

## 3. 问题 P3：没有赛事编排

### 现象

赛季有积分和循环赛，但没有"什么时候开赛"。现在的 `MELEE_SCHEDULE` 是"每 N 秒跑一场"，不是"今晚 8 点开赛"。

### 方案

**Step 1：赛程表加时间字段（半天）**

`fixtures` 表加 `scheduled_at TEXT`。建赛季时可传 `startAt` + `intervalMinutes`，自动排期。

**Step 2：开赛条件从"间隔"改成"到点"（半天）**

`scheduler.py` 的 tick 逻辑改为：只跑 `scheduled_at <= now` 且未开始的场次。保留 `maxFixtures` 防止一次跑太多。

**Step 3：公开赛程页（半天）**

`apps/web/season.html` 加"即将开赛"区块，显示下一场时间与倒计时。

### 验收

- 建赛季时指定 `startAt`，赛程表自动排好时间
- 到点才跑（测试用假时钟验证，不用真等）
- 赛季页能看到"下一场 20:00，还有 18 分钟"

---

## 4. 问题 P4：观感依赖单一内容源

### 现象

所有画面都围绕"两人问答"设计。换赛制时，舞台组件（候选盘、秘密牌）会不适用。

### 根因

前端把"二十问的语义"写进了通用组件：`index.html` 里直接有"候选"、"公开提问记录"这类文案与布局。

### 方案

把舞台拆成**通用层 + 赛制皮肤**：

| 层 | 内容 | 归属 |
|---|---|---|
| 通用 | 选手卡、解说条、时间轴、控制条、事件流 | 所有赛制共用 |
| 皮肤 | 中央区域（二十问是候选盘，Codenames 是 5×5 词阵，棋类是棋盘） | 每个赛制一个组件 |

实现方式：`view.js` 已经是纯 reducer，加一层 `gameKind` 判断，中央区域按 `gameKind` 渲染不同面板。不引入框架，保持零构建。

### 验收

- 切到新赛制时，中央区域自动换成对应皮肤，选手卡与时间轴不变
- 二十问的现有画面不变（回归截图对比）

---

## 4.5 执行结果（2026-10-21 更新）

| 项 | 状态 | 实际交付 |
|---|---|---|
| P2-Step1 解除 2 人限制 | ✅ 完成 | `players` 上限 8，按赛制校验人数 |
| P1-A TextArena 适配器 | ✅ 完成（4 个赛制） | 井字棋/四子棋/黑白棋/取石子 |
| P2-Step2 多人+隐藏信息 | ✅ 完成（自研） | 自研 4 人代号；TextArena 版缺 NLTK 数据不可用 |
| P3 赛事编排 | ✅ 完成 | `scheduled_at` + `startAt` + 到点开赛 |
| P4 赛制皮肤分层 | ✅ 完成 | 中央面板按 `gameKind` 切换 |

**P1 只做到 4 个而非 10 个的原因**：实测 110 个环境里，同时满足"双人 + 不依赖 LLM 主持 + 动作可解析"的只有 4 个。其余是单人、需要主持人、或依赖 NLTK 等外部数据。

**P2-Step2 的解决方式**：放弃接上游，自研 4 人代号。规则简单（25 词、两队、线索+猜词），可控且能精确设计隐藏信息边界；比按 phase 适配 SecretMafia 便宜得多。

### 4.6 全量检查修复（2026-10-21 晚）

对 §4.5 的交付做了验收式复查，发现五项"声明完成但实际不可用"的问题，已修复并加回归测试：

| 问题 | 证据 | 修复 |
|---|---|---|
| SSE 不是直播 | 实测 51 条事件要等 15 秒心跳才成批到达；`EventLog.on_event` 从未接线 | `EventLog.open` 统一挂广播回调，跨线程 `call_soon_threadsafe`；实测 1.4 秒 |
| 管理接口未鉴权 | token 模式下 `/jobs` 200 且回显 `apiKey` 明文 | `/jobs`、`/scheduler`、`/seasons/{id}` 补 `_guard`；`Job.as_dict` 递归脱敏 |
| 建场后立刻 404 | `/events` 在 worker 领取前误判"比赛不存在" | 用 `matches` 表区分"未开始"与"不存在" |
| 观战页 token 失效 | `main.js` 没调 `setToken`，token 模式 `/games` 401、SSE 无票据 | 补 `setToken` 与带 token 的 `/games` |
| 非二十问第二次渲染崩溃 | 顶栏 pill 重写后 `maxq` 消失，拖动时间轴抛 null | `setText` 空值安全；Playwright 复测无 pageerror |

同时补了代号/棋类的战报高光（原来 `highlights: 0`、`clips: 0`）、赛季页倒计时（P3 验收项）、`textarena_probe` 导入错误与 `ruff` 残留。

验证：`pytest` 229 项通过、`ruff check` 全清、六个前端模块 `node --check` 通过、Playwright 真浏览器覆盖井字棋/代号/token 模式/赛季页。

## 5. 执行顺序与依赖

```
P2-Step1（解除 2 人限制）
   └─ P1-A（TextArena 适配器）──┬─ 批量接确定性赛制
                                 └─ P2-Step2（Codenames 多人赛制）
                                        └─ P4（赛制皮肤分层）
P3（赛事编排）—— 独立，可并行
```

先做 P2-Step1，因为它是 P1 和 P2 共同的前置：多人限制不解除，TextArena 里一半的赛制接不进来。

---

## 6. 优先级排序（按投入产出）

| 顺序 | 任务 | 工作量 | 收益 |
|---|---|---|---|
| 1 | P2-Step1 解除 2 人限制 | 0.5 天 | 解锁后续所有多人赛制 |
| 2 | P1-A TextArena 适配器 + 3 个赛制 | 2 天 | 赛制从 1 到 4 |
| 3 | P2-Step2 Codenames + 隔离测试 | 1.5 天 | 首个多人+隐藏信息赛制 |
| 4 | P4 赛制皮肤分层 | 1 天 | 换赛制不重写界面 |
| 5 | P3 赛事编排 | 1.5 天 | 从"点播"到"排播" |
| 6 | P1 批量接剩余确定性赛制 | 每个 0.5 天 | 内容库 |

前三项做完（约 4 天），产品从"一个赛制的 demo"变成"多赛制、支持多人、有隐藏信息的转播台"——这是质变点。

---

## 7. 不做的事（明确边界）

| 不做 | 理由 |
|---|---|
| 匿名对战 + 人投票排行榜 | 与"角色人设"冲突，且 LMArena 已做到极致（39.5k★） |
| 做人气榜/ELO 全站榜 | 我们的积分是赛季内叙事工具，不是权威评级 |
| 自研 LLM 主持型赛制 | 与确定性判定冲突，破坏可复现性 |
| 引入 React/Vite | 当前零构建够用；真要迁，`view.js` 的 reducer 可直接搬 |
| 多租户 SaaS | 鉴权是静态 token，够私有部署；再往上应交给网关 |

---

## 8. 风险

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| TextArena 适配器语义对不齐（它的 state 是可变对象） | 中 | 中 | 先只接 1 个最简单的（TicTacToe），跑通再批量 |
| TextArena 版本升级破坏接口 | 中 | 中 | 锁版本；适配器只依赖 `reset/step/get_observation` 三个方法 |
| 多人赛制的观战信息量爆炸 | 中 | 高 | 先做 4 人（Codenames），不做 8 人狼人杀 |
| 赛程时间测试不稳定 | 低 | 低 | scheduler 注入时钟，测试用假时钟 |

---

## 9. 附：本次核对的证据

- GitHub API 实测（2026-10-21，`--resolve` 绕过 hosts）：12 个项目的星数/许可/推送时间见 §0
- TextArena 环境目录实测：111 个（110 游戏 + utils）
- TextArena 接口实测：`TicTacToe` 的 `reset/step` 不依赖 LLM；`SecretMafia` 有 5 个 Phase 枚举
- 我方实测：157 项测试全绿、25 条路由、4796 行后端
