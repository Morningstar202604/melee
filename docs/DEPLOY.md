# 部署（反代 / 多副本 / 备份）

三种形态，按规模选。

## A. 单机单进程（默认）

```bash
python scripts/serve.py --host 0.0.0.0 --port 8099
```

前端与 API 同端口；SQLite WAL；内嵌 worker（默认 4 线程）跑比赛与渲染。

适用：个人 / 内网演示 / 单机 10–50 人同时观看。

## B. 反向代理 + 单副本

必须显式声明代理层数，否则 `X-Forwarded-For` 不被信任，限流会把所有请求当成同一个 IP：

```bash
MELEE_TRUSTED_PROXY_HOPS=1 \
MELEE_FORWARDED_ALLOW_IPS=127.0.0.1 \
MELEE_CORS_ORIGINS=https://melee.example.com \
python scripts/serve.py --host 127.0.0.1 --port 8099
```

`scripts/serve.py` 会校验：`MELEE_TRUSTED_PROXY_HOPS>0` 但没给 `MELEE_FORWARDED_ALLOW_IPS` 时直接退出（避免"以为开了鉴权其实没开"）。

Caddy 示例：

```caddyfile
melee.example.com {
    reverse_proxy 127.0.0.1:8099 {
        header_up X-Forwarded-For {remote_host}
    }
}
```

**注意**：`header_up X-Forwarded-For {remote_host}` 会覆写客户端伪造的值，这是必须的。
若用 nginx，用 `proxy_set_header X-Forwarded-For $remote_addr;` 而不是 `$proxy_add_x_forwarded_for`。

## C. API + 独立 worker + Redis

```bash
# 副本 1、2（只服务 HTTP，不跑任务）
MELEE_INLINE_WORKER=off MELEE_WORKER=off \
MELEE_BROADCAST=redis MELEE_REDIS_URL=redis://redis:6379/0 \
MELEE_RATELIMIT=redis \
MELEE_SECRET_KEY=... MELEE_TICKET_SECRET=... \
python scripts/serve.py --host 0.0.0.0 --port 8099

# worker（可多开，每台跑一个）
MELEE_WORKER=on MELEE_WORKERS=4 \
MELEE_BROADCAST=redis MELEE_REDIS_URL=redis://redis:6379/0 \
python -m services.match.worker --db /data/match.db
```

关键点：

| 事项 | 说明 |
|---|---|
| 数据库 | 所有副本共用一个 SQLite 文件（NFS 不推荐）；再往上换 Postgres 需改 `eventlog.py` / `workqueue.py` |
| 广播 | `MELEE_BROADCAST=redis`，否则 A 副本的观众看不到 B 副本的比赛 |
| 限流 | `MELEE_RATELIMIT=redis`，否则每副本各算一份 |
| 票据 | **无状态签名**，任何副本都能验签；`MELEE_TICKET_SECRET` 必须各副本一致 |
| 配额 | `quotas` 表共享，单语句条件更新，多副本不会超发 |
| 凭据 | `MELEE_SECRET_KEY` 必须各副本一致，否则队列里的密文在别的副本解不开 |

## D. 分享与对外展示

分享卡与分享页依赖 `request.base_url` 生成绝对 URL。反代后面部署时：

```bash
MELEE_CORS_ORIGINS=https://melee.example.com python scripts/serve.py --host 127.0.0.1 --port 8099
```

并确保反代不覆盖 `Host`（Caddy 默认透传，nginx 需要 `proxy_set_header Host $host;`）。
否则 `og:image` 会指向内网地址，社交平台的预览图会裂。

验证：

```bash
curl -s https://melee.example.com/share/<matchId> | grep 'og:image'
# 期望：og:image 是 https://melee.example.com/matches/<matchId>/card.png
```

## 备份与恢复

SQLite 在线备份（不锁库）：

```bash
sqlite3 /data/match.db ".backup '/backup/match-$(date +%F).db'"
```

恢复：停服务 → 用备份替换 `match.db`，删除同目录 `match.db-wal` / `match.db-shm` → 启动。
验证：`curl /health` 返回 `{"status":"ok"}` 且 `/matches` 能列出历史场次。

建议 cron 每日一次，保留 7 天。

## 健康检查与指标

| 端点 | 用途 | 典型用法 |
|---|---|---|
| `GET /health` | 数据库 + 队列可读 | K8s liveness/readiness、负载均衡健康检查 |
| `GET /metrics` | Prometheus 文本（HTTP 请求数/延迟/状态码 + 业务 Gauge） | Prometheus 抓取 |
| `GET /metrics/business` | 业务指标 JSON（每场事件数、队列状态） | 人工排查 |

告警建议（都是用户可见症状）：

| 告警 | 条件 | 处置 |
|---|---|---|
| 服务不可用 | `/health` 连续 3 次非 200 | 看容器日志、检查磁盘 |
| 请求错误率高 | `http_requests_total{status=~"5.."}` 5 分钟 > 1% | 看 traceback |
| 任务积压 | `melee_jobs_total{status="pending"} > 20` 持续 10 分钟 | 加 worker 或查卡住的任务 |
| 任务失败 | `melee_jobs_total{status="failed"} > 0` | `GET /jobs?status=failed` 看 `lastError` |
| 延迟劣化 | p95 `http_request_duration_highr_seconds` > 2s | 看慢查询 / 磁盘 |

## 回滚

镜像按 commit 打 tag；回滚就是 `docker run` 上一个 tag。
数据库 schema 只有加表/加列（`CREATE TABLE IF NOT EXISTS`），没有破坏性迁移，旧版本能读新库的既有表。
唯一注意：`jobs` / `events` 的历史数据新旧版本都兼容。

## 已知限制

1. **SQLite 单写**：并发写靠 WAL + busy_timeout + 序号重试，实测 6 线程 × 30 条无丢失；再高的写入量应换 Postgres。
2. **媒体票据无状态**，但**媒体文件在本地磁盘**：多副本必须共享同一 `clips/` 目录（或改用对象存储）。
3. **配额按 UTC 日**滚动，跨时区用户会看到"半夜重置"。
4. **限流按 IP**，NAT 后的多个用户共享额度。
