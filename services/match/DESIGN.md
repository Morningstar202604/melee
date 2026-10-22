# 用例与运行脚本说明

## 目录

```
services/match/
├─ engine/
│  ├─ base.py                Game 协议（init/observe/legal_moves/apply）
│  └─ twenty_questions.py    双人属性问答版二十问
├─ agents/
│  ├─ base.py               Agent 协议：act() 返回 {move, raw}
│  ├─ scripted.py           ScriptedAgent（按脚本）/ GreedyAgent（确定性贪心）
│  └─ llm.py                LLMAgent（OpenAI 兼容端点，BYOK）
├─ eventlog.py               SQLite append-only 事件日志 + replay
├─ contract.py               JSON Schema 校验
├─ runner.py                 编排：轮次 → 动作 → 引擎判定 → 事件
├─ narrator.py               规则式解说（零模型依赖）
├─ api.py                    FastAPI：建场 / 事件 / 视图 / SSE
├─ demo.py                   命令行跑一局，打印时间线
└─ tests/                    pytest
```

## 引擎规则（twenty-questions）

候选物 16 个，每个有 5 个属性：`category / place / function / material / scale`。

- 轮流发问，只能问 `属性=值` 的是非题。
- 引擎按属性表直接回答，**没有 LLM 参与**，所以结果可复现、可测试。
- 任何时候都可以直接猜；**猜错立即判负**。
- 问满 `maxQuestions` 仍未猜中：若候选收敛到 1 个，最后一问的人胜；否则无人胜。

## 三条设计约束（对应计划 §3.2）

1. **事件日志是唯一真相**：`EventLog.append` 分配 `seq`，调用方无法跳号。
2. **直播与回放同构**：`replay(events)` 折叠出的视图，与直播时前端累加的结果一致（有测试锁）。
3. **隐藏信息不出引擎**：`observe()` 不含 target，`public_state()` 只含提问记录（猜测记录里带答案，所以被排除）。

## 加一个新赛制

实现 `Game` 协议四个方法即可，无需改 runner、日志或 API：

```python
class MyGame:
    game_id = "my-game"
    def init(self, seed, players, config) -> dict: ...
    def observe(self, state, player) -> dict: ...      # 只含该选手可见的信息
    def public_state(self, state) -> dict: ...          # 只含公开信息
    def current_player(self, state) -> str | None: ...
    def legal_moves(self, state, player) -> list[str]: ...
    def apply(self, state, player, move) -> MoveResult: ...
```

`MoveResult` 必须带 `next_state`，且 `apply` 内部先深拷贝——引擎不允许就地修改传入的 state。

## 犯规与弃权的语义

- 模型输出解析不出来 → 产生一个明显非法的动作（`kind=ask, value=""`）→ 引擎拒绝 → 记 `action.rejected`。
- 同一回合最多尝试 `max_attempts_per_turn`（默认 2）次。
- 仍然不合法 → 触发引擎的 `forfeit` 动作 → 对手获胜，`endReason="弃权"`。
- 这条路径保证"比赛一定会结束"，不会因为模型持续输出垃圾而挂住。

## BYOK 的边界

`ModelSpec.apiKey` 只在 `LLMAgent` 的 httpx 客户端里存在：

- 不进 `LLMConfig.__repr__`（`repr=False`）；
- 不进任何事件（`action.proposed.raw` 只放模型输出）；
- 上游 5xx 的响应体会被 `_redact()` 抹掉 Key 后再写进 `raw`；
- 测试 `test_key_never_appears_in_events_or_view` 同时检查事件、视图与 SSE 三种出口。
