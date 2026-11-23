# 本轮改造报告（2026-10-29）

> 对象：`melee` 转播台。方法：全量读码 + 真实服务实测 + 7 个同领域项目源码级对比。
> 原则：每条结论都有可复现命令或 `文件:行号` 证据；拿不出证据的不写。

---

## 1. 项目定位（重新识别）

README 自称「把 AI 对抗做成节目，而不是评测页」。读完 17k 行代码后，这个定位是**准确且稀缺**的：

```
选手模型 → 引擎（纯函数）→ 事件日志（append-only，seq 连续）
                                    ↓
              ┌──────────┬──────────┬──────────┬──────────┐
             直播 SSE    可拖回放   自动/人工导播  战报+事实校验  高光 mp4 / 分享卡
```

**真正的护城河不是"能跑 AI 对局"**（TextArena 有 110 个环境），**而是"同一份事件流的五个投影"**：
直播、回放、镜头、战报、视频全部由 `reduce(events)` 派生，因此天然一致、天然可复现。
竞品里没有任何一个同时具备这五层（详见 §4）。

**不成立的自我认知**：README 把"本地化/多语言"当卖点是无意义的——
这个项目的价值在「可验证的内容生产」与「观赛体验」，不在翻译。

---

## 2. 实测发现的问题（按严重度）

### High：宣称有、实际没有

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| H1 | **10 个 TextArena 赛制的高光只有 1 条「结束」**。`recap.py` 的 `_EFFECT_HIGHLIGHTS` 没有 `action` 分支，而适配器统一发 `kind=action` | 修复前实测：othello 446 事件 → `clipCount=1`，`kinds=['end']` | README 第 137 行宣称「十二种赛制都有高光剪辑」，实际 10/12 是空的；观赛产品最核心的"高光"对多数赛制失效 |
| H2 | **`/state` 在建场窗口内 404**，而同一时刻 `/events` 返回 200 空列表 | 连续 5 次 POST 后立即 GET：`state=404 events=200` | 观战页会闪"比赛不存在"；两个端点对"已登记未开写"语义不一致 |
| H3 | **终局弹窗把键盘用户锁死**。`trapFocus` 把焦点关进弹窗，但弹窗没有关闭按钮，Escape 又因为"焦点在按钮上"被提前 return | Playwright：焦点在按钮上按 Escape，弹窗不关；Tab 无法逃出 | 纯键盘/读屏用户只能刷新页面 |

### Medium：能力缺口（竞品有、我们没有）

| # | 问题 | 竞品依据 | 影响 |
|---|---|---|---|
| M1 | 没有逐手耗时/成本。`LLMAgent` 只记总预算，不记单步 | lmring `modelResponses(tokensUsed, responseTimeMs)` | 战报无法回答"这一手为什么慢"，也无法做模型性价比对比 |
| M2 | `visibility` 契约只用于"隐藏"，没有"揭示"。赛后看不到队长当时在想什么 | ChatArena `get_visible_messages(agent, turn)` 可按回合重建视角 | 转播产品最典型的"赛后揭秘"能力缺失；`visibility` 字段白写 |
| M3 | 时间轴按**事件序号**而非真实时间。一局二十问里提问与思考片段差一个数量级 | ai-town `useHistoricalTime` 按真实时间插值 | 拖动条前 90% 原地打转、最后 10% 跳完整局，体感"拖不动" |
| M4 | 契约校验每次 `1.769ms`（jsonschema 每次重新遍历 schema） | — | 事件日志写路径每场几百条，导播台 1.5s 轮询，是真实热点 |
| M5 | **代号默认选手整局重复同一线索**。`_act_captain` 固定取 `mine[0]`，那个词翻开后仍给「XX相关 1」 | 实测：15 条线索只有 5 个唯一值，318 事件才终局 | 观感是"AI 卡住了"；对局推进极慢 |
| M6 | 契约 `--check` 只比对事件类型与 `action.kind` 枚举，**payload 新增字段不检查** | 本轮给 `action.applied` 加 `elapsedMs`，生成物没跟上时门禁仍全绿 | 同类漂移会静默复发 |

### Low：可维护性

| # | 问题 |
|---|---|
| L1 | 时间轴上的切镜点若像素级重叠，按钮互相 intercept，一个都点不中（浏览器实测发现） |
| L2 | `eventlog._check_contract` 失败只告警不阻断（设计如此，但没有指标暴露） |
| L3 | `season.build_round_robin` 固定轮转，多 worker 下会队头阻塞（未改，见 §6） |

---

## 3. 已落地的改造

| 切片 | 改动 | 验证 |
|---|---|---|
| 1 | `recap.py` 新增 `action` 分支（`board_move`），权重分三档，决胜手 95 > 终局 60 | 10 个赛制实测：每场 6 highlights / 2 clips，覆盖 seq 从 4 到 439 |
| 2 | `action.applied` 增加 `elapsedMs` + `usage`；`AgentOutput` 携带耗时；`recap.timing` 聚合 | 实测 9/9 动作带 `elapsedMs`，`timing.elapsedMsTotal=2120` |
| 3 | `GET /view/as/{player}` + `GET /events?reveal=1`（直播期 403） | 实测：直播期 403，赛后 public 53 / revealed 55，多出 2 条私有事件 |
| 4 | 新增 `watch/timeline.js`（纯函数，9 条 node 单测）；时间轴按 `at` 缩放；切镜点可点击、重叠合并 | Playwright：16 个点全部可点，点击后游标正确跳转，移动端无横向溢出 |
| 5 | `contract.py` 改用 `fastjsonschema` 编译版，失败回退参考实现 | **1.769ms → 0.082ms（21.7x）**，等价性有测试锁定 |
| 6 | `codegen.check_generated()` 增加 payload 字段比对 | 故意删字段能被抓住 |
| 7 | `CodenamesAgent` 跳过已翻开目标 + 线索去重 | 线索 100% 唯一；同 seed 事件数 318 → 107-253 |
| 8 | `/state` 建场窗口返回 200 + `pending` | 新测试锁定；实测建场即查 200 |
| 9 | 终局弹窗加关闭按钮 + Escape 优先处理 | Playwright：焦点在按钮上按 Esc 也能关；Tab 不逃逸 |

**测试**：611 → **629 passed / 1 skipped**（+18 条），前端新增 9 条。
**覆盖率**：行 93.85%、分支 85.43%（门禁 91% / 85%，均通过）。
**门禁**：新增第 6b 项「前端单测」，`verify_all.sh` 从 10 项变 11 项。

---

## 4. 竞品对比（源码级，非 README 转述）

| 项目 | 它强在哪 | 它缺什么 | 我们能借鉴的 |
|---|---|---|---|
| **TextArena** 433★ | 112 个环境；`ObservationType` 六分类 | 无服务端、无 SSE、无回放 | 六分类映射到导播规则，新赛制零改导播 |
| **FastChat** 39,549★ | BT/Elo 统计深度、置信区间、异常检测 | 无对局引擎、无观战 | 冷热分离：`match.ended` 后批量重算评分 |
| **lmring** 212★ | 多租户/OAuth/i18n/对象存储；每响应记 token 与耗时 | 无对局叙事 | ✅ 已借鉴：逐手 token/耗时 |
| **ChatArena** 1,568★ | `get_visible_messages(agent, turn)` 按回合重建视角 | 无实时推送、已停维护 | ✅ 已借鉴：`/view/as/{player}` |
| **AgentBench** 3,796★ | 最大流做 agent×task 分配 | 完全无观战层 | 赛程用容量感知编排（未做，见 §6） |
| **ai-battlegrounds** 33★ | 2D 战场表现力、地图编辑器 | 无后端、key 暴露在浏览器 | 高光 chip 可点击（✅ 已做） |
| **ai-town** 10,721★ | `HistoricalTimeManager` 缓冲-速率控制的连续时间回放 | 强绑 Convex、无赛制抽象 | ✅ 已借鉴：时间轴按真实时间 |
| **Inspect AI** 3,126★ | 24 类 span 事件模型、实时写日志供 live viewer | 无对局与转播 | 事件 schema 版本化 + span 嵌套（未做） |

### 护城河：五投影一致性

**没有一个竞品把"直播/回放/导播/战报/高光"绑在同一份 append-only 日志上。**

- TextArena 有每玩家观察，没有公共流；
- ai-town 有平滑回放，没有叙事；
- FastChat 有榜单，没有观看；
- lmring 有 SSE，没有时间轴回放。

这条护城河在本轮**被验证为真实**：`reduce(events)` 与 `replayView(events, upTo)` 是同一个函数（`view.js:25` / `view.js:135`），`apply_director` 离线补齐与直播逐条相等（`director.py:169`），且有 71 条参数化测试锁住全部 12 赛制。

---

## 5. 性能实测

| 指标 | 改造前 | 改造后 | 变化 |
|---|---|---|---|
| 契约校验 | 1.769 ms/次（565/秒） | 0.082 ms/次（12,250/秒） | **21.7x** |
| 事件写入（含校验） | — | 0.300 ms/事件（3,332/秒） | 新增基线 |
| 事件读取 | — | 500 条 2.0ms | 新增基线 |
| 棋类高光 | 1 条（end） | 6 条（过程 + 终局） | 能力修复 |
| 棋类剪辑 | 1 段 | 2 段，覆盖全时段 | 能力修复 |
| 代号对局 | 318 事件 / 15 线索 | 107-253 事件 / 4-10 线索 | 节奏正常化 |
| SSE 端到端延迟 | 宣称 ≤1.4s | **实测中位数 2ms**（本地 700ms 步进对局，5 次采样） | 宣称保守，实际远超 |

---

## 6. 明确未做（有理由，不是遗漏）

| 项 | 为什么不做 |
|---|---|
| 事件 schema 版本化 + span 嵌套 | 会改动所有历史事件与前端 reduce，爆炸半径 > 收益；建议下一轮单独切片 |
| 赛程最大流编排 | 需要先有"多 worker 容量"概念；当前单机 4 线程下固定轮转不构成瓶颈 |
| 多副本在线人数全局聚合 | 需要 Redis Set + 心跳过期，当前 `viewer_count` 已注明 scope，属诚实降级 |
| 前端虚拟滚动 | 事件列表已 `slice(-6)` / `slice(-8)`，无大列表问题 |
| 视频渲染换 Remotion | 许可对组织规模有限制，PIL+ffmpeg 已满足"帧只由事件决定"的可复现要求 |

---

## 7. 复现命令

```bash
# 环境（Windows，Python 3.14 无 pydantic-core 轮子，用 3.12）
uv venv --python 3.12 .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-dev.txt

# 全部门禁（11 项）
bash scripts/verify_all.sh

# 单独跑
.venv/Scripts/python.exe -m pytest -q          # 629 passed
node --test apps/web/test/*.test.mjs           # 9 passed
.venv/Scripts/python.exe scripts/check_coverage.py

# 起服务
MELEE_DB=data/match.db .venv/Scripts/python.exe scripts/serve.py
```
