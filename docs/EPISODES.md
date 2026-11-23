# 外部对局导入

melee 不只跑自己的比赛，也可以把别的 arena 打过的对局接进来转播。
导入的场次是**只读回放**：不重跑引擎、不重新判定胜负，外部事实直接投影成
melee 事件流，随后直播、回放、导播、战报、高光全部复用现有链路。

## 三种输入

| 格式 | 来源 | 识别方式 |
| --- | --- | --- |
| `melee.episode/1` | 任意 runner / 自写导出 | `format` 字段或 `steps` 列表 |
| `kaggle` | Kaggle Game Arena episode | `configuration` + `info` / `steps` |
| `textarena` | TextArena 捕获的 `turns` 快照 | `format: textarena` 或 `turns` 列表 |

`auto` 会按字段自动识别；识别不了时显式传 `--format` / `?format=`。

## 通用格式

最小结构：

```json
{
  "format": "melee.episode/1",
  "source": "my-runner",
  "episodeId": "run-001",
  "game": "tic-tac-toe",
  "players": ["A", "B"],
  "seed": 7,
  "steps": [
    {"player": "A", "action": "[4]", "board": [["·","·","·"],["·","O","·"],["·","·","·"]]},
    {"player": "B", "action": "[2]", "board": [["·","·","X"],["·","O","·"],["·","·","·"]]}
  ],
  "winner": "A",
  "reason": "三子连线"
}
```

字段：

- `player` 可以是选手名、席位序号（0-based）或 `"Player 0"` 这类带尾号的字符串；
- `action` 可以是字符串、数组或对象；对象会取 `value` / `action` / `move`；
- `board` 可以是二维数组、行字符串数组、扁平数组（3/4/5/8/9/19 等平方尺寸），
  也可以直接给 `fen`；
- `thought` 会变成 `thought.delta`，进入观战页的“内心戏”；
- `at` / `timestamp` 可选，不合法时用导入时刻，不会因为一个时间戳整场失败；
- `winner` 可省略；省略时战报按平局/未知处理；
- `finalBoard` 可省略，默认取最后一手的棋盘。

## Kaggle

支持两种文件形态：

1. Kaggle API / notebook 里裸的 episode；
2. 从数据集下载的 `{"root": {...}}` 包装体，导入器会自动解包。

真实 Kaggle 棋局的关键字段不在顶层，而在 `root.configuration`、`root.info`
和 `root.rewards`：

- 赛制：`configuration.openSpielGameName`，例如 `chess`；
- 选手：`info.TeamNames`；
- 每手：`steps` 里带 `actionString` 的条目（`e4`、`Nf3`），不是 `submission=-1`
  的 setup 行；
- 每手之后的棋盘：`info.stateHistory`，第一项是初始局面，长度是动作数 + 1；
- 胜负：`rewards`（`[1, -1]` 表示 0 号位胜，`[0, 0]` 是平局）。

动作方用 `observation.playerId` 判定，而不是数组下标：Kaggle 的席位会被
seed 旋转，同一组里既有对手的观察，也有行动方的 `action`。模型当时的回答
在 `action.generate_returns[*].main_response`，会作为 `thought.delta` 保留。

只有 `info.actionHistory`、没有可读 `steps` 时也能导入。导入器会用前后两张
`stateHistory` FEN 逐个尝试合法着法，把 open_spiel 的数值 ID 还原成 SAN
（例如 `2426` → `e4`），并记下起止格给棋盘高亮。开发环境里用 `python-chess`
辅助反推 SAN 与合法性；它不进运行时镜像（GPL），线上没有它时仍能从 FEN 画棋盘。
本机不需要 `pyspiel`。

`observationString` 里的 FEN 会转成 8×8 棋盘；国际象棋的 `stateHistory`
本身就是 FEN，导入后每一手都能看到局面变化。

`examples/episodes/kaggle-real-chess.json` 是 `kaggle/chess-text-gameplay`
的真实 episode（CC BY 4.0），用来验收 `root`、`stateHistory` 和
`generate_returns` 这条完整链路，不是手写样例。

## TextArena

导入器接受带 `turns` 的快照：

```json
{
  "format": "textarena",
  "game": "tic-tac-toe",
  "players": ["A", "B"],
  "state": {"game_state": {"board": [["O","·","X"],["·","O","·"],["X","·","O"]]}, "rewards": {"0": 1, "1": -1}},
  "turns": [
    {"player": 0, "action": "[4]", "board": [["·","·","·"],["·","O","·"],["·","·","·"]]}
  ]
}
```

裸 `state.logs` 没有动作边界，无法可靠还原每一手；导入器会明确报错，
不猜。需要 TextArena 侧在捕获时同时记录 `turns`。

## HTTP

```bash
curl -X POST \
  'http://127.0.0.1:8899/matches/import-episode?format=auto' \
  -H 'Content-Type: application/json' \
  --data @examples/episodes/tic-tac-toe.json
```

也接受包装体：

```json
{"format": "kaggle", "episode": {"configuration": {}, "info": {}, "steps": []}}
```

返回：

```json
{
  "matchId": "m_...",
  "events": 31,
  "source": "kaggle-game-arena",
  "game": "chess",
  "players": ["Gemini", "GPT-5.6"],
  "winner": "GPT-5.6",
  "episodeId": "demo-chess-1"
}
```

## CLI

```bash
python3 -m services.match.cli match import-episode \
  examples/episodes/kaggle-chess.json \
  --format kaggle \
  --api http://127.0.0.1:8899 \
  --lang zh \
  --open
```

`--open` 会直接打开 `watch.html?match=<id>`。

## 批量导入

真实数据集动辄几千场，单文件入口不适合做验收。批量命令接受目录或文件；
不想先手工下载时，用 `--dataset owner/slug` 让 `kagglehub` 解析缓存：

```bash
python3 -m services.match.cli match import-batch \
  --dataset kaggle/chess-text-gameplay \
  --format kaggle --limit 100 --sample hash \
  --license 'CC BY 4.0' \
  --via-api --api http://127.0.0.1:8899 --token "$MELEE_TOKEN"
```

`--dataset` 自动补 `sourceUrl=https://www.kaggle.com/datasets/<owner>/<slug>` 和
`sourceLabel=Kaggle · <owner>/<slug>`；许可证仍从 `--license` 显式传入，不替数据集猜。

```bash
python3 -m services.match.cli match import-batch \
  /path/to/chess-text-gameplay \
  --pattern '*.json' \
  --format kaggle \
  --limit 100 \
  --sample hash \
  --via-api --api http://127.0.0.1:8899 --token "$MELEE_TOKEN" \
  --license 'CC BY 4.0' \
  --source-url 'https://www.kaggle.com/datasets/kaggle/chess-text-gameplay' \
  --source-label 'Kaggle Game Arena · chess-text-gameplay' \
  --out kaggle-batch-report.json
```

- `--dataset owner/slug` 与本地路径二选一；它调用 `kagglehub.dataset_download`，
  已缓存时不会重复下载，并把数据集 URL/标签写进出处；
- `--limit` 限制场次；`--sample hash` 是默认值，按
  `sha256(seed:path)` 打散后取样，避免总取文件名最前面的同一批模型；
  `--sample random` / `--sample first` 分别对应纯随机与旧行为；
  `--shuffle SEED` 仍可复现地打乱；
- `--via-api` 逐场走正式 HTTP 入口，token、`matches` 配额和限流都生效；
  不传时才是本地直写 SQLite（适合离线、单机、没有服务进程的场景）；
- 一场失败不会中断整批，报告里逐场记录 `imported` / `duplicate` / `failed`、
  `bytes`、`source` 和失败原因；顶层给出 `sources`、总字节数与许可证字段，方便复核；
  日配额耗尽会立即停止，不做无意义重试；
- `--db` 默认读 `MELEE_DB`，只在直连模式使用；
- `--open-first` 打开第一场导入成功的观战页；
- 退出码 `0` 表示全部成功，`2` 表示至少一场失败，`1` 表示路径/参数错误。

单场 API 也支持 `Content-Length` 预检（>8MB 直接 413）、`metadata` 包装体，
以及重导入幂等：同一 `(source, episodeId)` 不扣第二次配额。

## 导出成标准棋谱

国际象棋场次可以直接导出 PGN，交给 Lichess、Stockfish、ChessBase 等工具：

```bash
python3 -m services.match.cli match export <match-id> --format pgn --out game.pgn
```

`GET /matches/{id}/export?format=pgn` 返回 `application/x-chess-pgn`；默认 JSON 导出不变。
PGN 头部记录 `Event`、`Site`、`GameId`、`License` 和 `Annotator`，每一步的内心戏写成
PGN comment；`Result` 来自 `match.ended`，直播棋局写 `*`。日期保留 `????.??.??`，
因为 Kaggle 数据没有可信的绝对比赛日期。导出只接受 SAN；测试套件再用 `python-chess`
把 PGN 读回来，确认着法数与终局局面。

## 实时外部对局

赛后文件不是唯一入口。外部 arena 可以在自己的回调里把「到目前为止的 steps」
反复 POST 到实时桥，melee 会建场并增量追加，不需要等比赛结束：

```bash
# 第一帧：建场并开播
curl -X POST 'http://127.0.0.1:8899/matches/import-episode/live?format=auto' \
  -H 'Content-Type: application/json' \
  -d '{"format":"kaggle","episode":{...},"finished":false,"metadata":{"license":"CC BY 4.0","sourceUrl":"https://..."}}'

# 后续帧：同样的 episodeId，steps 只增不减
curl -X POST 'http://127.0.0.1:8899/matches/import-episode/live?format=auto' \
  -H 'Content-Type: application/json' \
  -d '{"format":"kaggle","episode":{...},"finished":false}'

# 终局：finished=true
curl -X POST 'http://127.0.0.1:8899/matches/import-episode/live?format=auto' \
  -H 'Content-Type: application/json' \
  -d '{"format":"kaggle","episode":{...},"finished":true}'
```

- `episodeId` 必填且稳定；它是直播幂等键，同一 ID 的第一帧建场，后续帧追加，
  `finished=true` 只收尾一次；
- 帧必须前缀稳定：第 N+1 帧的 `steps` 必须以第 N 帧为前缀，删步/改历史返回 409；
- 重复帧幂等返回当前进度，不追加事件；已结束场次不会被后续帧复活；
- 每帧仍然受 `write_match` 鉴权、`matches` 配额和请求体 8MB 限制；建场成功才扣配额；
- CLI 可以用 `melee match import-live snapshot.json --finished` 手动推一帧。

## 来源、许可证与幂等

- 导入结果会写入 `matches.source` 与 `matches.source_id`；同一
  `(source, episodeId)` 重导入只返回已有场次，不会生成第二个 `matchId`；
- 数据集级事实通过 `metadata` 传入：`license`、`sourceUrl`、`sourceLabel`。
  CLI 对应 `--license` / `--source-url` / `--source-label`，会显示在观战页
  的出处 chip 上；
- 站内统计默认只把站内赛事算进胜者榜与 14 天活动；导入场次有独立
  `importedMatches` / `importedActivity`，需要看导入节奏时传 `?source=import`；
- 导入棋盘现在有 a–h / 1–8 坐标、最后一手起止格高亮；没有 FEN 的非棋类
  仍然只显示文字局面，这是数据边界，不伪造棋盘。

## 边界

- 导入场次不进入 worker 队列，不会重新跑；
- 实时桥是 push 模型，不主动轮询外部 API；集成方需要在事件回调里发帧；
- 单场最多 10,000 手、60,000 条事件、8MB 输入；思考片段最多 2,000 字。
  这些上限是为了不让一个坏文件把进程内存或 SQLite 撑爆；
- 批量直连 SQLite 仍保留给离线/单机场景；生产批量应加 `--via-api --token`，
  走鉴权、配额与限流。每日配额默认 20，跑数据集前用 `MELEE_QUOTA_MATCHES`
  显式调高，或给批处理单独发 token；
- `/matches/{id}/state` 对导入场次走事件投影，不调用引擎，因此国际象棋、
  围棋这类 melee 没有原生规则的棋也能正常打开；
- 外部 `reason` 原文放进 `finalState.sourceReason`，对外原因统一为“对局结束”，
  避免英文界面出现中文句子；
- 导入的棋盘会按尺寸缩放到观战面板，超过 9 列的棋类自动缩小格子。
