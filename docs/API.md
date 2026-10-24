# HTTP API

所有路径都挂在服务根（默认 `http://127.0.0.1:8099`）。前端同源，不需要额外 CORS 配置。

- 鉴权：`MELEE_AUTH=token` 时，除 `/health` 外的接口都要 `Authorization: Bearer <token>`；
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

SSE 长连接。支持 `Last-Event-ID` 请求头与 `ticket` 查询参数（token 模式）。
每条事件的 `id` 是 seq，`data` 是完整事件 JSON；比赛结束时服务端主动关闭。

### `GET /matches/{matchId}/state`

跨副本可读的状态快照（从日志重放，不依赖进程内缓存）：

```json
{"matchId":"m_ab12","game":"twenty-questions","players":["A","B"],"finished":false,
 "currentPlayer":"A","events":24,"lastSeq":23,"remaining":7,"turnIndex":3}
```

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
 "perPlayer":{...},"highlights":[...],"problems":[]}
```

`problems` 是服务端自检：非空表示战报里的某个数字在事件里对不上。

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
                          "model": "gpt-4o-mini", "apiKey": "sk-...", "maxTokens": 400}}
  ],
  "seasonId": null,
  "inlineWorker": true
}
```

- `players` 2–8 人，id 唯一，按赛制校验人数；
- `script` 选手可传 `["ask:place=海边", "guess:灯塔"]`，每条 ≤ 200 字符、≤ 200 条；
- `model` 是 BYOK，`baseUrl` 过 SSRF 白名单（拒绝回环/私网/元数据）；
- 返回 `matchId`、`seed`、`jobId`、`quotaRemaining`。

写路径走队列：同一份代码可以在 API 进程内跑（`inlineWorker=true`），
也可以由独立 worker 进程消费。

### `GET /jobs` / `GET /jobs/{jobId}`

任务状态。`payload` 里的 `apiKey` 已递归脱敏成 `***`。

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

### `GET /matches/{matchId}/clips`

剪辑清单（不渲染）：每段的起止 seq、标题、时长。

### `POST /matches/{matchId}/clips/render?limit=3&perEventMs=900&async=false`

同步渲染返回文件清单与 URL；`async=true` 入队并返回 `jobId`。
没有 ffmpeg 时返回 503。

### `GET /matches/{matchId}/clips/{filename}`

下载 mp4。token 模式下需要 Bearer 或 `?ticket=`（媒体票据，10 分钟、绑定场次、可复用）。

## 运维

| 路径 | 说明 |
|---|---|
| `GET /health` | 探针。数据库与队列都可用才 200，否则 503 |
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
