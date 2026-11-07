# 公平性与多智能体能力报告（2026-10-29）

> 目标：验证「AI 能否真正独自对局」——不看脚本、不靠泄露、不靠偏袒。
> 方法：先做对抗性审计找出真实缺陷，再逐条修复，最后用门禁锁住。
> 原则：每条结论都有可复现命令或 `文件:行号`；拿不出证据的不写。

---

## 1. 结论摘要

| 维度 | 审计前 | 现状 |
|---|---|---|
| 隐藏信息泄露 | **3 处**（Kuhn 双方手牌、GOPS 双方手牌、GOPS 秘密出牌） | 0（有 44 条参数化门禁） |
| 先手偏袒 | **固定**（6 个种子全是同一人先手） | 由 seed 决定，40 局严格 20/20 |
| 回合数对称 | **不对称**（20q 中 A 出手 11 次、B 10 次） | 对称（预算取人数整数倍） |
| 资源对称 | 已对称（独立 client / 预算 / 重试） | 保持，且有测试锁定 |
| 自然能力可完成度 | 12 赛制里有 **4 个**会卡死或被迫弃权 | **12/12 零非法、零弃权、正常终局** |
| 多 AI 编队 | 11/12 赛制强制 2 人，代号写死 4 人 | 代号支持 2–6 队 × 每队 2–4 人（4–16 AI） |
| 公平性门禁 | 无 | **59 条**参数化测试 + 11 条编队 + 11 条外部 AI |

测试总量：**729 passed / 1 skipped**（本轮 +100 条）。

> 注：以上数字是 2026-10-29 公平性改造时的快照。当前全量测试与覆盖率以
> `bash scripts/verify_all.sh` 的最新输出为准，不在本文固化。

---

## 2. 审计发现的真实缺陷（全部可复现）

### 2.1 隐藏信息泄露（High）

| # | 位置 | 现象 | 影响 |
|---|---|---|---|
| L1 | `textarena_adapter.observe()` | 返回完整 `gameState`，含**双方手牌** `player_cards` | 任何读 `observation["gameState"]` 的选手（自定义 agent、复盘脚本）都能看到对手手牌 |
| L2 | 同上，GOPS | `player_hands` 含双方手牌 | 同上 |
| L3 | GOPS `pending_bids` | **本轮秘密出牌**对后手可见 | 后手能看到先手出的牌再决定自己出什么，**赛制竞技性直接归零** |
| L4 | `public_state()` | 公开事件流同样带上述字段 | 观众与所有重放都能看到，等于直播剧透 |

复现命令（修复前）：

```python
g = registry.get("game-of-pure-strategy"); st = g.init(seed=3, players=["A","B"], config={})
print(st["gameState"]["player_hands"])       # {0: [...], 1: [...]}  双方手牌
# 先手出一张后：
print(st["gameState"]["pending_bids"])       # {1: 2}  后手可见先手的暗牌
```

### 2.2 结构性偏袒（High）

| # | 现象 | 证据 |
|---|---|---|
| F1 | **先手固定**：tic-tac-toe / othello 六个种子全部 A 先手；kuhn-poker 全部 B 先手 | 上游环境先手永远坐 0 号位，而我们直接把选手列表传进去 |
| F2 | **回合数不对称**：20q 的 `maxQuestions=21`（奇数），A 问 11 次、B 问 10 次 | `{"A": 11, "B": 10}` |
| F3 | **问满上限判给提问者**：偶数预算下这个人永远是后手 | 奖励"问得多"而不是"推理对" |

### 2.3 自然能力无法发挥（High）

| # | 现象 | 根因 |
|---|---|---|
| N1 | 二十问的模型**永远不可能合法猜中** | `legal_moves` 只给 `guess:<candidate-name>` 占位符，观察里也没有候选名单；唯一"会赢"的是偷用 CATALOG 的脚本选手 |
| N2 | Othello 第 18 步**卡死** | 无子可下时上游会自动 skip，但适配器不给 `[pass]` 选项，选手只能看到格式提示 |
| N3 | GOPS 第 18 步**被迫弃权** | 手牌快打完时观察不再印 Available Moves，`legal_moves` 退化成格式提示 |
| N4 | Connect-four 第 6 步**非法** | 从观察文本正则抠棋盘，`rows[0]` 不保证是顶行，给出"看似合法、实际已满"的列 |

### 2.4 多 AI 编队缺失（Medium）

- 11/12 赛制 `player_range` 是 `(2,2)`，第三人直接被拒。
- 代号写死 `(4,4)`，队伍映射硬编码 `players[0]/[2]=红、players[1]/[3]=蓝`。
- 3 队时 **team3 没有自己的词**（配色只按红蓝分配），只能靠翻别人的词获胜——假的三队。

---

## 3. 修复内容

### 3.1 隐藏信息裁剪（`textarena_adapter._safe_game_state`）

赛制的 `SUPPORTED` 表新增 `hidden` 声明：

```python
"kuhn-poker":              {"hidden": {"player_cards": "own"}}
"game-of-pure-strategy":   {"hidden": {"player_hands": "own", "pending_bids": "own"}}
```

三种模式：
- 未声明 → 完全公开（棋盘类本就是完美信息）；
- `"own"` → 观察里只留自己那份；公开状态整个删掉；
- `"drop"` → 任何视角都不给。

**终局摊牌**：`reveal=True` 时恢复完整——扑克在摊牌前不给看牌，摊牌后必须给，否则观众看不出谁在诈唬。

### 3.2 先手轮转（`TextArenaGame.init`）

不改 `players` 顺序（它是身份的事实），另存 `seats`（席位 → 选手）：

```python
seats = [players[(seed + i) % len(players)] for i in range(len(players))]
```

`current_player()` 返回 `seats[env.current_player_id]`；结算 `_outcome()` 也按 `seats` 翻译。
同 seed 同结果，重放天然可复现。

### 3.3 二十问规则修正

| 改动 | 理由 |
|---|---|
| `maxQuestions` 取人数整数倍 | 21 → 20，A/B 各 10 次 |
| 先手 = `seed % len(players)` | 不再固定 A |
| 问满上限**一律平局** | 获胜只能靠猜中，不能靠"最后一问恰好收敛" |
| 观察补 `candidates` 全名单 | 候选是公开规则；不给名字，猜错判负这条规则对模型等于不存在 |
| LLM 提示词带上候选名单 | 同上 |

### 3.4 合法动作补齐

| 场景 | 修复 |
|---|---|
| Othello 无子可下 | `legal_moves` 返回 `["[pass]"]`；`normalize_action` 接受 `[pass]`；`apply` 对 pass 只检查"是否换手" |
| GOPS 手牌耗尽 | 新增 `_hand_moves()`：合法动作 = 自己手里尚未打出的牌 |
| Connect-four 列判断 | 优先用 `gameState.board` 的顶行，不再正则抠文本 |
| BoardAgent | 优先用引擎给的 `legal`，不再自己重复解析 |

### 3.5 多 AI 编队

新增 `services/match/roster.py`（纯函数，11 条测试）：把「一组 AI」解析成「席位」，
三者严格分开——**player（身份）/ seat（位置）/ team（阵营）**。

代号引擎支持：

```python
config = {"teams": 3, "teamSize": 2}   # 3 队 × 2 人 = 6 个 AI
```

- 默认 `teams=2, teamSize=2` → 经典 4 人，**逐位兼容旧行为**；
- 配色：2 队沿用 9/8/7/1；3 队及以上均分（否则新队伍没有自己的词）；
- 每队多队员**轮流猜**（用 `guessesThisTurn`，不是 `turnIndex`——后者每步都涨会跳号）；
- 编队参数写进 `match.created.config`，**重放能重建同一场**。

API：

```json
POST /matches
{"game":"codenames","seed":11,
 "players":[{"id":"AI-0"}, ..., {"id":"AI-5"}],
 "config":{"teams":3,"teamSize":2}}
```

---

## 4. 公平性验证（可复现）

### 4.1 先手分布（40 局）

```
赛制                 局数  A先手  B先手
tic-tac-toe          40    20     20
othello              40    20     20
nim                  40    20     20
connect-four         40    20     20
twenty-questions     40    20     20
kuhn-poker           40    20     20
```

### 4.2 同等强度镜像对局（Minimax vs Minimax，20 局）

```
赛制                 A胜  B胜  平
tic-tac-toe           0    0  20     ← 理论必和，正确
reverse-tic-tac-toe   0    0  20     ← 同上
wild-tic-tac-toe     10   10   0     ← 先手 10/10，胜负完全跟随先手
```

**这是关键证据**：井字棋本身必和，如果平台有偏袒，不可能 20/20 全平。

### 4.3 自然能力完成度（12 赛制，只给 `observe()`）

```
赛制                     步数  非法  弃权  终局
codenames                 18     0     0  True
connect-four               7     0     0  True
crusade                   40     0     0  True
game-of-pure-strategy     26     0     0  True
kuhn-poker                 7     0     0  True
nim                        6     0     0  True
othello                   61     0     0  True
reverse-tic-tac-toe        8     0     0  True
simple-tak                13     0     0  True
tic-tac-toe                8     0     0  True
twenty-questions           5     0     0  True
wild-tic-tac-toe           9     0     0  True
```

### 4.4 反作弊对抗（4 类尝试，全部被堵）

| 尝试 | 结果 |
|---|---|
| 读 `observation.gameState` 找对手手牌 | 只有自己 1 个键 |
| 读 `public_state` 找对手手牌 | 字段不存在 |
| GOPS 后手偷看先手暗牌 | `pending_bids = {}` |
| 二十问从观察推出 target | 除 `candidates` 外任何字段都不含答案 |

> **一个必须澄清的点**：Kuhn 扑克只有 3 张牌，知道自己的牌就能推出对手的牌——
> 这是**公开规则**（上游规则文本写明 "dealt without replacement"），不是泄露。
> 真正的泄露是"直接读到对手的牌"，这一条已经堵死。

---

## 5. 与开源项目的对照（源码级）

| 机制 | 参考项目 | 它的做法 | 我们的落地 |
|---|---|---|---|
| **N-agent 轮转** | PettingZoo `agent_selector` | 用 `agent_order` + `next()` 显式轮转，支持任意 N | `seats` 按 seed 轮转 + 代号 `teamNames` 顺序轮转 |
| **按 player 裁剪观察** | TextArena `state.observations[pid]` | 每玩家一份 `Observations` dict | `_safe_game_state(viewer_index)` |
| **InformationState vs Observation** | OpenSpiel | 两者严格分开：前者含私有信息，后者是公开的 | 我们把它落成 `observe()` vs `public_state()` 两个方法 |
| **动作合法性只给当前玩家** | OpenSpiel `legal_actions(player)` | 非当前玩家返回空 | `legal_moves()` 同样只给当前玩家 |
| **多人结算** | PettingZoo `terminations` / `rewards` | 每 agent 一份 | `_outcome()` 按 seats 翻译 reward |
| **并发配额公平** | AgentBench `assigner.py` | MaxFlow 分配 agent × task | 未做（见 §6） |
| **观察规格按 agent 定义** | Melting Pot `observation_spec` | 每个 agent 独立 spec | `hidden` 表按赛制声明 |

**别人有、我们也有**：N-agent 轮转、按 player 裁剪、合法动作隔离、多人结算、重放确定性。
**别人没有、我们有**：同一份事件流的五投影（直播/回放/导播/战报/高光）+ 事实校验战报 + 赛后揭秘。

---

## 6. 明确未做（有理由）

| 项 | 为什么不做 |
|---|---|
| 赛程 MaxFlow 编排 | 需要先有"多 worker 容量"概念；当前单机 4 线程不构成瓶颈 |
| Melting Pot 式 observation_spec | 我们的 `Game` 协议已用 `observe()` 表达，再套一层 spec 是重复抽象 |
| 开放式社交模拟（OASIS 百万 agent） | 与"转播"定位无关，性能模型也不同 |
| 多队赛制的赛季积分 | 当前 `Season` 只支持 1v1 循环赛；多队积分需要新的排名模型，属独立切片 |

---

## 7. 复现命令

```bash
# 全量测试（数字以本次运行输出为准）
.venv/Scripts/python.exe -m pytest -q

# 只跑公平性门禁（59 条）
.venv/Scripts/python.exe -m pytest services/match/tests/test_fairness.py -q

# 编队解析（11 条）
.venv/Scripts/python.exe -m pytest services/match/tests/test_roster.py -q

# 起服务并建 3 队对局
MELEE_DB=data/match.db .venv/Scripts/python.exe scripts/serve.py
curl -X POST localhost:8099/matches -H 'Content-Type: application/json' -d '{
  "game":"codenames","seed":11,
  "players":[{"id":"AI-0"},{"id":"AI-1"},{"id":"AI-2"},{"id":"AI-3"},{"id":"AI-4"},{"id":"AI-5"}],
  "config":{"teams":3,"teamSize":2}}'
```
