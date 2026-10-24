# 架构说明

## 1. 一句话

一个比赛服务：**引擎产生事实 → 事件日志保存事实 → 所有表现层从事实重算**。
直播、回放、战报、导播、高光、分享卡，全部是同一份事件的不同投影。

## 2. 分层

```
┌──────────────────────────────────────────────────────────────┐
│ 表现层  apps/web（零构建 ES module）                          │
│   index / watch / season / recap / director                  │
│   reducer: view.js（事件 → 视图，直播与回放共用）              │
└───────────────▲──────────────────────────────▲───────────────┘
                │ SSE / REST                   │ PNG / mp4
┌───────────────┴──────────────────────────────┴───────────────┐
│ 接入层  services/match/api.py                                 │
│   鉴权 · 配额 · 限流 · CORS/CSP · 静态前端 · 分享页 · 指标      │
└───────────────▲──────────────────────────────▲───────────────┘
                │                              │
┌───────────────┴──────────┐   ┌───────────────┴───────────────┐
│ 编排层                    │   │ 派生层                         │
│  runner.py  驱动对局      │   │  narrator.py  解说             │
│  worker.py  队列执行      │   │  director.py  自动导播         │
│  schedule.py 赛季推进     │   │  recap.py     战报/高光        │
│  control.py  人工切镜     │   │  render.py    视频             │
│                           │   │  card.py      分享卡           │
│                           │   │  archive.py   赛事库/统计      │
└───────────────▲──────────┘   └───────────────────────────────┘
                │
┌───────────────┴──────────────────────────────────────────────┐
│ 领域层  services/match/engine/                                │
│   twenty_questions（自研）· codenames（自研）                  │
│   textarena_adapter（井字棋/四子棋/黑白棋/取石子）              │
│   统一 Game 协议：init / observe / public_state /              │
│                   current_player / legal_moves / apply         │
└───────────────▲──────────────────────────────────────────────┘
                │
┌───────────────┴──────────────────────────────────────────────┐
│ 基础设施  eventlog.py（SQLite append-only）                    │
│           workqueue.py（租约队列）                             │
│           broadcast.py（内存/Redis）                           │
│           auth.py / secrets.py / outbound.py / ratelimit.py    │
└──────────────────────────────────────────────────────────────┘
```

依赖方向只有自上而下。领域层不 import 接入层，引擎不 import 日志。

## 3. 五条不变量

### 3.1 事件日志是唯一真相

`EventLog` 只做追加：seq 从 0 开始、严格递增、无空洞。写入时如果主键冲突
（多连接并发）就重读 `MAX(seq)+1` 再写，最多 8 次；这保证了"序号连续"这条契约
在 API、worker、导播台三个写者同时工作时仍然成立。

`assert_contiguous()` 是对这条不变量的运行时断言，测试里每条对局都会调用。

### 3.2 直播 = 追加，回放 = 前缀

前端只有一份 `reduce(view, event)`。直播时每来一条事件就 reduce 一次；
回放时从第 0 条 reduce 到游标位置。因为输入相同、函数相同，画面必然一致。

`test_api.py::test_seed_is_reproducible` 与 `test_games_parity.py` 锁住了这条。

### 3.3 导播决策也是事件

自动导播与人工切镜都写成 `director.cue` 事件。所以：

- 回放能看到当时的镜头序列；
- `apply_director(events)` 离线补齐的结果与直播逐条相等（六赛制参数化测试）；
- 人工切镜在 `control.py` 里重开连接写入，冲突时重试 seq。

### 3.4 引擎是纯函数

`Game.apply(state, player, move) -> MoveResult` 不读时钟、不联网、不用全局随机数。
随机只在 `init(seed)` 里出现一次。

TextArena 的 `env.step` 是就地修改的可变对象，适配器每次 `deepcopy` 一份再推进，
所以调用方持有的旧 state 永远不会被改。这一层是"纯函数"契约唯一的适配点。

### 3.5 统计不编数

- `/archive` 的比赛数、事件数来自 SQL；
- `/stats` 的胜者榜来自 `match.ended` 事件；
- `/presence` 的人数是 `Broadcaster.viewer_count()` 的真实订阅数；
- 首页的 14 天柱状图是真实的每日计数，没有数据的日子高度为 0。

## 4. 关键机制

### 4.1 写路径走队列

`POST /matches` 只做校验与入队，比赛由 worker 执行。好处：

- 请求不被长对局拖住；
- 副本崩了，租约到期后别的副本接手；
- 单机默认 `MELEE_INLINE_WORKER=on`，API 进程自己消费，开箱即用。

worker 领任务时用一条带条件的 `UPDATE` 抢租约；执行期间每 `lease/3` 续租一次。
不续租的话，一场跑 5 分钟的比赛会在第 180 秒被第二个副本重跑，产生两条 `match.ended`
（这是审计时真实发生过的缺陷，已由 `test_workqueue.py` 锁住）。

### 4.2 崩溃恢复

worker 重试时先用 `rehydrate()` 从日志重放，拿到"跑到哪了"：

- 状态未结束 → 接着跑；
- 状态已结束但缺 `match.ended` → `ensure_terminal()` 补写终局事件并标记 `ended_at`。

第二条是本轮修的：旧实现看到 `finished` 就直接跳过，比赛永远显示"直播中"。

### 4.3 隐藏信息

代号赛制里，队长的 `observe()` 返回 `secretColors`，队员不返回。
公开状态 `public_state()` 只暴露已翻开的颜色。事件流里不存在私有事件——
隐藏信息在"观察"这一步就被裁掉了，不依赖前端自觉。

### 4.4 解说事实校验

`LLMNarrator` 每句都过三层校验：

1. 形状：非空、≤ 60 字、不含胜负判定词；
2. 数字：句中每个数字都必须在事实 JSON 里出现过；
3. 语义：答案方向不能反、选手不能错位、动作类型不能张冠李戴。

任何一层不过就退回规则式解说。宁可朴素，不撒谎。

### 4.5 多副本

| 能力 | 单机 | 多副本 |
|---|---|---|
| 事件日志 | SQLite WAL | 需共享文件系统或换库（当前不支持） |
| 广播 | 内存 | `MELEE_BROADCAST=redis` |
| 限流 | 内存 | `MELEE_RATELIMIT=redis` |
| 票据 | 进程随机密钥 | 配 `MELEE_TICKET_SECRET`（无状态签名） |
| 队列 | 同库 | 同库天然共享 |
| 在线人数 | 本进程订阅数 | 各副本各报各的，未聚合 |

## 5. 为什么这样选

| 决策 | 原因 | 代价 |
|---|---|---|
| SQLite 而不是 Postgres | 单机自托管零依赖，WAL 足够撑"API + worker + 导播台"三写者 | 多副本需要共享存储 |
| 零构建前端 | 播放器状态 = 事件 + 游标，纯 reducer 足够；好看来自排版与节奏，不是组件树 | 复杂表单会啰嗦 |
| lit-html 而非 React | 8KB、模板自动转义、无构建 | 生态小 |
| 自研二十问与代号 | 规则即产品；TextArena 的对应环境依赖 LLM 主持，破坏确定性 | 赛制数量少 |
| 接 TextArena 棋类 | 规则成熟、判定确定，不重复造 | 受上游观察格式约束（已踩过一次性队列的坑） |
| 事件日志不换 Kafka | append-only + seq 是产品契约，SQLite 直接写最可控 | 单机上限 |

## 6. 前端结构

```
index.html   首页：hero、直播条、赛事库、赛制入口、统计
watch.html   观战：1600×900 舞台 + 侧栏 + 时间轴 + 控制条 + 剧场模式
season.html  赛季：积分榜、赛程、宿敌、新建/推进
recap.html   战报：一句话、数字、瞬间、选手数据、高光
director.html 导播：选台、镜头、切镜、实时事件
src/
  api.js     统一 REST 客户端、格式化、游戏元数据
  nav.js     顶栏与页脚（五个页面共用）
  paint.js   首次绘制前清空静态占位，之后走 lit-html 增量
  view.js    事件 → 视图（直播与回放共用）
  sse.js     EventSource 封装（含 token → ticket 交换）
  home.js / main.js / season.js / recap.js / director.js
```

## 7. 数据模型

```sql
matches(match_id PK, game, seed, players JSON, created_at, ended_at)
events(match_id, seq, type, player, at, payload JSON, PK(match_id, seq))
jobs(job_id PK, kind, payload JSON, status, attempts, max_attempts,
     lease_owner, lease_until, last_error, created_at, updated_at)
seasons(season_id PK, name, created_at, game, status)
rosters(season_id, player_id, display, persona, model, PK(season_id, player_id))
fixtures(season_id, round, match_id, player_a, player_b, scheduled_at,
         PK(season_id, round, player_a, player_b))
results(season_id, match_id PK, player_a, player_b, winner, reason)
quotas(token_name, day, matches, renders, PK(token_name, day))
```

`seasons.game` / `seasons.status` 是后加的列，`season.py::_migrate()` 用
`PRAGMA table_info` 判断后 `ALTER TABLE`，旧库升级不会崩。
