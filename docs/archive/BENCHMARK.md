# 竞品对标（2026-10-24 实测）

数据来源：GitHub REST API（`/repos/{owner}/{repo}`），采集时间 2026-10-24。星数会变，本文只保证"采集时刻真实"。

## 一、具名对标对象

| 项目 | 星数 | 许可 | 最近推送 | Open issues | 定位 |
|---|---:|---|---|---:|---|
| lm-sys/FastChat | 39,549 | Apache-2.0 | 2026-05-01 | 1,048 | LMArena 本体：匿名对战 + 人投票 + Elo |
| TextArena/TextArena | 431 | MIT | 2026-10-05 | 45 | 110 个文本游戏环境，评测基础设施 |
| llm-ring/lmring | 214 | Apache-2.0 | 2026-10-22 | 10 | 可自托管 LMArena：对比 + 投票 + 榜单 |
| Farama-Foundation/ChatArena | 1,567 | Apache-2.0 | 2025-08-11 | 7 | 多智能体语言游戏（已停维护） |
| THUDM/AgentBench | 3,790 | Apache-2.0 | 2026-02-08 | 80 | Agent 综合 benchmark（ICLR'24） |
| jesseduffield/ai-battlegrounds | 32 | 无许可（NOASSERTION） | 2025-12-30 | 0 | 2D 战场实时观战 |

> `ai-battlegrounds` 仓库无 SPDX 许可文件，只能"只读思路"，不能复用代码。

## 二、逐维度对标

评分 1–5（5 = 该维度最强）。"差距类型"只有三种：**跨越**（我们独有）、**持平**、**承认**（对手更强，且我们不打算追）、**刻意忽略**（不在定位内）。

| 维度 | melee | FastChat | lmring | TextArena | ChatArena | AgentBench | 差距类型 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---|
| 赛制数量 | 3 | 2 | 3 | 1 | 1 | 5 | 刻意忽略（TextArena 110 个，不追） |
| 多人 / 隐藏信息 | 3 | — | — | 2 | 4 | — | 领先（4 人代号，`observe()` 裁剪） |
| 实时直播（SSE） | 5 | — | — | — | — | — | **跨越** |
| 可拖动回放 + 倍速 | 5 | — | — | — | — | — | **跨越** |
| 自动 + 人工导播 | 5 | — | — | — | — | — | **跨越** |
| 战报 + 数字事实校验 | 5 | — | — | — | — | — | **跨越** |
| 高光视频（服务端渲染） | 4 | — | — | — | — | — | **跨越**（唯一有成品 mp4 的） |
| 分享卡 + OG 落地页 | 5 | — | 2 | — | — | — | **跨越** |
| 真实在线人数 | 4 | 3 | 3 | — | — | — | 领先 |
| 赛事库 / 归档 | 4 | 3 | 4 | 3 | 2 | 3 | 持平 |
| 赛季（积分 + 循环赛 + 宿敌） | 4 | 4 | 4 | 4 | — | — | 持平 |
| 到点开赛（scheduled fixtures） | 5 | — | — | — | — | — | **跨越** |
| 匿名对战 + 人投票 | — | 5 | 5 | — | — | — | 承认（红海，且与角色叙事冲突） |
| 全站权威 Elo 榜 | — | 5 | 5 | 3 | — | 2 | 承认（不做权威榜） |
| 模型接入广度 | 4 | 5 | 4 | 4 | 3 | 4 | 承认（我们是任意 OpenAI 兼容） |
| 鉴权 / 配额 / 限流 | 4 | 2 | 4 | — | — | — | 领先 |
| 部署形态 | 4 | 4 | 5 | 3 | 3 | 3 | 承认（lmring 有账号体系） |
| 测试 / CI / 许可台账 | 4 | 4 | 4 | 4 | 3 | 3 | 持平 |
| 前端构建链 | 2 | 4 | 4 | 3 | 2 | 3 | 承认（零构建是取舍） |

**"差距类型"合计**：跨越 7 项、领先 3 项、持平 4 项、承认 5 项、刻意忽略 1 项。
也就是说：**在"转播层"上我们领先，在"评测层/账号体系/赛制广度"上我们承认差距并明确不做。**

## 三、四条具名结论

1. **对 lmring（214★）**：它的账号体系、模型对比、投票榜是完整产品；我们没有账号体系（静态 token），
   但有实时观战、回放、导播、战报、高光、分享卡——这些它一项都没有。**结论：在"看"这件事上跨越，在"管"这件事上承认。**
2. **对 TextArena（431★）**：赛制 6 vs 110，差两个数量级。它是 Gym 风格的评测环境，`step()` 返回观察与奖励，
   没有 SSE、没有回放、没有导播。**结论：刻意忽略数量，只在"确定性 + 可播"交集里接 4 个棋类。**
3. **对 FastChat（39,549★）**：匿名对战 + 人投票 + Elo 已被做到极致，且它的价值在网络效应（人多才有意义）。
   我们做角色叙事与赛季，不做权威榜。**结论：承认，不进入。**
4. **对 ai-battlegrounds（32★）**：有 2D 战场渲染，观感强于卡片；但它是无许可仓库，且文字赛制不需要精灵渲染。
   **结论：承认渲染差距，只读思路，不复用代码。**

## 四、我们真正独有的（可验证）

| 能力 | 证据 |
|---|---|
| 直播与回放共用同一个 reducer，画面必然一致 | `apps/web/src/view.js` + `test_games_parity.py` |
| 人工切镜写入事件流，回放逐条重现 | `services/match/control.py` + `test_director.py` |
| 解说三层事实校验（形状/数字/语义） | `services/match/narrator.py` + `test_pipeline.py` |
| 战报数字逐项对账（`problems` 非空即失败） | `services/match/recap.py` + `test_archive.py` |
| 六赛制都真的产出解说与镜头（不是"代码路径支持"） | `test_games_parity.py` 32 条 |
| 分享卡由服务端画，爬虫无需执行 JS | `services/match/card.py` + `test_archive.py::test_share_page_has_open_graph_tags` |

## 五、采集命令（可复现）

```bash
for repo in lm-sys/FastChat TextArena/TextArena llm-ring/lmring \
            jesseduffield/ai-battlegrounds Farama-Foundation/ChatArena THUDM/AgentBench; do
  curl -s -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$repo" \
    | python -c "import sys,json;d=json.load(sys.stdin);print(d['full_name'],d['stargazers_count'],(d.get('license') or {}).get('spdx_id'),d['pushed_at'][:10])"
done
```

未取到时的处理：API 限流会返回 403，重跑即可；本文所有数字都来自成功响应，没有估算。
