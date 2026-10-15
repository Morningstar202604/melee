/**
 * 双层限流（单 IP 频次 + 全局容量）+ 异常检测，按用途分档。
 *
 * 🔗 工程逻辑：
 *   - 每个档位独立计数：不同端点有不同的成本（建房 > 聊天 > 预检探针）
 *   - IP 维度：窗口内超过该档上限即拒绝；10 秒内被拒 3 次进入 60 秒封禁名单
 *   - 全局维度：所有档共享一个粗粒度总量帽，防分布式洪峰
 *   - 全部基于内存；单进程 / Serverless 冷启会重置——与"单实例演示 + 原型"定位一致。
 *     多实例同步需接 Redis sliding window（见 docs/audit 报告）
 */

const GLOBAL_WINDOW_MS = 10_000
const GLOBAL_MAX = 100
const ENTRY_TTL_MS = 600_000
const MAX_ENTRIES = 10_000
const BLOCK_DURATION_MS = 60_000
const BLOCK_TRIGGER_COUNT = 3

export interface LimiterProfile {
  windowMs: number
  maxPerWindow: number
  label: string
}

interface LimiterState {
  hits: Map<string, number[]>
  blocklist: Map<string, number>
  rateLimitEvents: Map<string, number[]>
}

/** 全局容量（所有档位共享） */
const globalHits: number[] = []

function pruneGlobal(now: number): void {
  while (globalHits.length > 0 && now - globalHits[0]! > GLOBAL_WINDOW_MS) {
    globalHits.shift()
  }
}

export interface Limiter {
  profile: LimiterProfile
  allow(key: string): boolean
  status(key: string): {
    hitsInWindow: number
    maxPerWindow: number
    globalHits: number
    globalMax: number
    blocked: boolean
    blockExpiresInMs: number
  }
}

function createLimiter(profile: LimiterProfile): Limiter {
  const state: LimiterState = {
    hits: new Map(),
    blocklist: new Map(),
    rateLimitEvents: new Map(),
  }

  function pruneExpired(now: number): void {
    for (const [key, timestamps] of state.hits) {
      const alive = timestamps.filter((at) => now - at < ENTRY_TTL_MS)
      if (alive.length === 0) state.hits.delete(key)
      else state.hits.set(key, alive)
    }
  }

  function enforceCapacity(): void {
    if (state.hits.size <= MAX_ENTRIES) return
    const overflow = state.hits.size - MAX_ENTRIES
    let removed = 0
    for (const key of state.hits.keys()) {
      state.hits.delete(key)
      removed += 1
      if (removed >= overflow) break
    }
  }

  function isBlocked(key: string, now: number): boolean {
    const until = state.blocklist.get(key)
    if (until === undefined) return false
    if (now > until) {
      state.blocklist.delete(key)
      return false
    }
    return true
  }

  function recordRateLimit(key: string, now: number): void {
    const events = (state.rateLimitEvents.get(key) ?? []).filter((at) => now - at < GLOBAL_WINDOW_MS)
    events.push(now)
    state.rateLimitEvents.set(key, events)
    if (events.length >= BLOCK_TRIGGER_COUNT) {
      state.blocklist.set(key, now + BLOCK_DURATION_MS)
      state.rateLimitEvents.delete(key)
    }
  }

  return {
    profile,
    allow(key: string): boolean {
      const now = Date.now()
      pruneExpired(now)
      pruneGlobal(now)

      if (isBlocked(key, now)) return false
      if (globalHits.length >= GLOBAL_MAX) return false
      globalHits.push(now)

      const recent = (state.hits.get(key) ?? []).filter((at) => now - at < profile.windowMs)
      if (recent.length >= profile.maxPerWindow) {
        state.hits.set(key, recent)
        recordRateLimit(key, now)
        return false
      }
      recent.push(now)
      state.hits.set(key, recent)
      enforceCapacity()
      return true
    },
    status(key: string) {
      const now = Date.now()
      const nowHits = (state.hits.get(key) ?? []).filter((at) => now - at < profile.windowMs).length
      const blocked = isBlocked(key, now)
      const blockUntil = state.blocklist.get(key) ?? 0
      return {
        hitsInWindow: nowHits,
        maxPerWindow: profile.maxPerWindow,
        globalHits: globalHits.length,
        globalMax: GLOBAL_MAX,
        blocked,
        blockExpiresInMs: blocked ? Math.max(0, blockUntil - now) : 0,
      }
    },
  }
}

/** 建房：最贵（一次会拉起 N 路模型调用），每分钟 5 次 */
export const battleLimiter = createLimiter({ windowMs: 60_000, maxPerWindow: 5, label: '建房' })

/** 单挑台聊天：每次一轮对话，每分钟 20 次（够正常用，挡住暴力刷） */
export const chatLimiter = createLimiter({ windowMs: 60_000, maxPerWindow: 20, label: '聊天' })

/** 预检 / 模型列表：轻量探针，每分钟 30 次 */
export const probeLimiter = createLimiter({ windowMs: 60_000, maxPerWindow: 30, label: '预检' })

/** 投票 / 押注：观众动作，允许较高频次，但必须有界 */
export const spectatorLimiter = createLimiter({ windowMs: 60_000, maxPerWindow: 60, label: '观众' })

/** 建房兼容入口（历史调用点与既有测试依赖此签名） */
export function allow(ip: string): boolean {
  return battleLimiter.allow(ip)
}

/** 查询一个 IP 的建房限流状态（用于运维工具 / 调试） */
export function rateLimitStatus(ip: string): ReturnType<Limiter['status']> {
  return battleLimiter.status(ip)
}

/** 统一的 429 响应体（各路由共用，措辞随档位变化） */
export function rateLimitMessage(profile: LimiterProfile, unit: string): string {
  const seconds = Math.round(profile.windowMs / 1000)
  return `请求过于频繁：每 IP 每 ${seconds} 秒最多 ${profile.maxPerWindow} ${unit}`
}
