# melee 转播台 · 服务端说明

本文件只讲**当前**实现：分层、模块职责、怎么扩展。
历史演进（第 N 批修复了什么）见 [CHANGELOG](../../CHANGELOG.md)；
实测指标与门禁命令见 [docs/QUALITY.md](../../docs/QUALITY.md)；
对外接口见 [docs/API.md](../../docs/API.md)。

## 1. 分层

```
HTTP 层      services/match/api.py（组合根）+ routes/（按业务域）
编排层       runner / worker / schedule / scheduler / control
派生层       narrator / director / recap / clips / render / card / archive
领域层       engine/（二十问、代号、TextArena 适配器 + 注册表）
基础设施     eventlog / workqueue / broadcast / auth / secrets / outbound / db / settings
```

依赖方向自上而下：领域层不 import 接入层，引擎不 import 日志。

## 2. 模块清单

### HTTP 层

| 模块 | 职责 |
|---|---|
| `api.py` | 组合根：CORS、安全响应头、指标鉴权、生命周期、挂载路由与静态前端 |
| `routes/common.py` | 共享前置：鉴权 + 限流 + 配额、ID 校验、事件读取、SSE 帧、时间解析 |
| `routes/matches.py` | 建场、事件、回放视图、战报、状态、列表、赛事库、统计、SSE |
| `routes/season_routes.py` | 赛季建/列/详情/推进 |
| `routes/media.py` | 剪辑清单、渲染、下载、分享卡、媒体票据 |
| `routes/director.py` | 直播中的比赛、镜头状态、人工切镜 |
| `routes/share.py` | 分享落地页（OG 标签） |
| `routes/system.py` | 健康探针、赛制清单、任务查询、调度状态 |
| `observability.py` | Prometheus 标准指标 + 业务指标 JSON |

新增端点：先判断属于哪个域，加到对应 `routes/*.py`；跨域共享逻辑放 `routes/common.py`。
**不要**再把端点写回 `api.py`。

### 编排与派生

| 模块 | 职责 |
|---|---|
| `runner.py` | 驱动一局：观察 → 动作 → 判定 → 事件；预算耗尽时正常收尾 |
| `worker.py` | 队列执行器：领任务、续租、崩溃续跑；内嵌/独立两种形态 |
| `workqueue.py` | 租约队列（pending/running/done/failed） |
| `schedule.py` | 赛季推进：按轮次跑完剩余 fixture，可断点续跑 |
| `scheduler.py` | APScheduler 定时推进（`MELEE_SCHEDULE` 开启） |
| `control.py` | 人工切镜写入事件流 |
| `narrator.py` | 规则式 / LLM 解说，三层事实校验 |
| `director.py` | 规则式自动导播（确定性、可复现） |
| `recap.py` | 战报、转折点、数字自检 `verify_summary` |
| `clips.py` / `render.py` / `card.py` | 剪辑清单 / mp4 渲染 / 分享卡 |
| `archive.py` | 赛事库与全站统计（全部来自 SQL 计数） |
| `rehydrate.py` | 从事件日志重建状态（跨副本读） |

### 领域层

| 模块 | 职责 |
|---|---|
| `engine/base.py` | `Game` 协议：init / observe / public_state / current_player / legal_moves / apply |
| `engine/registry.py` | 赛制注册表：内置 + TextArena，API 只认这里 |
| `engine/twenty_questions.py` | 自研二十问 |
| `engine/codenames.py` | 自研四人代号（隐藏信息靠 `observe()` 裁剪） |
| `engine/textarena_adapter.py` | TextArena 确定性环境适配器（当前 7 种） |
| `engine/textarena_probe.py` | 探测工具：判断一个上游环境能否当确定性赛制 |

新增赛制：实现 `Game` 协议 → 在 `registry` 注册 → 跑 `test_games_parity.py`
（对 `registry.available()` 全量参数化，自动要求解说/镜头/战报/渲染齐备）。

### 基础设施

| 模块 | 职责 |
|---|---|
| `eventlog.py` | append-only 日志，seq 连续；事件类型从 JSON Schema 派生 |
| `db.py` | 建表缓存：同一进程同一库只执行一次 DDL |
| `broadcast.py` | 内存 / Redis 广播 + 真实订阅计数 |
| `auth.py` | token 鉴权、日配额、无状态签名票据 |
| `secrets.py` | Fernet 封存队列里的 API Key |
| `outbound.py` | BYOK 出站地址校验（拒绝回环/私网/元数据） |
| `ratelimit.py` | 限流（`limits` 库，内存/Redis） |
| `settings.py` | pydantic-settings 单一配置入口 |

## 3. 五条不变量

1. **事件日志是唯一真相**：所有 UI 状态都能由 `reduce(events)` 重算。
2. **直播 = 追加，回放 = 前缀**：共用同一个 reducer，画面必然一致。
3. **导播决策也是事件**：人工切镜写进日志，回放逐条相等。
4. **引擎是纯函数**：`apply` 不读时钟、不联网、不用随机数。
5. **统计不编数**：`/stats`、`/archive`、`/presence` 全部来自 SQL 与真实订阅数。

## 4. 跑起来

```bash
python -m pip install -r requirements.txt   # 运行时
python scripts/serve.py                     # http://127.0.0.1:8099/
```

离线跑一局看事件时间线：

```bash
python -m services.match.demo --seed 7 --db data/match.db
```

常用调用：

```bash
# 建场（不写 script 就是贪心选手）
curl -X POST http://127.0.0.1:8099/matches \
  -H "Content-Type: application/json" \
  -d '{"game":"twenty-questions","seed":7,"players":[{"id":"A"},{"id":"B"}]}'

# 增量拉事件 / 折叠视图 / SSE 续传
curl "http://127.0.0.1:8099/matches/<matchId>/events?after=10"
curl "http://127.0.0.1:8099/matches/<matchId>/view"
curl -N "http://127.0.0.1:8099/matches/<matchId>/stream" -H "Last-Event-ID: 24"
```

## 5. 测试与门禁

```bash
python -m pip install -r requirements-dev.txt
bash scripts/verify_all.sh          # 10 道门禁，全过打印 ALL GREEN
python -m pytest services/match/tests -q
```

`test_architecture.py` 锁住三条结构约束：事件类型只有一个来源、
每个事件日志实例独占写锁、对外路由清单不被悄悄增删。

## 6. 已知边界

1. **鉴权是静态 token，不是账号体系**：够防"别人白用额度"，不够多租户。
2. **SQLite 单文件**：多副本部署需要共享存储；广播与限流可切 Redis，事件日志不行。
3. **凭据加密依赖进程密钥**：`MELEE_SECRET_KEY` 泄漏等于密文可解，这不是 KMS。
4. **语义校验是启发式的**：拦得住方向说反、角色错位、动作张冠李戴，拦不住复杂错误归因。
5. **`visibility` 已进契约、过滤已实现**：事件流尚无私有事件，隐藏信息靠 `observe()` 裁剪。
6. **前端零构建**：五个页面都是原生 ES module，没有构建链。
7. **在线人数是单进程订阅数**：多副本各报各的，没有全局聚合。
