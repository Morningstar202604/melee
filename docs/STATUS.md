# 项目状态

这份文件只记录当前能力边界、复验入口和明确欠账，不固化会过期的测试数量或覆盖率。

## 当前能力

- 事件日志、REST、SSE、断线续传、状态快照、事件重放、多视角回放和赛后私有事件解锁。
- 内置二十问与代号；安装可选 TextArena 依赖后，注册表最多包含 12 个已验证赛制。
- 自动导播、人工切镜、规则式/可选 LLM 解说、战报事实校验、高光清单、分享卡和 OG 分享页。
- 赛事库、统计、在线人数、赛季循环赛和到点开赛。
- BYOK OpenAI-compatible/Anthropic/Gemini、采样参数、流式思考、选手自记忆、token 预算、`{env:VAR}` 和真实回合墙钟。
- HTTPAgent 外部接入；平台发送裁剪观察与合法动作，不发送隐藏信息。
- SSRF 门禁、Fernet 队列封存、Bearer 鉴权、配额、限流、安全响应头和审计脚本。

## 外部依赖边界

- TextArena 是可选依赖；未安装时只有内置赛制。
- 真实模型、Redis、ffmpeg、Docker daemon、浏览器内核和生产反向代理需要在目标环境单独验收。
- 自动化测试中的 FakeOpenAI、MockTransport、fakeredis 和静态 Docker 校验验证的是契约与错误处理，不是第三方服务的长期可用性保证。

## 明确不做

- 平台侧模型工具调用、联网、文件访问、子代理和多步工具循环；完整 agent 能力由 HTTPAgent 自己提供。
- 匿名投票、全站 Elo、账号型多租户 SaaS、计费、完整 TextArena 环境目录。
- 多队赛季积分、通用开放式主持赛制、分布式追踪和 OIDC 账号体系。

## 复验

```bash
bash scripts/verify_all.sh
```

门禁命令和每一步的解释见 [QUALITY.md](QUALITY.md)。

## 深入文档

- [API](API.md)
- [架构](ARCHITECTURE.md)
- [公平性](FAIRNESS.md)
- [部署](DEPLOY.md)
- [路线图](ROADMAP.md)
- [变更记录](../CHANGELOG.md)
