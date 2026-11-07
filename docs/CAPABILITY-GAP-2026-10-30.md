# 能力覆盖审计（历史基线与当前状态）

更新时间：2026-10-30；本文件最初用于对比 melee 与 OpenCode。代码随后已补齐 P0/P1，不能再把本文早期“缺失”表格当作当前状态。

## 定位结论

melee 是 AI 对战转播服务，不是 OpenCode 式编码 agent。参赛模型得到的是公平、可复现的选手接口：裁剪后的观察、赛制规则、有限采样调用和一个合法动作。平台侧不提供模型联网、文件、子代理或多步工具循环；需要完整 agent 能力时，通过 HTTPAgent 在外部服务内部实现。

## 当前已实现

| 能力 | 当前实现 | 主要证据 |
|---|---|---|
| 模型接入 | OpenAI-compatible、Anthropic Messages、Gemini `generateContent` | `services/match/agents/llm.py`；`test_llm_protocols.py` |
| 选手提示词 | `systemPrompt`，平台保留合法动作和 JSON 约束 | `player_specs.py`；`test_llm_sampling.py` |
| 采样参数 | temperature、topP、seed、stop、penalty、stream | `player_specs.py`；`test_llm_sampling.py` |
| 选手自记忆 | `includeHistory`，只记录本选手动作/思考 | `llm.py`、`runner.py`；`test_llm_sampling.py` |
| 资源控制 | `maxTokens` 64–32000、`maxTotalTokens`、`turnTimeoutMs` | `player_specs.py`、`routes/matches.py`、`runner.py`；`test_turn_timeout.py` |
| 密钥引用 | `{env:VAR}`，缺失按结构化 player id 弃权 | `player_specs.py`、`worker.py`；协议测试 |
| HTTPAgent | 裁剪观察、合法动作、600 秒上限、deadlineMs、SSRF/重试/脱敏 | `http_agent.py`、`docs/API.md` |
| 转播能力 | 事件日志、SSE、回放、导播、战报、高光、分享、赛事库、赛季 | `services/match/routes/`、五页前端、对应测试 |
| 安全边界 | Fernet 队列封存、缺密钥拒绝入队、URL 凭据拒绝、422 脱敏、SSRF | `secrets.py`、`outbound.py`、`api.py`、安全回归测试 |

## OpenCode 对比：有意不提供

OpenCode 的 bash/read/write/edit/grep/glob/webfetch/MCP、权限策略、子代理、会话压缩、LSP、formatter、IDE 和插件机制不属于 melee 的公平选手接口。把这些能力放到平台侧会改变胜负规则；外部 agent 可通过 HTTPAgent 自行提供工具和多步规划。

## 仍然不做

- 平台侧模型工具调用、联网、文件访问、子代理和多步工具循环；
- 热加载赛制插件、通用事件 webhook、全量 TextArena 环境目录；
- 匿名投票、全站 Elo、账号型多租户 SaaS、计费和多队赛季积分。

## 复验口径

```bash
bash scripts/verify_all.sh
```

数字只采用本次命令输出；不要把历史报告中的测试数、覆盖率或外部服务联调结果复制到当前 README。真实 Anthropic/Gemini、Redis、ffmpeg、Docker 和浏览器内核仍需在目标环境单独验收。

## 历史来源

早期差距矩阵和 OpenCode 功能基准保留在 Git 历史中；当前 API 字段与行为以 `docs/API.md`、源码和测试为准。
