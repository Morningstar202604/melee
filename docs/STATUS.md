# 项目状态（2026-10-22）

一句话：**单机可完整演示六种赛制、直播/回放/导播/战报/高光全链路可用；离生产级还差 1 项（覆盖率），已逐条列在下面。**

这份文件只写"实测过的"和"没做的"，不写形容词。

---

## 1. 已完成并实测

| 能力 | 状态 | 证据 |
|---|---|---|
| 六种赛制可跑完整对局 | ✅ | 二十问/代号/井字棋/四子棋/黑白棋/取石子，`test_games_parity.py` 32 条 |
| 事件契约（JSON Schema 单一真相源） | ✅ | `packages/contracts`，`test_contract_parity.py` 三处一致 |
| 直播（SSE + Last-Event-ID） | ✅ | worker 写事件后 1.4s 内到达；断线重连从下一条开始 |
| 回放（任意位置重算 + 倍速） | ✅ | 直播与回放共用 `reduce(events)`；拖动时间轴实测 |
| 自动导播 + 人工切镜 | ✅ | 离线补 cue 与直播镜头序列逐条相等（六赛制参数化测试） |
| 解说 | ✅ | 规则式全赛制；LLM 解说需 `MELEE_NARRATOR=llm`，三层事实校验 |
| 战报 + 数字对账 | ✅ | `verify_summary`；代号/棋类 `problems` 为空 |
| 高光渲染 mp4 | ✅ | PIL 画帧 + ffmpeg；代号/井字棋/取石子抽帧核验 |
| 赛季（OpenSkill + 循环赛 + 宿敌） | ✅ | 8 人 28 场跑完出榜；积分幂等 |
| 赛程到点开赛 | ✅ | `scheduled_at`；未来场次调度器跳过（实测 tick 返回空） |
| 多副本 worker（租约队列） | ✅ | 崩溃重试、过期重领、次数上限 13 条测试 |
| 跨副本广播 | ✅ | Redis 7 实测：A 建场、B 订阅，39 条事件 3.6s 内到齐 |
| 跨副本限流 | ✅ | 两副本交替 12 次建场：10×201 + 2×429 |
| BYOK 出站防护（SSRF） | ✅ | `outbound.py`；内网/元数据/file 均 422，公网端点可跑完 |
| 鉴权 / 配额 / BYOK 加密 | ✅ | token 模式全端点 401 覆盖；队列密文 `enc:v1:` |
| 媒体票据 | ✅ | 可复用、绑定场次、10 分钟；无凭据 401 |
| 单端口部署 | ✅ | `python scripts/serve.py` 同端口出前端与 API |
| Docker | ✅ | 镜像 healthy；容器内跑完 64 事件对局 |
| CI | ✅ | GitHub Actions 两个 job（test / docker）全绿 |
| 截图 / 录屏 | ✅ | `docs/screenshots/` 10 张；`docs/video/walkthrough.mp4` 55 秒 |

---

## 2. 部分完成（能用，但有明确限制）

| 能力 | 完成度 | 限制 |
|---|---|---|
| 真实模型选手 | 可用 | 任意 OpenAI 兼容端点；实测 Agnes-2.5-Flash。免费额度会 429，此时记犯规，无熔断 |
| 隐藏信息 | 可用 | 代号靠 `observe()` 裁剪；事件流 `visibility` 字段已备但未使用 |
| 部署 | 单机可用 | Docker + compose；未提供 k8s / 反代配置样例 |
| 无障碍 | 基础 | 解说 `aria-live`、`prefers-reduced-motion`、键盘快捷键；**无焦点环与完整键盘遍历** |
| 观测 | 基础 | `/health` + `/metrics`（Prometheus 文本）；无追踪、无告警规则 |

---

## 3. 未完成（明确欠账）

| 项 | 影响 | 计划 |
|---|---|---|
| 覆盖率 85.8% / 分支 77.6% | 低于 90/85 红线（CI 门禁是 80%，所以不阻断发版） | 继续补 `api.py` 的 SSE/剪辑下载分支 |

---

## 4. 量化指标（本机实测）

| 指标 | 值 |
|---|---|
| 测试 | 354 passed / 2 skipped |
| 行覆盖率（非测试代码） | 85.8% |
| 分支覆盖率（非测试代码） | 77.6% |
| 变异分数（30 个抽样变异体） | 73% |
| 圈复杂度 ≤10 的函数 | 93.8%（272/290，`build_recap` 44→2、`find_highlights` 19→2） |
| 重复行 | 1.33%（jscpd） |
| Bandit Medium+ | 0 |
| 依赖 CVE | 0（OSV 扫描 13 个依赖；升级 Pillow/cryptography/pytest 后清零） |
| 后端代码 | 7,150 行（不含测试） |
| 前端代码 | 1,740 行，零构建（渲染层 lit-html，vendored 8KB） |

---

## 5. 怎么自己验

```bash
bash scripts/verify_all.sh     # 7 道门禁，全过打印 ALL GREEN
python scripts/seed_demo.py    # 生成演示数据
python scripts/serve.py        # 单端口起服务
```

打开 <http://127.0.0.1:8099/> 即可看到观战页。

---

## 6. 重构记录（2026-10-23）

**原则：能用成熟库就不自己写。** 本轮把四处手写实现换成了库：

| 原实现 | 换成 | 代码量变化 | 收益 |
|---|---|---|---|
| 自写内存桶 + Redis ZSET 限流（约 150 行） | `limits` 5.8.0（`MovingWindowRateLimiter` + `MemoryStorage`/`RedisStorage`） | 150 → 95 行 | 后端可插拔、窗口语义标准、键过期由库处理 |
| 进程内票据字典（SSE + 媒体） | `itsdangerous` 2.2.0 签名票据 | 60 → 35 行 | **无状态**：多副本任一副本都能验签，过期写进载荷，重启不失效 |
| 手写 Prometheus 文本（5 行指标） | `prometheus-fastapi-instrumentator` 8.1.0 | — | 请求数/延迟直方图/状态码全部标准化；业务指标保留为 Gauge |
| 线程 + sleep 的赛季调度 | `APScheduler` 3.11.3 `BackgroundScheduler` | 60 → 75 行（略增） | `max_instances=1`、`coalesce`、任务停止由库保证 |
| 单线程 worker | 可配置并发（默认 4 线程） | +10 行 | 慢对局不再阻塞新开赛（实测 6 场并发跑完，队列 0 积压） |
| 19 处 `os.environ.get("MELEE_*")` | `pydantic-settings` 2.16.0 单一 `Settings` | 分散 → 1 处 | 启动即校验类型，配置错误不再等到运行时 |
| 手写 `innerHTML` + `escapeHtml`（30 处） | `lit-html` 3.3.3（8KB，vendored，无构建） | 1053 → 约 1000 行 | **模板自动转义**，XSS 从"记得转义"变成"默认安全" |
| 手写 XOR 定长比较 | 标准库 `hmac.compare_digest` | -5 行 | 标准实现 |

**顺带清掉的**：本地 `melee/` 旧实现（597MB，含 node_modules）——已备份到 `/tmp/legacy-backup/melee-legacy-20261023.tgz`，并保留在五个仓库的 `legacy` 分支。根目录只剩本项目。

**未换的**（评估后认为自研更合适）：
- 事件日志：`append-only + seq` 是产品契约的核心，SQLite 直接写最可控（换 Kafka/Redis Streams 会引入运维成本）。
- 比赛引擎：规则本身就是产品，无成熟库可替代。
- 解说事实校验：业务规则，没有通用库。
