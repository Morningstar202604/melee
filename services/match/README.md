# melee 转播台 · M0 运行说明

对应计划：`docs/plans/2026-10-19-broadcast-arena-plan.md` 的 M0 里程碑。

## 第十一批（真实实现收口）

- **棋类也有解说与镜头**：`RuleNarrator.line` 增加 `action` 分支，`Director.cue_for` 增加棋类落子与代号线索/翻牌分支。此前四子棋/黑白棋/井字棋/取石子**零解说、零切镜**，只有代号有。
- **离线导播与直播对齐**：`apply_director` 之前用「动作类型是 guess」判断终局，棋类每步都是 guess，会全程误判；改为读 `publicState.finished`。六种赛制的镜头序列逐个相等（`test_games_parity.py`）。
- **战报按赛制解释**：`build_recap` 输出 `game` / `moves` / `problems`；`verify_summary` 接受团队胜者（red/blue）。此前代号的战报自检永远报「胜者不在参赛名单里」。
- **高光渲染去二十问化**：`build_context` 输出 `gameKind` / `players` / `plays` / `teamRemaining`，`_draw_panel` 按赛制画面板（提问链 / 线索与翻牌 / 最近落子），`_draw_ledger` 支持 2–4 人。此前代号与棋类的视频帧写着"候选 —"、只认 A/B。
- **SSE 交给 sse-starlette**：`EventSourceResponse` + `ping=15` 替代手写帧，断连检测与心跳由库负责；测试断言前统一 CRLF→LF。
- **媒体票据**：`<video>` 带不了 Authorization，新增 `POST /matches/{id}/media-ticket`（可复用、绑定场次、10 分钟），`/clips/{file}` 在 token 模式下同时接受 Bearer 与媒体票据。
- **LLM 解说接线**：新增 `narrator_from_env()`，`MELEE_NARRATOR=llm` 时 worker 与赛季批量都走模型解说（此前 `LLMNarrator` 只有测试在用，生产永远规则式）；`repr` 显式脱敏。
- **合法动作枚举**：棋类适配器从观察里解析 `Available Moves`/`Valid moves`，四子棋按顶行推导，取石子在 `gameState.piles` 上枚举；代号排除已翻开的词。此前只给格式提示，真实模型两次犯规就弃权——实测井字棋与代号都出现过。
- **限流多副本**：`MELEE_RATELIMIT=redis` 有真实实现（ZSET 滑动窗口），两个副本共享计数；Redis 挂了 fail-open。
- **依赖与门禁**：`requirements.txt` 补上 cryptography/Pillow/openskill/textarena/redis；新增 `NOTICE` 台账、`scripts/check_licenses.py`、`.github/workflows/ci.yml`、`.env.example`。
- **前端**：取石子堆条渲染、选手卡改为本人出手数、键盘快捷键（Space/←/→/L/Esc）、解说 `aria-live="polite"`、`prefers-reduced-motion` 降级。

## 第十批（全量检查修复）

- **SSE 真直播**：`EventLog.on_event` 之前从未接线，worker 线程写的事件只能等 15 秒心跳回查日志。现在 `EventLog.open` 统一挂 `broadcast.publish_event`，`MemoryBroadcaster` 用 `call_soon_threadsafe` 跨线程投递；实测事件在 1.4 秒内到达（原为 15 秒一批）。
- **管理接口鉴权**：`/jobs`、`/jobs/{id}`、`/scheduler`、`/scheduler/tick`、`/seasons/{id}` 补上 `_guard`。此前 token 模式下未鉴权，`/jobs` 还会把队列里的 `apiKey` 原样回显。
- **任务载荷脱敏**：`Job.as_dict()` 递归抹掉 `apiKey` / `authorization`，执行体仍拿得到明文。
- **建场即可读**：`/matches/{id}/events` 对"已登记但 worker 还没写第一条事件"返回 200 空列表，不再在前几百毫秒误报 404。
- **观战页 token**：`main.js` 之前没有 `setToken`，token 模式下观战页拿不到 `/games` 与 SSE 票据；已补。
- **前端空指针**：非二十问赛制会重写顶栏 pill，`maxq` / `remainHead` 元素随之消失，第二次渲染抛 `Cannot set properties of null`；改为空值安全的 `setText`。
- **战报/高光补全**：代号补 `assassin` / `own_word` / `end` 三类转折点，棋类补终局高光；`oneLine` 修掉「A 获胜：A 获胜。」与团队胜者 `red` 未翻译。
- **赛程页**：展示 `scheduledAt` 与"即将开赛"倒计时，接口补 `played` 标记。
- **工具**：`textarena_probe` 导入不存在的 `_parse_index` 已修（现可跑单环境探测）；`codegen.py` 未用导入清除，`ruff check` 全清。

## 第九批（解说密度）

- **补齐代号话术**：`RuleNarrator` 新增 `hint` / `reveal` 两个分支，代号比赛从"全程零解说"变成每步都有话。
- **局势播报**：新增 `situation()`，只在转折点开口（领先易手，或静默超过 4 步），不做每步都报。
- **量化结果**：同一局代号，动作 37 条、解说 52 条（其中局势 15 条），覆盖率 141%；相邻解说最大间隔 7 条事件（原来是整场无解说）。

**设计取舍**：宁可 8 秒沉默，也不每步都说"红队正在思考"——那只是噪音。判断标准是这句话是否让观众多知道一件事。

## 第八批（多人赛制 / 动态选手卡）

- **自研 4 人代号（Codenames）**：`engine/codenames.py`。25 词、9 红 / 8 蓝 / 7 中立 / 1 刺客；队长给线索、队员猜词；猜错换手、翻到刺客判负。
- **隐藏信息隔离**：只有队长能看到完整配色；公开棋盘里未翻开的词 `color: null`。四条测试锁住，其中一条用结构断言（不能存在长度 25 的颜色数组）。
- **前端支持 N 人**：选手卡从写死的 A/B 改成按 `players` 动态分配左右两栏（2 人一人一边，4 人 2v2）。
- **代号棋盘渲染**：5×5 词阵，翻开的词按红/蓝/灰上色。

**为什么自研而不是接 TextArena 的 Codenames**：它依赖 NLTK 的 `words` 语料，本机 `nltk.download` 返回 False，环境构造直接失败。

## 第七批（赛制扩充 / 排期 / 赛制皮肤）

- **TextArena 适配器**：`engine/textarena_adapter.py`，把上游确定性环境接成我们的 `Game` 协议。实测接入 4 个：井字棋、四子棋、黑白棋、取石子。
- **赛制注册表**：`engine/registry.py`，内置与适配统一到一处；`GET /games` 返回清单。
- **赛制皮肤**：前端中央面板按 `gameKind` 切换（二十问是候选盘/提问记录，棋类是棋盘），顶栏与选手卡的文案同步切换。
- **赛事排期**：`fixtures` 加 `scheduled_at`，建赛季可传 `startAt` + `intervalMinutes`，开赛条件从"间隔"改为"到点"。

**实测排除的两个赛制**（不是没做，是不能用）：

| 赛制 | 原因 |
|---|---|
| Mastermind | 单人游戏，`num_players` 必须 1 |
| GuessWho | 重置时要 `OPENROUTER_API_KEY`（内部起 LLM 主持人），违反确定性 |

## 第六批新增（导播台 / 赛季页 / 战报页）

- **人工导播**：`control.py`，`POST /director/{id}/cue` 把导播员的切镜写进同一条事件流（所以回放能重现），`GET /director/matches` 列正在直播的比赛。
- **导播台界面**：`apps/web/director.html`，左选台、中选镜头/填备注/切、右看自动与人工计数 + 实时事件。
- **赛季页**：`apps/web/season.html`，积分榜、宿敌、赛程。
- **战报页**：`apps/web/recap.html`，一句话战报、四项统计、三个瞬间、内联高光视频。
- **导航**：四个页面互通，`api` / `token` / `match` 参数自动带到同站其它页面。

## 第五批新增（上线前工程）

- **鉴权**：`auth.py`。`MELEE_AUTH=token` + `MELEE_API_TOKENS="alice:t1,bob:t2"`；请求头 `Authorization: Bearer <token>`。默认 `off`（仅本机）。
- **配额**：每个 token 每天建场/渲染次数，按 UTC 日滚动，存 `quotas` 表；`GET /quota` 查用量。
- **限流**：`ratelimit.py`，按来源 IP 与接口分档（建场 10/分、渲染 5/分、读 120/分、SSE 30/分）。XFF 取链尾，防伪造。
- **凭据加密**：`secrets.py`。入队的 API Key 用 Fernet 加密（`MELEE_SECRET_KEY`），队列里只有密文；`python -m services.match.secrets --gen` 生成密钥。
- **SSE 票据**：`POST /auth/ticket` 换 60 秒一次性票据，避免把 token 拼进 URL 进访问日志。
- **移动端**：窄屏改为纵向堆叠的原生布局，不再缩放 16:9 桌面舞台。
- **`MELEE_DB`**：数据库路径终于可配（之前硬编码，是个真实缺陷）。

## 第四批新增（任务队列 / 跨副本写路径 / 前端接高光 / 债务清理）

- **持久化任务队列**：`workqueue.py`。比赛与渲染都落 `jobs` 表，租约领取、到期可重领、超过次数标 failed。副本崩了任务会被别人接手。
- **跨副本写路径**：`worker.py`。`python -m services.match.worker` 独立跑，或随 API 内嵌（`MELEE_INLINE_WORKER`）。建场接口现在只入队，返回 `jobId`。
- **续跑**：worker 检测到已有日志就重放已完成的动作继续跑（`Runner(resume=True)`），不会重复写 `match.created`。
- **前端接高光**：顶栏与终局卡都有「导出高光」，右侧面板内联播放三段视频。
- **债务清理**：删除队列化后的死代码（旧同步跑法）、清掉全部未使用导入、加 `pyproject.toml` 的 ruff 规则（F401/F811/F821/F841/E9）守住同类问题。

## 第三批新增（渲染 / 跨副本状态 / 定时开赛 / 语义校验）

- **高光渲染成片**：`services/match/render.py`，PIL 画帧 + ffmpeg 编码，`POST /matches/{id}/clips/render` 出 mp4，`GET /matches/{id}/clips/{file}` 下载。
- **跨副本状态**：`services/match/rehydrate.py`，从事件日志重放重建比赛状态；`GET /matches/{id}/state` 与 `GET /matches` 不再依赖进程内字典。
- **定时开赛**：`services/match/scheduler.py`，`MELEE_SCHEDULE=<seasonId>` 开启，按间隔逐场推进；`GET /scheduler` 查状态，`POST /scheduler/tick` 手动推一次。
- **语义级解说校验**：`narrator._is_faithful` 从两层扩到三层，新增"回答方向 / 选手归属 / 动作类型"三项检查。

## 第二批新增（赛程 / 解说 / 高光导出 / 多实例）

- **赛程自动推进**：`services/match/schedule.py`，`POST /seasons/{id}/run` 一键跑完剩余赛程，可断点续跑（只跑还没有结果的场次）。
- **LLM 解说 + 事实校验**：`services/match/narrator.py` 的 `LLMNarrator`，每句都要过 `_is_faithful`（数字必须在事实里、不得出现胜负判定词），不过就退回规则式。
- **高光剪辑清单**：`services/match/clips.py`，`GET /matches/{id}/clips` 输出时间窗口与事件切片，供前端或后续渲染器消费。
- **可插拔广播**：`services/match/broadcast.py`，`MELEE_BROADCAST=redis` + `MELEE_REDIS_URL` 切到跨副本；默认仍是内存。
- **离线比赛可读**：赛季批量跑出的比赛没有进程内 handle，`/events`、`/view`、`/recap`、`/clips` 会回落到日志文件。

## M2–M4 新增（导播 / 赛季 / 高光 / 前端）

- **自动导播**：`services/match/director.py`，确定性规则，每条 `director.cue` 都带触发条件；离线补 cue 与实时产出的镜头序列有测试保证一致。
- **赛季**：`services/match/season.py`，OpenSkill 积分、单循环排程、宿敌统计；从「已结束比赛列表」可幂等重算。
- **战报与高光**：`services/match/recap.py`，转折点由事件定义（一次排除 ≥4、收敛到 1、报答案、犯规、弃权），并带事实校验 `verify_summary`。
- **前端舞台**：`apps/web/`，零构建，直接消费 SSE，支持回放、倍速、回到直播。
- **增量思考流**：`LLMConfig(stream=True)`，思考按字符块推进 `thought.delta`，不必等整段结束。

## M1 新增（真实模型）

- **选手接真实模型**：`services/match/agents/llm.py`，任何 OpenAI 兼容端点。
- **动作被约束成 JSON**：`{"kind":"ask","value":"place=海边","thought":"..."}`，解析不出来就是犯规，绝不替模型猜。
- **思考流**：优先取 `reasoning_content`，其次取 JSON 里的 `thought`，落成 `thought.delta` 事件。
- **BYOK**：Key 随建场请求进来，只活在这次比赛的客户端里；不落库、不进事件、不进日志。

## M0 做了什么

- **事件契约**：`packages/contracts/schema/battle-event.schema.json`，12 种事件的判别联合，含每条 payload 的必填字段。
- **确定性引擎**：`services/match/engine/twenty_questions.py`，双人属性问答版二十问。答案由属性表算出，零 LLM、零网络。
- **编排器**：`services/match/runner.py`，把"谁在什么时候做了什么、结果如何"写成事件。
- **事件日志**：`services/match/eventlog.py`，SQLite append-only，`seq` 由日志分配，支持 `after` 增量读取与折叠回放。
- **解说**：`services/match/narrator.py`，规则式生成，每句话都能追溯到具体效果。
- **HTTP + SSE**：`services/match/api.py`，建场、增量事件、折叠视图、`Last-Event-ID` 续传。
- **假选手**：`services/match/agents/scripted.py`，一个按脚本出招，一个确定性贪心（用于离线与回归）。

## 跑一局

```bash
cd melee
python -m services.match.demo --seed 7 --db data/match.db
```

输出是一份可读时间线，末尾给出由事件折叠出的视图状态。

## 起服务

```bash
python scripts/serve.py --port 8099
```

```bash
# 建场（不写 script 就是贪心选手）
curl -X POST http://127.0.0.1:8099/matches \
  -H "Content-Type: application/json" \
  -d '{"game":"twenty-questions","seed":7,"players":[{"id":"A"},{"id":"B"}]}'

# 增量拉事件
curl "http://127.0.0.1:8099/matches/<matchId>/events?after=10"

# 折叠视图
curl "http://127.0.0.1:8099/matches/<matchId>/view"

# SSE（断线重连把 Last-Event-ID 带上即可续传）
curl -N "http://127.0.0.1:8099/matches/<matchId>/stream" -H "Last-Event-ID: 24"
```

## 测试

```bash
python -m pytest services/match/tests -q
```

当前 **279 passed（另有 1 项 Redis 集成测试默认跳过）**，覆盖：

| 不变量 | 测试 |
|---|---|
| `seq` 从 0 开始、连续、不跳号 | `test_seq_starts_at_zero_and_is_contiguous` |
| 每条事件都符合 JSON Schema | `test_every_event_matches_contract` |
| 契约能拒绝非法事件 | `test_contract_rejects_*` |
| 回放幂等、且与终局一致 | `test_replay_is_deterministic_and_matches_final_state` |
| 回放前缀不会看到未来 | `test_replaying_a_prefix_never_sees_the_future` |
| 答案不进观察、不进公开状态 | `test_target_never_leaks_*` |
| 第一次报答案之前，事件流不含答案 | `test_no_event_payload_contains_the_target_before_the_end` |
| 猜错立即判负 | `test_guessing_wrong_immediately_loses` |
| 非法动作被记录并重试 | `test_illegal_move_is_logged_and_retried` |
| 重开连接后 `seq` 接着走 | `test_eventlog_resumes_seq_after_reopen` |
| HTTP 建场/404/参数校验 | `test_api.py` 前四条 |
| SSE 从 `Last-Event-ID` 续传 | `test_stream_replays_from_last_event_id` |
| 同种子可复现 | `test_seed_is_reproducible` |
| schema / pydantic / TS 三处一致 | `test_contract_parity.py` |
| 模型输出解析（纯 JSON / 代码块 / 带废话） | `test_llm_agent.py` 前六条 |
| 解析不出来 → 犯规，不猜 | `test_unparseable_becomes_illegal_not_guessed` |
| 思考流进 `thought.delta` | `test_reasoning_field_becomes_thought` |
| 上游 5xx 错误体脱敏 | `test_http_error_does_not_leak_key` |
| 提示词里没有 Key、没有答案 | `test_prompt_contains_no_secret_and_no_target` |
| Key 不进事件 / 视图 / SSE | `test_key_never_appears_in_events_or_view` |
| 连续犯规 → 弃权判负 | `test_illegal_llm_output_is_rejected_then_forfeits` |
| 导播 cue 有触发条件、序列确定 | `test_director.py` |
| 离线补 cue 与实时一致 | `test_offline_director_matches_live_director` |
| 循环赛配对、无重复、全覆盖 | `test_season.py` 前三条 |
| 积分幂等、同场不重复计 | `test_standings_are_idempotent`、`test_recording_same_match_twice_*` |
| 赛季存取往返一致 | `test_persist_and_load_roundtrip` |
| 高光包含报答案且有权重序 | `test_recap.py` |
| 战报数字与事件逐项对得上 | `test_recap_counts_match_events` + `test_recap_endpoint_is_consistent_with_events` |
| 8 人循环赛完整跑完并出榜 | `test_full_season_of_eight_players` |
| 流式思考分块、正文仍可解析 | `test_streaming.py` |
| 赛季一键跑完 / 断点续跑 / 榜单自洽 | `test_extras.py` 前三条 |
| 高光窗口不越界、不重叠 | `test_clips_are_within_source_events`、`test_clip_windows_do_not_overlap` |
| 解说事实校验（数字、判定词、长度） | `test_faithful_line_passes` 等四条 |
| 解说失败退回规则式 | `test_llm_narrator_falls_back_*` |
| 内存广播投递与隔离 | `test_memory_broadcaster_*` |
| 重放结果与直播终态一致 | `test_rehydrate_matches_live_result` |
| 进行中的比赛能重放出"轮到谁" | `test_summary_has_current_player_while_running` |
| 渲染出的视频可解码、帧数正确 | `test_render_produces_playable_file` |
| 同一事件画出的帧逐像素相同 | `test_frame_is_deterministic` |
| 局面面板统计全场而非窗口 | `test_context_counts_full_match_not_window` |
| 调度器按 tick 推进、跑完即停 | `test_scheduler_*` |
| 解说方向/角色/动作类型校验 | `test_pipeline.py` 末四条 |
| 队列租约、过期重领、次数上限 | `test_workqueue.py` 前九条 |
| worker 跑完整场并落终态 | `test_worker_runs_a_queued_match` |
| 崩溃后续跑不重复不跳号 | `test_queued_match_can_resume_after_crash` |
| 凭据加密往返、密文不含明文 | `test_secret_roundtrip`、`test_seal_specs_hides_api_key` |
| token 鉴权三种结果 | `test_auth_*` |
| 配额到顶返回 429、按 token 隔离 | `test_quota_*` |
| 限流阈值、XFF 取链尾 | `test_limiter_*`、`test_client_ip_*` |
| token 模式端到端 | `test_token_mode_end_to_end` |
| 人工 cue 入流、序号连续 | `test_push_cue_appends_event`、`test_cue_seq_stays_contiguous` |
| 非法镜头/负时长/已结束被拒 | `test_control.py` 中间四条 |
| 自动与人工分开计数 | `test_auto_and_manual_are_counted_separately` |
| 适配器不改旧 state（纯函数） | `test_apply_does_not_mutate_previous_state` |
| 每步 env 独立、不共享 | `test_env_instances_are_not_shared_between_states` |
| 四个棋类零犯规打完（12 组） | `test_board_agent_finishes_without_illegal_moves` |
| 只从最后一条观察取动作 | `test_extract_moves_reads_only_latest_observation` |
| 排期按轮次错开、可持久化 | `test_round_robin_schedules_rounds_apart` 等 |
| 代号：队长可见配色、队员不可 | `test_captain_sees_colors_agent_does_not` |
| 代号：未翻开词不暴露颜色 | `test_public_board_hides_unrevealed_colors` |
| 代号：翻刺客立即判负 | `test_hitting_assassin_loses_immediately` |
| 代号：默认选手 5 个种子零犯规 | `test_default_agent_finishes_without_illegal_moves` |
| 解说密度：每步都有话 | `test_every_applied_action_gets_a_narration_line` |
| 线索与翻牌都有解说 | `test_hint_is_narrated`、`test_reveal_is_narrated` |
| 局势播报不刷屏 | `test_situation_is_not_emitted_every_step` |

## 已知边界

1. **鉴权是静态 token，不是账号体系**。够防"别人白用额度"，不够做多租户。再往上应交给网关或 IdP。
2. **配额检查不是原子操作**。`quotas` 表共享，但"读-改-写"没有行锁，多副本同时建场可能各多算一次；限流已支持 Redis（`MELEE_RATELIMIT=redis`）。
3. **凭据加密依赖进程密钥**。`MELEE_SECRET_KEY` 泄漏等于密文可解；这不是 KMS，只解决静态落盘。
4. **语义校验是启发式的**。拦得住方向说反、角色错位、动作张冠李戴，拦不住更复杂的错误归因。
5. **`visibility` 已进契约、过滤已实现**。代号通过 `observe()` 裁剪隐藏信息（队长配色只进队长观察），但事件流本身尚无私有事件，`visibility` 仍无实际用例。
6. **模型超时等同一次犯规**，没有超时熔断与预算上限。
7. **前端零构建**。观战、赛季、战报、导播四个页面都是原生 ES module，没有构建链。
8. **媒体票据只存在进程内存**。多副本部署时签发与下载需落在同一副本（或把票据换到 Redis）；单副本不受影响。

## 验收记录（本次）

- 95 项测试全绿。
- 8 人循环赛 28 场跑完并出榜：灰隼 7-0、长明 5-2、白鹭 3-4……积分与胜负一致。
- HTTP 一键跑完 4 人赛季 6 场，重跑全部 skipped（幂等）。
- 赛季比赛可离线读取：`/clips` 给出 3 段高光（16.2 秒），`/recap` 一句话战报与事件计数一致。
- 真浏览器实测两轮：直播 78 条事件、候选收窄到 1、8 次切镜；赛季比赛 40 条事件、5 次切镜。
- Key 在事件、视图、SSE、SQLite 四处均未出现。
- 渲染实测：2 段高光共 4.8 秒、24 帧/段，ffmpeg 解码零错误，抽帧与网页构图一致。
- 跨副本实测：重启服务后 `/state` 重放日志仍给出 `winner=B, remaining=1, currentPlayer=null`。
- 队列实测：建场返回 `jobId`，`/jobs/{id}` 显示 `done`；任务失败会按 `attempts` 重试并在超限后标 `failed`。
- 前端实测（CDP 驱动真实点击）：高光面板出现两个可播放视频（5.4s / 54 帧）。
- 稳定性：全量 147 项连跑三次无抖动；`ruff check` 全清。
- 鉴权实测：无 token 401、错 token 401、正确 token 200。
- 配额实测：alice 第 3 次建场 429，bob 不受影响。
- 限流实测：第 10 次建场起 429。
- 加密实测：`jobs.payload` 里 `apiKey` 形如 `enc:v1:gAAAA…`，明文检索为空。
- 移动端实测：390×844 下顶栏单行、纵向堆叠、无横向溢出。
- 导播台实测：人工切镜写入 seq 4，统计显示"自动 0 / 人工 1"，记录与实时流同步更新。
- 赛季页实测：4 人 6 场，榜单 3-0 / 1-2 / 1-2 / 1-2，与 API 一致。
- 赛制扩充实测：4 个棋类 × 3 个种子共 12 组，全部零犯规完赛并分出胜负。
- 皮肤分层实测：四子棋对局 42 条事件，棋盘渲染、步数、标题全部正确。
- 六赛制 × 3 种子验收：二十问/井字棋/四子棋/黑白棋/取石子/代号 全部零犯规完赛。
- 浏览器实拍：二十问（提问记录）、井字棋（3×3 棋盘）、代号（5×5 词阵 + 4 张选手卡）均正确。

## 第十批验收记录（2026-10-21）

- 全量 `pytest` 229 项通过（60.8s），`ruff check` 全清，`node --check` 六个前端模块通过。
- SSE 实时性：worker 线程写事件后，订阅者在 1.4 秒内收到（修复前 51 条事件要等 15 秒心跳才补一批）。
- 管理接口：token 模式下 `/jobs`、`/jobs/{id}`、`/scheduler`、`/scheduler/tick`、`/seasons/{id}` 无 token 全部 401；带 token 200。
- 载荷脱敏：带 `sk-plaintext-leak` 的建场任务，`/jobs` 与 `/jobs/{id}` 响应里检索不到明文。
- 建场竞态：`inlineWorker=false` 建场后立刻请求 `/events` 返回 200 + 空数组，SSE 同样 200（此前 404）。
- 浏览器实测（Playwright + 真 Chromium）：井字棋拖动时间轴两次无 pageerror（修复前 `Cannot set properties of null`）；代号 4 人 35 条事件实时推进、4 张选手卡、5×5 词阵正确。
- token 模式浏览器实测：观战页能拉到 6 个赛制、建场后 26 条事件、SSE 已连接。
- 战报/高光：井字棋 1 条高光 1 段剪辑；代号 3 条高光 2 段剪辑；一句话战报分别为「A 获胜。」「红队获胜：翻到刺客「海洋」，蓝队立即判负。」
- 赛季页实测：建 `startAt=+1h` 的赛季，页面显示「即将开赛 10/21 23:26 · A vs D · 还有 60 分钟」，赛程每行带本地时间。

## 第十一批验收记录（2026-10-21 晚）

- 全量 `pytest`：279 passed / 1 skipped（Redis 集成项默认跳过），`MELEE_TEST_REDIS_URL` 提供后 280 passed；`ruff check` 全清；六个前端模块 `node --check` 通过；`scripts/check_licenses.py` 通过。
- 跨赛制一致性（新 `test_games_parity.py`，32 条）：六种赛制都能出解说与镜头；离线 `apply_director` 与直播镜头序列逐个相等；战报 `problems` 全空。
- 真实模型（Agnes-2.5-Flash，OpenAI 兼容端点）：二十问 49 事件、6 条思考、零犯规；井字棋 58 事件、7 解说、7 镜头、仅 1 次犯规；代号 21 事件、3 次犯规（其中两次是免费账号 429 限流导致的空动作）。Key 在事件、状态、战报中均未出现。
- 真实 LLM 解说（`MELEE_NARRATOR=llm`）：6 句解说中 1 句由模型产出并通过事实校验，其余按设计回退规则式。
- 双副本 + Redis（Docker `redis:7-alpine`）：A 副本建场、B 副本订阅 SSE，39 条事件在 3.6 秒内全部到达；Redis 限流两副本共享，12 次建场第 11 次起 429。
- 媒体票据：无凭据下载 401，Bearer 200，媒体票据 200，跨场票据 401。
- 前端：取石子堆条渲染、键盘 Space/←/→/L 实测生效，井字棋拖动时间轴两次无 `pageerror`。
