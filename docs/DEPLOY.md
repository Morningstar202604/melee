# Deployment

**English** · [简体中文](zh-CN/DEPLOY.md)

Four shapes, picked by scale.

## A. One process (default)

```bash
python scripts/serve.py --host 0.0.0.0 --port 8099
```

Front-end and API share a port; SQLite in WAL mode; an inline worker runs matches and renders. Good for a personal install, an internal demo, or a room of viewers on one machine.

## A2. Docker Compose

```bash
docker compose up --build
# http://127.0.0.1:8088/
```

`deploy/Caddyfile` serves static pages with `file_server` and reverse-proxies `/matches`, `/archive`, `/stats`, `/share` and SSE to `match:8099`, with buffering disabled for SSE.

Same-origin is not optional: the front-end calls the API at its own origin. If you split ports instead, pass `?api=http://127.0.0.1:8099` and set `MELEE_CORS_ORIGINS`; otherwise the archive, stats and watch pages will 404.

## B. Reverse proxy, one replica

Declare the proxy layer, or `X-Forwarded-For` is ignored and every request looks like one IP to the rate limiter:

```bash
MELEE_TRUSTED_PROXY_HOPS=1 \
MELEE_FORWARDED_ALLOW_IPS=127.0.0.1 \
MELEE_CORS_ORIGINS=https://melee.example.com \
python scripts/serve.py --host 127.0.0.1 --port 8099
```

`scripts/serve.py` exits if hops are declared without allowed proxy IPs — a half-configured proxy is worse than none.

Caddy:

```caddyfile
melee.example.com {
    reverse_proxy 127.0.0.1:8099 {
        header_up X-Forwarded-For {remote_host}
    }
}
```

`{remote_host}` overwrites a spoofed client header, which is exactly what you want. On nginx use `proxy_set_header X-Forwarded-For $remote_addr;`, not `$proxy_add_x_forwarded_for`.

## C. API, workers and Redis

```bash
# Replicas 1 and 2: HTTP only, no jobs
MELEE_INLINE_WORKER=off MELEE_WORKER=off \
MELEE_BROADCAST=redis MELEE_REDIS_URL=redis://redis:6379/0 \
MELEE_RATELIMIT=redis \
MELEE_SECRET_KEY=... MELEE_TICKET_SECRET=... \
python scripts/serve.py --host 0.0.0.0 --port 8099

# Workers, one or more per machine
MELEE_WORKER=on MELEE_WORKERS=4 \
MELEE_BROADCAST=redis MELEE_REDIS_URL=redis://redis:6379/0 \
python -m services.match.worker --db /data/match.db
```

| Item | Requirement |
|---|---|
| Database | One SQLite file shared by all replicas (NFS is not recommended); anything larger means replacing `eventlog.py` and `workqueue.py` |
| Broadcast | `MELEE_BROADCAST=redis`, or viewers on replica A cannot see matches on replica B |
| Rate limit | `MELEE_RATELIMIT=redis`, or each replica counts separately |
| Tickets | Stateless signatures; `MELEE_TICKET_SECRET` must match everywhere |
| Quota | The shared `quotas` table uses conditional single-statement updates, so replicas cannot over-issue |
| Secrets | `MELEE_SECRET_KEY` must match, or queued ciphertext cannot be opened elsewhere |

## D. Sharing behind a proxy

Share cards and share pages build absolute URLs from `request.base_url`. Keep the original `Host` (Caddy does by default; on nginx add `proxy_set_header Host $host;`), and set `MELEE_CORS_ORIGINS` to the public origin. Otherwise `og:image` points at an internal address and previews break.

```bash
curl -s https://melee.example.com/share/<matchId> | grep 'og:image'
# expect https://melee.example.com/matches/<matchId>/card.png
```

## Backup and restore

Online backup, no write lock:

```bash
sqlite3 /data/match.db ".backup '/backup/match-$(date +%F).db'"
```

Restore: stop the service, replace `match.db`, delete `match.db-wal` and `match.db-shm` beside it, start, then check `/health` and that `/matches` lists history. A daily cron with seven days of retention is sensible.

## Health and metrics

| Endpoint | Use |
|---|---|
| `GET /health` | Database and queue readable — liveness/readiness |
| `GET /metrics` | Prometheus: requests, latency, status codes, business gauges |
| `GET /metrics/business` | Human-readable business counters |

Alerts worth having: `/health` failing three times; 5xx above one percent for five minutes; `melee_jobs_total{status="pending"} > 20` for ten minutes; any `failed` job; p95 latency above two seconds.

## Rollback

Images are tagged by commit; roll back by running the previous tag. Schema changes only add tables or columns (`CREATE TABLE IF NOT EXISTS`, guarded `ALTER TABLE`), so an old build reads existing tables. `jobs` and `events` are compatible in both directions.

## Known limits

1. SQLite is single-writer. WAL, `busy_timeout` and `seq` retries held up under six threads × thirty writes; heavier loads need Postgres.
2. Media tickets are stateless, but files live on local disk. Multiple replicas must share the `clips/` directory or use object storage.
3. Quotas roll over on the UTC day.
4. Rate limits are per IP; users behind one NAT share a bucket.
