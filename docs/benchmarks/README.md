# 并发对抗压测

这里保存 `scripts/stress_arena.py` 的原始 JSON 报告。脚本本身不模拟“模型智商”：
选手用 `delayMs` 模拟真实 AI 的思考节奏，验证的是建场、队列、worker、SSE 写入
与终态收尾在并发下是否收敛。真实 LLM 的提示词与工具链由单测和单场 live 验收覆盖。

## 2026-11-24

服务：本机隔离实例 `http://127.0.0.1:8910`，`MELEE_RATELIMIT=off`，SQLite 演示库副本。

| 档位 | 建场 | 完赛 | 卡死 | 赛制覆盖 | p50 | p95 | 事件 |
|---|---|---|---|---|---|---|---|
| 24 场（每赛制 2 份） | 24 | 24 | 0 | 12/12 | 4.2s | 7.6s | 4,094 |
| 48 场（每赛制 4 份） | 48 | 48 | 0 | 12/12 | 9.5s | 13.9s | 8,188 |

报告文件：[stress-arena-24.json](stress-arena-24.json)、[stress-arena-48.json](stress-arena-48.json)。

复现：

```bash
MELEE_DB=/tmp/melee-stress.db MELEE_RATELIMIT=off python scripts/serve.py --port 8910
python scripts/stress_arena.py --api http://127.0.0.1:8910 --copies 4 --timeout 150 \
  --out docs/benchmarks/stress-arena-48.json
```

注意：

- `--copies 4` 时每场都走 12 个赛制的真实引擎，不是 mock；SQLite 单库写入是当前瓶颈。
- 生产多副本应改用 Redis 广播与共享队列，并单独做租约/重试/死信演练（见路线图 1.11）。
- 无卡死指“在 150s 超时内都出现 `match.ended`”；慢请求不等于语义正确，仍需看终态事件。
