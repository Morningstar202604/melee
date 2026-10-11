/**
 * 双层限流（IP 频次 + 全局容量）+ 异常检测。
 *
 * 🔗 工程逻辑：
 *   - IP 维度：60 秒窗口 5 次（防脚本刷创建）
 *   - 全局维度：10 秒 100 次（防分布式洪峰）
 *   - 异常检测：凡是过去 10 秒内触发 IP 限流超过 3 次的 IP，会被加入一个
 *     60 秒封禁名单（冷却期）。被全局容量挡掉的请求不会触发异常检测。
 *   - 全部基于内存；部署于 单进程 / Serverless 冷启会重置——这跟我们"单实例
 *     演示 + 原型"定位一致。如需持久化 + 多实例同步，应接入 Redis sliding window。
 */

const WINDOW_MS = 60_000
const MAX_PER_WINDOW = 5
const ENTRY_TTL_MS = 600_000
const MAX_ENTRIES = 10_000

const GLOBAL_WINDOW_MS = 10_000
const GLOBAL_MAX = 100

const hits = new Map<string, number[]>()

function pruneExpired(now: number): void {
  for (const [ip, timestamps] of hits) {
    const alive = timestamps.filter((at) => now - at < ENTRY_TTL_MS)
    if (alive.length === 0) hits.delete(ip)
    else hits.set(ip, alive)
  }
}

function enforceCapacity(): void {
  if (hits.size <= MAX_ENTRIES) return
  const overflow = hits.size - MAX_ENTRIES
  let removed = 0
  for (const ip of hits.keys()) {
    hits.delete(ip)
    removed += 1
    if (removed >= overflow) break
  }
}

// --- 全局容量 ---
const globalHits: number[] = []

function pruneGlobal(now: number): void {
  while (globalHits.length > 0 && now - globalHits[0] > GLOBAL_WINDOW_MS) {
    globalHits.shift()
  }
}

// --- 异常检测 ---
const BLOCK_DURATION_MS = 60_000
const blocklist = new Map<string, number>()
const rateLimitEvents = new Map<string, number[]>()

function isBlocked(ip: string, now: number): boolean {
  const until = blocklist.get(ip)
  if (until === undefined) return false
  if (now > until) {
    blocklist.delete(ip)
    return false
  }
  return true
}

function recordRateLimit(ip: string, now: number): void {
  const events = (rateLimitEvents.get(ip) ?? []).filter((at) => now - at < GLOBAL_WINDOW_MS)
  events.push(now)
  rateLimitEvents.set(ip, events)
  if (events.length >= 3) {
    blocklist.set(ip, now + BLOCK_DURATION_MS)
    rateLimitEvents.delete(ip)
  }
}

export function allow(ip: string): boolean {
  const now = Date.now()
  pruneExpired(now)
  pruneGlobal(now)

  if (isBlocked(ip, now)) return false

  if (globalHits.length >= GLOBAL_MAX) return false
  globalHits.push(now)

  const recent = (hits.get(ip) ?? []).filter((at) => now - at < WINDOW_MS)
  if (recent.length >= MAX_PER_WINDOW) {
    hits.set(ip, recent)
    recordRateLimit(ip, now)
    return false
  }
  recent.push(now)
  hits.set(ip, recent)
  enforceCapacity()
  return true
}

/**
 * 查询一个 IP 的当前限流状态（用于运维工具 / 调试）。
 */
export function rateLimitStatus(ip: string): {
  hitsInWindow: number
  maxPerWindow: number
  globalHits: number
  globalMax: number
  blocked: boolean
  blockExpiresInMs: number
} {
  const now = Date.now()
  const nowHits = (hits.get(ip) ?? []).filter((at) => now - at < WINDOW_MS).length
  const blocked = isBlocked(ip, now)
  const blockUntil = blocklist.get(ip) ?? 0
  return {
    hitsInWindow: nowHits,
    maxPerWindow: MAX_PER_WINDOW,
    globalHits: globalHits.length,
    globalMax: GLOBAL_MAX,
    blocked,
    blockExpiresInMs: blocked ? Math.max(0, blockUntil - now) : 0,
  }
}
