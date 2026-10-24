# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本。

## [1.0.1] — 2026-10-24

### 修复

- CI 冒烟脚本的两条断言：首页文案已改为「把 AI 对抗做成节目」，原 grep「转播台」不再命中；
  `recap` 的 `problems` 是紧凑 JSON，`grep` 需用 `-F` 否则 `[]` 被当作字符类。
- 产品行为无变化。

### 文档

- 新增 `docs/BENCHMARK.md`：7 维度 × 6 项目的完整对标矩阵与采集命令。
- 新增 `docs/assets/social-card.png`（1280×640）与生成脚本 `scripts/make_social_card.py`。
- `apps/web/README.md`、`services/match/README.md`、`services/match/DESIGN.md` 同步到五页结构与当前模块。

## [1.0.0] — 2026-10-24

v1.0 的目标是把项目从"能演示"推到"能对外展示"：补齐观众入口、分享、统计与运维缺口，
修掉真实模型联调暴露的判罚缺陷，把所有声明对齐到实测值。

### 新增

- **首页与赛事库**：`GET /archive`（分页、按赛制/状态筛选）+ 五页统一导航与设计系统。
- **全站统计**：`GET /stats`（比赛数、事件数、赛制分布、胜者榜、近 14 天活动、在线人数）。
- **分享能力**：`GET /matches/{id}/card.png` 生成 1200×630 PNG；`GET /share/{id}`
  输出带 OG / Twitter 标签的落地页；前端「分享」按钮优先走 Web Share API。
- **真实在线人数**：`GET /matches/{id}/presence`，读数来自广播后端的订阅计数。
- **赛季赛制**：`POST /seasons` 支持 `game` 字段，赛季内所有对局使用同一引擎；
  旧库自动 `ALTER TABLE` 补列。
- **赛季操作**：`GET /seasons` 列表、前端「新建赛季」「推进赛程」。
- **开赛面板**：首页可直接选赛制、改选手名、填种子、接入 BYOK 真实模型。
- **剧场模式**：观战页 `F` 键隐藏侧栏与控制区。
- **分享与连播**：终局卡新增「看战报」「下一场」。
- **截图与录屏脚本**：`scripts/capture_gallery.py`（12 张）、`scripts/record_walkthrough.py`（46 秒 mp4）。

### 安全

- **剪辑下载路径穿越**：`match_id` 参与文件路径拼接，`..` / 盘符 / 分隔符可读走 SQLite 主库与系统文件；现由 `_safe_match_id` 拒绝（400）。
- **SSRF 漏 CGNAT / ULA**：`100.64.0.0/10` 与 IPv6 `fc00::/7` 现在同样拒绝，阿里云元数据 `100.100.100.200` 单独列出。
- **token 模式下 `/metrics` 公开**：指标端点纳入鉴权。
- **缺 CSP**：新增 `Content-Security-Policy`。

### 修复

- **赛季并发跑同一 fixture**：`run_season` 加数据库级赛季锁，冲突返回 409；调度器遇到锁跳过。
- **终局事件 TOCTOU**：`EventLog.append_terminal` 用 `BEGIN IMMEDIATE` 把「查重 + 插入 + 标记 ended_at」放进一个事务。
- **`mark_ended` 非幂等**：改为 `COALESCE(ended_at, ?)`，重试不再把结束时间往后推。
- **`create_match` 用 `INSERT OR REPLACE`**：重跑同一场会抹掉 `ended_at`，已结束比赛变回"直播中"；改为 `INSERT OR IGNORE`。
- **worker 轮询线程遇异常即死**：`_loop` 加自愈，`claim` 撞锁不再让 4 个线程全部退出。
- **续租线程遇锁冲突即死**：`renew` 加 3 次重试，`renew_loop` 捕获异常后下周期继续。
- **失租者写 `complete`/`fail`**：改为只允许当前 `lease_owner` 更新，避免把别人正在跑的任务标成 done。
- **重试次数用尽的 running 任务永久卡住**：新增 `workqueue.reap()`，每次轮询前回收。
- **赛季 `results` 顺序不稳定**：读取按 `fixtures.round` 排序，OpenSkill 评分不再因 `match_id` 字典序而变。
- **同 id 重建赛季混入旧选手**：`persist(..., replace=True)` 先清 roster/fixtures/results。
- **配额先扣后校验**：参数校验通过后才扣；渲染在确认有可剪内容后才扣；`/seasons/{id}/run` 按场数扣。
- **`inlineWorker` 绕过 `MELEE_INLINE_WORKER=off`**：服务端配置优先。
- **非法 JSON / 非法 startAt 返回 500**：改为 400。
- **赛季 404 留下孤儿比赛**：先校验赛季再写库。
- **直播中生成的分享卡被永久缓存**：结束后重新生成。
- **代号：对手翻掉你最后一个词不判胜**：现在该队直接获胜。
- **Redis 广播异常抛进事件写入**：广播失败降级为告警，事件照常落库。
- **`quotaRemaining` 语义错误**：改名为 `rateRemaining`（它是每分钟限流余量，不是每日配额）。
- **TextArena 观察一次性队列**：`observe()` 读空后 `legal_moves` 退化为格式提示，
  真实模型两次犯规弃权。改为在 `init` / `apply` 时抓取并缓存观察。
- **裸动作被判犯规**：棋类模型只回 `[4]` / `[col 3]` / `[0 2]` 时，现按"与 legal_moves
  逐字符相同"接受；其余仍严格拒绝。
- **取石子动作枚举不全**：只给"每堆取 1 个"，现枚举每堆 `1..n`。
- **弃权局战报自检误报**：`perPlayer` 只从 `action.applied` 推导，胜者可能不在名单里；
  改为以 `match.created` 的参赛名单为准。
- **崩溃后比赛卡在"直播中"**：worker 重试时若重放状态已 `finished` 但缺 `match.ended`，
  自动补写终局事件并标记 `ended_at`。
- **终局事件重复**：`Runner._finish` 先查后写，任务重试不再产生两条 `match.ended`。
- **429/5xx 直接记犯规**：LLM 选手加有限退避重试（默认 2 次，0.8s / 1.6s）。
- **token 模式下 `/metrics` 公开**：指标端点纳入鉴权。
- **缺 CSP**：新增 `Content-Security-Policy`（同源脚本、内联样式、`frame-ancestors 'none'`）。
- **移动端布局**：`body` 固定高度导致选手卡被裁；`display:contents` 后卡片均分高度留白。
- **已结束比赛无限重连**：前端在收到 `match.ended` 或快照已结束时主动关闭 SSE。
- **导播台空列表**：从 URL 打开的比赛会补进左侧列表；事件数改用总数而非最近 12 条。
- **赛事库伪随机柱状图**：换成"本场事件数 / 本页最大值"的真实比例条。
- **胜者榜混入团队名**：`red` / `blue` 不再进个人胜者榜。

### 变更

- 前端从 `index.html` 单页拆为 `index.html`（首页）+ `watch.html`（观战）；
  `?match=` 旧链接自动跳转，不破坏已有书签。
- 页面渲染统一走 `paint.js`：首次绘制前清空静态占位，之后走 lit-html 增量。
- 观战页顶栏重排，`matchId` 输入框与按钮不再撑破 52px 高度。

### 文档

- 重写 README：入口、截图、能力表、BYOK、API、质量、对标、边界。
- 新增 `docs/ARCHITECTURE.md`、`docs/API.md`、`docs/QUALITY.md`、`docs/ROADMAP.md`。
- `docs/screenshots/README.md` 更新为 12 张新截图与复现命令。

### 移除

- 旧的 10 张截图（被 `capture_gallery.py` 重新生成的一组替代）。

## [0.9.0] — 2026-10-23

### 变更

- 用成熟库替换手写实现：`limits`（限流）、`itsdangerous`（票据）、
  `prometheus-fastapi-instrumentator`（指标）、`APScheduler`（调度）、
  `pydantic-settings`（配置）、`lit-html`（模板）。
- worker 支持并发（默认 4 线程），慢对局不再阻塞新场次。

### 修复

- worker 长任务不续租导致同一场被两个副本重跑（出现两条 `match.ended`）。

## [0.8.0] — 2026-10-22

### 新增

- Dockerfile / compose / 健康检查 / Prometheus 指标。
- 许可证台账（NOTICE）与 `check_licenses.py`。
- GitHub Actions：test + docker 两个 job。

### 修复

- 存储型 XSS（选手名未转义）。
- `X-Forwarded-For` 轮换绕过限流。
- SQLite 并发写 `database is locked`。
- 管理接口未鉴权且回显 API Key。
- 视频下载无鉴权（改为媒体票据）。
- SSE 假直播（事件要等 15s 心跳才到）。
- 棋类零解说零镜头；代号战报自检恒失败；高光面板硬编码二十问语义。

## [0.7.0] — 2026-10-21

### 新增

- 赛制从 1 扩到 6：自研代号（4 人隐藏信息）+ TextArena 井字棋/四子棋/黑白棋/取石子。
- 赛季：OpenSkill 积分、循环赛程、宿敌。
- 赛程到点开赛（`scheduled_at`）。
- 前端按 `gameKind` 切换中央面板（二十问 / 代号 / 棋盘 / 堆条）。
