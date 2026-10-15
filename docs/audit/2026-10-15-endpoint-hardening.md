# API 公开端点收口批次（深度检查第二轮）

- 日期：2026-10-15
- 触发：全量深度检查——把程序完整跑起来且无问题；重点扫"不容易一眼看到的"面
- 纪律：不重复造轮子；每条修复都有行为锁或实测证据；发现即复现再修

## 一、批次范围

对全部 14 条路由（3 页面 + 11 API）做输入面 / 错误面 / 并发面 / 生命周期面的逐条扫描，
内容包括限流收口、请求体体积上限、安全头不变式、来源 IP 可信性，以及测试工具链缺口。

## 二、发现与修复（按维度）

| # | 维度 | 发现 | 证据 | 修复 |
|---|---|---|---|---|
| 1 | 安全 | 5 个端点（chat / ping / models / vote / predict）无限流；stream / snapshot / cancel 也无 | 逐路由读取确认 | `lib/battle/ratelimit.ts` 改分档限流器（建房 5/min、聊天 20/min、预检 30/min、观众 60/min），共享全局帽；全部 9 个 API 端点接入 `clientIp` + `rateLimitedResponse` |
| 2 | 安全 | [实测复现] `x-forwarded-for` 取首段可伪造：固定头第 6 次 429，但轮换伪造头 10/10 全过——append 型反代后每 IP 限流可被绕过 | `lib/http/request.ts:22` | `MELEE_TRUSTED_PROXY_HOPS`（默认 1）取 XFF 链尾第 N 项；伪造首段无效（行为锁：`'bogus, 1.2.3.4'` → `1.2.3.4`） |
| 3 | 安全 | [实测复现] `request.json()` 无体积上限：chunked 编码可绕 content-length 做内存放大 | `lib/http/request.ts:53` | `readJsonCapped` 双路径封死（声明的 content-length 超限即 413；无声明则边读边计数，超限 cancel + 413）；6 个带体端点全部改用 |
| 4 | 安全 | health 503 回显内部错误消息 | `app/api/health/route.ts:37` | 固定文案"数据库不可用"，细节只进日志 |
| 5 | 安全 | SSE 事件流、snapshot、cancel 响应无安全头（SDK 与手写 Response 绕过 `secureResponse`） | `app/api/battle/[id]/stream/route.ts:131` | `hardenResponse`（重建 Response 保留流与状态）+ 全部手写响应统一 `secureResponse`；新增不变式测试：9 条路由响应都必须带 nosniff/CSP/X-Frame-Options |
| 6 | 输入 | [实测复现] 非对象 body（`null` / `[]` / `"str"`）在 vote 路由触发 500（`body.contestantId` 抛 TypeError） | `app/api/battle/[id]/vote/route.ts` | `readJsonCapped` 空值兜底 + 回归锁（三种形态都必须 400） |
| 7 | 输入 | `voterKey` 无长度上限（可作卡位/放大载体）；ping 的 contestant 数量/字段无上限 | 两处路由 | `MAX_VOTER_KEY_LENGTH = 128`、ping 上限 8 名 + modelId/apiKey 长度上限 |
| 8 | 工具链 | [实测] `npm test` 的 glob 只覆盖 `lib/**`：`app/api/chat/route.test.ts` 从未被执行（既有 3 条测试长期空转） | `package.json:13` | glob 改为 `app/** lib/**`；测试数 159 → 182（新增 21 条 app 侧 + 2 条新锁） |
| 9 | 工具链 | [实测] 3 个路由（health / models / ping）用 `@/` 别名，`node --test` 无法解析 → 不可测 | `ERR_MODULE_NOT_FOUND: Cannot find package '@/lib'` | 改相对导入（与其余路由一致，无行为变化） |
| 10 | 边界 | chat 的 1MB 上限低于字段上限字节和（40 × 8000 CJK ≈ 960KB，emoji 可超）→ 合法请求会被 413 | `app/api/chat/route.ts:19` | 上限调 2MB 并写明推导依据；测试用例同步 |

## 三、逐项验证证据（生产构建）

- 全量测试 **182/182 全绿**（原 159 只覆盖 lib）；lint / tsc / build 全通过
- 生产构建端到端冒烟 **30/30**：页面 3 条、错误面 11 条、413 体积面 5 条、真实对战失败链路
  （SSE 7 事件 + snapshot 落库 + Key 不回显）、429 面 4 条（建房 6th / chat 21st / snapshot 61st / 另一 IP 不受影响）
- 并发面：同 voterKey 并发投票 5 路 → **恰好 1×200 + 4×409**（DB UNIQUE 兜底）
- 重放面：SSE 断开后带 `Last-Event-ID` 重连 → 从 lastId+1 续传，不重不漏含终态
- 生命周期：终态 60s 宽限内迟到连接可全量重放；宽限后 handle 回收 → stream 404 且指引
  snapshot，战报仍可读

## 四、审计记录（DE Lite 本地顾问式）

- 托管审计路径（Decision Engine MCP）在本会话不可用 → 按路由契约走本地顾问式
  （`local_host_f2a298496d2f43dad455367f`，`fallback_mode=session-llm`，`advisory_only=true`）
- 五维账本（logic / edge_cases / security / performance / concurrency）经
  `aqg_multi_review.py validate` 校验 **valid=true**；10 条发现全部带 `file:line` 证据，
  其中 7 条 `verified=true`（已复现并修复）
- **如实标注**：单模型本地审核不能代替跨厂商面板，不构成 PASS/clean 判定，不能关闭 AQG
  门禁；账本 decision 为 `needs-cross-llm-rerun`，提交决策保留给人

## 五、有意保留的限制（文档化，不在本批次闭合）

1. **限流状态为进程内存**：多实例部署会分裂、冷启重置。与"单实例演示"定位一致；多实例需
   接 Redis 滑动窗口（或交给平台层）。
2. **全局容量帽（100/10s）**：单源可打满造成全站 429（已有"3 次被拒 → 60s 封禁"缓和）。
   若上线需按真实流量重标定。
3. **直连暴露时来源 IP 不可信**：`MELEE_TRUSTED_PROXY_HOPS=0` 语义已在注释中说明；部署
   必须置于代管 XFF 的反代之后并显式设置 hops。
4. **限流键清理为每请求全量扫描**（≤10k 键）：当前规模可接受，压测出现热点再改惰性清理。
