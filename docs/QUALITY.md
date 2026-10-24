# 质量报告（可复验）

最后更新：2026-10-24。所有数字都是本机实测；命令与结果一一对应，没有估算。

## 一、门禁总览

| # | 门禁 | 命令 | 结果 |
|---|---|---|---|
| 1 | 测试 | `python -m pytest` | **433 passed, 2 skipped**（跳过的是 Redis 集成） |
| 2 | 覆盖率 | `pytest --cov=services.match --cov-branch --cov-fail-under=85` | 行 **91.1%**（3989/4380）、分支 **80.6%**（1008/1250），门禁通过 |
| 3 | Lint | `python -m ruff check .` | All checks passed |
| 4 | 安全 | `bandit -c pyproject.toml -ll -r services/match` | Medium 0 / High 0（15 条 Low 属已审阅的 assert / subprocess） |
| 5 | 许可证 | `python scripts/check_licenses.py` | 通过（无 GPL/AGPL，NOTICE 台账与安装版本一致） |
| 6 | 前端语法 | `node --check apps/web/src/*.js` | 7 个模块全通过 |
| 7 | 契约生成 | `python packages/contracts/scripts/codegen.py --check` | 生成物齐备 |
| 8 | 依赖 CVE | `python scripts/audit_cves.py --fail-on high` | 0 个 High/Critical |

一键跑完：`bash scripts/verify_all.sh` → 全绿打印 `ALL GREEN`。

## 二、覆盖率明细（按模块）

| 模块 | 行覆盖 | 说明 |
|---|---:|---|
| `settings.py` / `contract.py` | 100% | 纯声明与校验 |
| `auth.py` | 98% | 票据、配额 |
| `recap.py` | 97% | 战报与事实校验 |
| `ratelimit.py` | 95% | 限流 |
| `render.py` | 94% | 高光渲染 |
| `workqueue.py` / `season.py` / `schedule.py` | 94% | 队列、赛季、赛程 |
| `runner.py` / `codenames.py` / `director.py` / `narrator.py` | 90–91% | 核心链路 |
| `api.py` | 83% | 未覆盖集中在 SSE 异常分支与剪辑下载的边角 |
| `worker.py` | 83% | 未覆盖集中在租约异常与渲染失败分支 |
| `textarena_adapter.py` | 85% | 未覆盖为上游异常路径 |

**为什么不是 95%**：剩下的分支几乎全是"外部依赖失败"路径（Redis 断连、ffmpeg 缺失、上游 4xx/5xx），
要测就得引入真实故障注入；当前用 `fakeredis` 与 `FakeOpenAI` 覆盖了主要分支，剩余部分如实记录。

## 三、复现步骤

```bash
python -m pip install -r requirements-dev.txt
bash scripts/verify_all.sh
```

Windows 下需要 `ffmpeg` 在 PATH（高光渲染测试会跳过而不是失败）。

## 四、本轮修复清单（含回归测试）

| 级别 | 缺陷 | 回归测试 |
|---|---|---|
| High | TextArena 观察一次性队列被 `observe()` 读空，导致 `legal_moves` 退化成格式提示 | `test_textarena_adapter.py::test_observe_does_not_consume_legal_moves`、`::test_legal_moves_survive_a_turn` |
| High | 棋类模型只回裸动作 `[4]` 被判格式犯规 | `test_llm_agent.py::test_bare_legal_action_is_accepted_verbatim`、`::test_bare_text_outside_legal_moves_still_rejected` |
| High | 取石子只枚举"每堆取 1"，`[0 2]` 被判非法 | `test_textarena_adapter.py::test_nim_enumerates_every_take` |
| High | 弃权局战报自检误报"胜者不在参赛名单里" | `test_recap.py::test_recap_accepts_winner_when_nobody_applied_an_action` |
| High | worker 崩溃在终局前，比赛永远"直播中" | `test_archive.py::test_worker_repairs_missing_terminal_event`、`::test_ensure_terminal_appends_exactly_one_end_event` |
| Medium | 429/5xx 一次就把模型记成犯规 | `test_llm_agent.py::test_retries_on_429_then_succeeds`、`::test_gives_up_after_retry_budget` |
| Medium | token 模式下 `/metrics` 公开可读 | `test_archive.py::test_metrics_require_token_when_auth_on` |
| Medium | 安全头缺 CSP | `test_archive.py::test_security_headers_include_csp` |
| Medium | 赛季赛制写死二十问 | `test_archive.py::test_season_persists_game_and_runs_that_engine` |
| Medium | 旧库缺 `seasons.game` 列导致升级即崩 | `test_archive.py::test_season_migrates_old_schema` |
| Critical | 剪辑下载路径穿越可读任意文件 | `test_archive.py::test_clip_download_rejects_path_traversal` |
| High | 赛季并发跑同一 fixture 写坏事件流 | `test_archive.py::test_season_run_returns_409_when_locked` |
| High | 终局事件 TOCTOU 产生两条 `match.ended` | `test_archive.py::test_mark_ended_is_idempotent`、`::test_create_match_does_not_wipe_ended_at` |
| High | worker 轮询线程遇异常永久死亡 | `test_workqueue.py`（reap + owner 校验） |
| High | 失租者把他人任务标成 done | `test_archive.py::test_workqueue_complete_requires_owner` |
| High | 重试耗尽的 running 任务永久卡住 | `test_archive.py::test_workqueue_reap_marks_orphan_running_jobs` |
| High | SSRF 漏 CGNAT / ULA | `test_archive.py::test_ssrf_blocks_cgnat_and_ula` |
| Medium | 代号对手翻完你最后一个词不判胜 | `test_archive.py::test_codenames_last_word_revealed_by_opponent_ends_game` |
| Medium | 同 id 重建赛季混入旧选手 | `test_archive.py::test_recreating_season_replaces_old_roster` |
| Medium | 非法 JSON / startAt 返回 500 | `test_archive.py::test_bad_json_body_is_400_not_500`、`::test_bad_start_at_is_400_not_500` |
| Medium | 配额被非法请求消耗 | `test_archive.py::test_quota_is_not_consumed_by_invalid_requests` |
| Medium | 赛季 404 留下孤儿比赛 | `test_archive.py::test_unknown_season_does_not_leave_orphan_match` |

## 五、真实模型联调记录

用本机 ZCode 配置的 OpenAI 兼容端点（`glm-5.3-flash`）实跑：

| 项目 | 结果 |
|---|---|
| 建场带 BYOK | 201，Key 加密入队 |
| 模型动作 | 井字棋 `[4]`、四子棋 `[col 3]`、取石子 `[0 2]` 均被正确解析 |
| 上游 502 | 重试后仍失败，落成一条 `action.rejected`，raw 保留上游 trace_id |
| Key 泄漏检查 | 事件流 / 战报 JSON 全文搜索 Key：0 次命中 |
| 战报自检 | `problems: []` |

上游免费额度会间歇性 502/429，这是服务端问题，不是判罚逻辑；本轮已加有限重试，
持续失败仍如实记犯规，不伪造动作。

## 六、竞品对照

数字来自 GitHub API（2026-10-24 采集），矩阵与复现命令见 [BENCHMARK.md](BENCHMARK.md)。

## 七、性能实测

| 场景 | 指标 |
|---|---|
| 事件 SSE 延迟 | worker 写入到客户端收到 ≤ 1.4s（pytest 里用真实 SSE 连接测） |
| `/archive` 200 场 | 预热后 < 50ms（测试断言 < 500ms，防 CI 抖动） |
| 50 场导播台列表 | 21ms（已过滤 `ended_at IS NULL`，不再全量重放） |
| 分享卡生成 | 单张 1200×630 约 30–60ms，已结束的比赛直接复用磁盘文件 |
| 高光渲染 | 每段 1–3 秒画面，ffmpeg 编码耗时随帧数线性增长 |
