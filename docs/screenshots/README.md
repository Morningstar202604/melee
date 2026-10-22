# 截图与录屏

全部由 Playwright 驱动真实 Chromium 在真实服务上截取（`MELEE_DB=/tmp/... python scripts/serve.py`）。

| 文件 | 页面 | 说明 |
|---|---|---|
| `01-lobby.png` | 观战页（空状态） | 未选比赛时的引导 |
| `02-twenty-questions.png` | 二十问直播 | 候选收窄、内心戏、解说、切镜 |
| `03-codenames.png` | 代号直播 | 5×5 词阵、队别/阶段/线索、4 张选手卡 |
| `04-tic-tac-toe.png` | 井字棋 | 3×3 棋盘、逐手记录 |
| `05-nim.png` | 取石子 | 三堆堆条 |
| `06-recap.png` | 战报页 | 一句话战报、统计、三个瞬间 |
| `07-season.png` | 赛季页 | 积分榜、宿敌、赛程与倒计时 |
| `08-director.png` | 导播台 | 选台、镜头、人工切镜、实时事件 |
| `09-mobile.png` | 观战页（390×844） | 纵向堆叠布局 |
| `10-replay.png` | 回放态 | 琥珀色时间轴与「回放中」标记 |
| `../video/walkthrough.mp4` | 全流程录屏 | 直播 → 回放 → 赛季 → 导播 → 代号，55 秒 |

复现命令：

```bash
MELEE_DB=/tmp/demo.db MELEE_QUOTA_MATCHES=500 python scripts/serve.py --port 8099
cd apps/web && python -m http.server 8088
python /tmp/shots.py   # 截图脚本见仓库 docs/screenshots/README 的说明
```
