# HTTP API

所有路径都挂在服务根（默认 `http://127.0.0.1:8099`）。前端同源，不需要额外 CORS 配置。

- 鉴权：`MELEE_AUTH=token` 时，除 `/health` 与公开分享端点外都要 `Authorization: Bearer <token>`；
  公开分享端点指 `/share/{matchId}` 与 `/matches/{matchId}/card.png`（`MELEE_PUBLIC_SHARE=on`，默认），
  只暴露战报摘要；设成 `off` 则与其它接口一样要求鉴权；
- 配额：`/matches` 与 `/clips/render` 按 token 每日计数，超限返回 429；
- 限流：按 IP 分档（读 120/分、建场 10/分、渲染 5/分、SSE 30/分）；
- 错误：400/401/403/404/413/422/429/503，响应体统一 `{"detail": "..."}`。

## 观战

### `GET /matches/{matchId}/events?after=-1`

拉事件。`after` 是最后一条已收到的 seq，用于断线补齐。

```json
{"matchId": "m_ab12", "events": [...], "nextAfter": 41}
```

### `GET /matches/{matchId}/stream`

SSE 长连接。支持 `Last-Event-ID` 请求头（浏览器原生）与 `lastEventId` 查询参数
（前端自管重连时用，因为换票后的新 URL 带不上旧请求头），以及 `ticket` 查询参数（token 模式）。
每条事件的 `id` 是 seq，`data` 是完整事件 JSON；比赛结束时服务端主动关闭。

### `GET /matches/{matchId}/state`

跨副本可读的状态快照（从日志重放，不依赖进程内缓存）：

```json
{"matchId":"m_ab12","game":"twenty-questions","players":["A","B"],"finished":false,
 "currentPlayer":"A","events":24,"lastSeq":23,"remaining":7,"turnIndex":3}
```

建场后 worker 尚未写下第一条事件的窗口里返回 200 + `"pending": true`；
只有 `matches` 表里根本没有这个 id 才 404。

### `POST /matches` 的高级编队（多 AI / 多队）

`players` 现在支持 2–16 名选手；`config` 用来表达「不是红蓝两端」的编制：

```json
{
  "game": "codenames",
  "seed": 11,
  "players": [{"id": "AI-0"}, {"id": "AI-1"}, {"id": "AI-2"},
              {"id": "AI-3"}, {"id": "AI-4"}, {"id": "AI-5"}],
  "config": {"teams": 3, "teamSize": 2}
}
```

- `teams`：队伍数（2–8）。默认 2。
- `teamSize`：每队人数（2–8，含 1 名队长）。默认 2 → 经典 4 人。
- 3 队 × 2 人 = 6 个 AI；2 队 × 4 人 = 8 个 AI。
- 每支队伍都必须有**自己的词**（2 队沿用经典 9/8/7/1；3 队及以上均分），
  否则新增队伍只能靠翻别人的词获胜，是假的多队。
- 编队参数会写进 `match.created` 的 `config`，重放能重建同一场。

### 接入任意 AI（HTTP Agent）

除了内置脚本选手与 OpenAI 兼容模型，任何框架写的 AI 都可以通过 HTTP 参赛：

```json
{
  "game": "tic-tac-toe",
  "players": [
    {"id": "my-agent", "http": {"endpoint": "https://my-agent.example.com/act", "apiKey": "..."}},
    {"id": "baseline"}
  ]
}
```

平台会向 `endpoint` 发 POST：

```json
{"you":"my-agent","opponent":["baseline"],"isYourTurn":true,
 "observations":["..."],"legalMoves":["[4]","[5]"],
 "turnIndex":2,"gameState":{"board":[...]}}
```

`http` 块可选字段：

| 字段 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `timeoutS` | float | 30 | 1–600 | 单次出招等待上限（外部框架多步规划可放宽到 10 分钟） |
| `maxRetries` | int | 1 | 0–3 | 传输层重试次数 |
| `apiKey` | string | `""` | ≤500 字符 | 作为 `Authorization: Bearer` 发出；支持 `{env:VAR}` |

设置了 `config.turnTimeoutMs` 时，请求体额外带 `"deadlineMs": 30000`——
外部 agent 应在此预算内返回，超时由平台侧墙钟打断并按弃权计。

期望响应（两种格式都接受）：

```json
{"move": "[4]"}
```
```json
{"kind": "guess", "value": "[4]", "thought": "为什么走这步"}
```

安全与公平边界：
- 出站地址过 SSRF 白名单（拒绝回环、私网、云元数据）；
- 只转发 `observe()` 已裁剪的字段，**不会**带上 `target` / `secretColors` / 其他选手的私有事件；
- 超时、重试、响应截断与 LLM 选手同一套参数，不因"外部 AI"放宽；
- 配置 `MELEE_SECRET_KEY` 后，`apiKey` 与 BYOK 一样用 Fernet 封存后入队；缺少密钥时带外部 Key 的建场直接返回 503，不进事件、日志或队列。

### 用真实模型参赛（BYOK）与能力面参数

`players[].model` 让该选手用你的 OpenAI 兼容 / Anthropic / Gemini 端点出招。
除 `baseUrl`/`model`/`apiKey` 外全部可省略，**省略时行为与升级前完全一致**。

```json
{
  "game": "twenty-questions",
  "players": [
    {"id": "A", "model": {
      "baseUrl": "https://api.anthropic.com",
      "model": "claude-sonnet-4-5",
      "apiKey": "{env:ANTHROPIC_API_KEY}",
      "protocol": "anthropic",
      "temperature": 0.5,
      "maxTokens": 2000,
      "topP": 0.9,
      "seed": 42,
      "systemPrompt": "你是擅长排除法的侦探，优先问能对半切分候选的问题。",
      "includeHistory": true
    }},
    {"id": "B"}
  ],
  "config": {"turnTimeoutMs": 30000}
}
```

| 字段 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `baseUrl` | string | 必填 | ≤500 字符 | 端点根地址，过 SSRF 白名单 |
| `model` | string | 必填 | ≤200 字符 | 模型名 |
| `apiKey` | string | 必填 | ≤500 字符 | 支持 `{env:VAR}`，服务端解析 |
| `protocol` | string | `openai` | openai/anthropic/gemini | anthropic 走 `/v1/messages`，gemini 走 `:generateContent` |
| `temperature` | float | 0.7 | 0–2 | |
| `maxTokens` | int | 400 | 64–32000 | 单次回复上限 |
| `maxTotalTokens` | int | 60000 | 1000–2000000 | 单场预算，耗尽以"预算用尽"收尾 |
| `jsonMode` | bool | true | | 下发 `response_format`（仅 openai 协议） |
| `topP` | float | 不下发 | 0–1 | |
| `seed` | int | 不下发 | | 可复现采样 |
| `stop` | string[] | 不下发 | ≤4 条，每条 ≤64 字符 | |
| `frequencyPenalty` | float | 不下发 | −2–2 | |
| `presencePenalty` | float | 不下发 | −2–2 | |
| `stream` | bool | false | | openai 协议下把思考增量写进 `thought.delta` |
| `systemPrompt` | string | `""` | ≤4000 字符 | 只替换人格/策略段；合法动作与 JSON 格式约束仍由平台拼接 |
| `includeHistory` | bool | false | | 把**本选手自己**的历史（动作, 思考）拼进 prompt |

公平性：以上全部是「选手自带配置」，与 `temperature` 同地位；观察内容一个字不变，
`includeHistory` 只回放自己的动作与思考，不引入对手私有信息或隐藏字段。

`apiKey` 支持 `{env:VAR}`：worker 解封后从环境变量取值；变量缺失时该选手按明确
原因记弃权（不崩 worker、不打印变量值）。

### 回合墙钟：`config.turnTimeoutMs`

```json
{"game":"twenty-questions","players":[{"id":"A"},{"id":"B"}],
 "config":{"turnTimeoutMs":30000}}
```

- 范围 1000–600000 毫秒；**省略时不强制**（旧行为：只把 `deadlineMs` 写进 `turn.started`）。
- 设置后 runner 用墙钟真实强制：到点未返回，该手记 `action.rejected`（reason 含"超时"），
  按弃权走同一条 `action.applied` 路径，对手获胜。
- 值落进 `match.created.config`，重放与战报能回答「这一手为什么被判超时」。
- 该配置不进入赛制 `game.init`（平台层参数，不污染重放状态）。

### `GET /matches/{matchId}/view`

按完整事件日志重放并返回折叠后的公共视图。

### `GET /matches/{matchId}/view/as/{player}`

某位选手在当时的视角：返回该选手可见的事件子集与折叠后的视图。
直播期用它做"队长视角"回放，赛后用来看"他当时看到了什么"。

```json
{"matchId":"m_ab12","player":"红队队长","events":[...],"view":{"turns":[...]}}
```

选手不存在时 404；`visibility` 过滤在服务端完成，前端拿不到别人的私有事件。

### `GET /matches/{matchId}/events?reveal=1`

赛后揭秘：比赛**已经结束**时才返回私有事件（队长思考等），
直播期请求返回 403——隐藏信息赛制的看点就是直播期看不到。
不带 `reveal` 时行为与普通 `/events` 完全一致。

### `GET /matches/{matchId}/presence`

真实在线观赛人数（当前进程内该场的 SSE 订阅数）：

```json
{"matchId":"m_ab12","viewers":3}
```

多副本部署时每个副本各报各的，不做全局聚合（已在 README「已知边界」注明）。

### `GET /matches/{matchId}/recap`

战报：

```json
{"game":"codenames","winner":"blue","reason":"...","oneLine":"蓝队获胜：...",
 "turns":30,"asks":0,"guesses":30,"rejected":0,"moves":30,
 "perPlayer":{...},"highlights":[...],"problems":[],
 "timing":{"elapsedMsTotal":12840,"elapsedMsMax":2100,"tokensTotal":512,
           "slowestTurn":{"seq":19,"player":"A","elapsedMs":2100,"action":{...}}}}
```

`problems` 是服务端自检：非空表示战报里的某个数字在事件里对不上。

`timing` 只统计真的带了 `elapsedMs` 的动作：脚本选手没有网络延迟时
全是 0，前端据此隐藏整行（显示一排 0 比不显示更误导）。

### `GET /matches/{matchId}/card.png`

1200×630 PNG 分享卡。已结束的比赛直接复用磁盘缓存；直播中的每次重画。

### `GET /share/{matchId}`

分享落地页。含 `og:title` / `og:description` / `og:image` / `twitter:card`，
爬虫不需要执行 JS 就能取到预览；页面同时给出「进入观战页」按钮。

## 赛事库与统计

### `GET /archive`

| 参数 | 默认 | 说明 |
|---|---|---|
| `limit` | 24 | 1–100 |
| `offset` | 0 | 分页偏移 |
| `game` | — | 赛制 id，如 `codenames` |
| `status` | — | `live` / `ended` |
| `q` | — | 搜选手名或 matchId（LIKE，≤64 字符） |

```json
{"count": 10, "limit": 24, "offset": 0, "items": [
  {"matchId":"m_ab12","game":"codenames","gameLabel":"代号","players":["A","B","C","D"],
   "status":"ended","winner":"blue","reason":"...","events":255,
   "createdAt":"...","endedAt":"...","firstAt":"...","lastAt":"..."}
]}
```

### `GET /stats`

```json
{"matches":10,"finished":9,"live":1,"events":1086,
 "byGame":[{"game":"twenty-questions","label":"二十问","matches":3}],
 "topWinners":[{"player":"灰隼","wins":3}],"activity":[{"day":"2026-10-24","matches":10}],
 "viewers":0}
```

团队赛的 `red` / `blue` 不计入 `topWinners`（它们不是选手）。

## 建场

### `POST /matches`

```json
{
  "game": "tic-tac-toe",
  "seed": 2027,
  "maxQuestions": 21,
  "players": [
    {"id": "A", "delayMs": 1200},
    {"id": "B", "model": {"baseUrl": "https://api.example.com/v1",
                          "model": "gpt-4o-mini", "apiKey": "sk-...",
                          "maxTokens": 400, "maxTotalTokens": 60000}}
  ],
  "seasonId": null,
  "inlineWorker": true
}
```

- `players` 2–16 人，id 唯一，按赛制校验人数；
- `script` 选手可传 `["ask:place=海边", "guess:灯塔"]`，每条 ≤ 200 字符、≤ 200 条；
- `model` 是 BYOK，`baseUrl` 过 SSRF 白名单（拒绝回环/私网/元数据）；
- `maxTokens` 是单次回复上限（64–32000）；`maxTotalTokens` 是单场累计预算（1000–2000000，默认 60000），
  用尽时整场以「token 预算用尽」结束并写 `match.ended`；
- 返回 `matchId`、`seed`、`jobId`、`rateRemaining`（本分钟限流窗口余量，不是每日配额）。

写路径走队列：同一份代码可以在 API 进程内跑（`inlineWorker=true`），
也可以由独立 worker 进程消费。

### `GET /jobs` / `GET /jobs/{jobId}`

任务状态。`payload` 里的 `apiKey` 已递归脱敏成 `***`；生产环境必须配置 `MELEE_SECRET_KEY`，否则带外部 Key 的建场会在入队前返回 503。

### `GET /matches`

返回已发现的比赛 ID 列表；完整赛事筛选使用 `/archive`。

### `POST /auth/ticket`

在 token 模式下用 Bearer 换取 60 秒 SSE 票据：

```json
{"ticket":"...","ttlSec":60}
```

## 赛季

### `POST /seasons`

```json
{"seasonId":"autumn-2026","name":"2026 秋季赛","game":"tic-tac-toe",
 "players":[{"id":"A"},{"id":"B"},{"id":"C"}],
 "startAt":"2026-11-01T20:00:00+08:00","intervalMinutes":30}
```

- `game` 必须是 2 人赛制（循环赛按两人对局记分）；
- 不传 `startAt` 就只排对阵，可随时手动推进。

### `GET /seasons` / `GET /seasons/{id}`

`/seasons` 返回列表与进度；`/seasons/{id}` 返回积分榜、宿敌、赛程与全部结果。

### `POST /seasons/{id}/run?maxFixtures=n`

把**已到点且未完成**的场次跑完。返回 `played`、`skipped` 与每场结果。

## 导播

### `GET /director/matches`

正在进行的比赛（已过滤 `ended_at`，只对候选做重放）。

### `GET /director/{matchId}`

```json
{"matchId":"...","eventCount":41,
 "cues":{"auto":7,"manual":2},"recent":[...],"lastEvents":[...]}
```

### `POST /director/{matchId}/cue`

```json
{"shot":"close","target":"灰隼","durationMs":1200,"note":"关键提问"}
```

`shot` ∈ `wide` / `close` / `thought` / `reveal` / `slowmo`。写入事件流后
SSE 与回放都能看到。

## 高光

### `GET /games`

返回当前安装环境实际注册的赛制清单。内置赛制始终可用；TextArena 赛制只有安装可选依赖后才会出现。

### `POST /matches/{matchId}/media-ticket`

用 Bearer 换取绑定比赛、可复用 10 分钟的媒体票据：

```json
{"ticket":"...","ttlSec":600}
```

### `GET /matches/{matchId}/clips`

剪辑清单（不渲染）：每段的起止 seq、标题、时长。

### `POST /matches/{matchId}/clips/render?limit=3&perEventMs=900&template=broadcast&async=false`

同步渲染返回文件清单与 URL；`async=true` 入队并返回 `jobId`。
`template` 取 `broadcast` / `spotlight` / `scorecard`，未知值退回 `broadcast`。
没有 ffmpeg 时返回 503。

### `GET /matches/{matchId}/clips/{filename}`

下载 mp4。token 模式下需要 Bearer 或 `?ticket=`（媒体票据，10 分钟、绑定场次、可复用）。

## 运维

| 路径 | 说明 |
|---|---|
| `GET /health` | 探针。数据库与队列都可用才 200，否则 503 |
| `GET /ops/health` | 运维健康：任务积压 / 最老 pending / 失败率 / 卡住的直播。超阈值 503 |
| `GET /ops/metrics` | 同一批指标的 Prometheus 文本（`melee_ops_*`） |
| `GET /metrics` | Prometheus 文本（token 模式下需鉴权） |
| `GET /metrics/business` | 业务指标 JSON（事件数、任务数） |
| `GET /quota` | 当前 token 的当日用量 |
| `GET /scheduler` | 赛季调度状态 |
| `POST /scheduler/tick` | 手动推进一次调度 |

## 事件契约

事件类型与字段的单一真相源是 `packages/contracts/schema/battle-event.schema.json`。
`packages/contracts/scripts/codegen.py` 由它生成 pydantic 与 zod 类型，
`test_contract_parity.py` 保证三处一致。

事件类型：`match.created`、`match.started`、`round.started`、`turn.started`、
`thought.delta`、`action.proposed`、`action.rejected`、`action.applied`、
`state.changed`、`narration.line`、`director.cue`、`match.ended`。

### 私有事件（visibility）

事件可带 `visibility` 字段。`"public"`（默认）所有人可见；
`{"players": ["A"]}` 表示只有这些选手可见，服务端在 `/events` 与 SSE 出口过滤，
日志里仍保留全量（序号不产生空洞）。当前用于代号赛制的队长思考——
队长的配色推理不能播给观众，也不能让对手看到。
