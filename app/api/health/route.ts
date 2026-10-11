import { getDb } from '@/lib/db/client'
import { logger } from '@/lib/log/logger.ts'
import { getActiveBattleCount } from '@/lib/battle/registry.ts'
import { secureResponse } from '@/security.ts'

export const runtime = 'nodejs'

/**
 * 综合健康检查：
 *   - ok=true   → 200（DB 连接正常）
 *   - ok=false  → 503（DB 异常，service unavailable）
 *
 * 不主动暴露 IP / 内部细节到外部，仅返回聚合状态。
 */
export async function GET() {
  const startedAt = Date.now()
  try {
    const db = getDb()
    const row = db.prepare('SELECT COUNT(*) AS battle_count FROM battles').get() as
      | Record<string, unknown>
      | undefined
    const battleCount = Number(row?.battle_count ?? 0)
    const activeBattles = getActiveBattleCount()
    const uptimeMs = process.uptime() * 1000
    logger.info('health检查', { battleCount, activeBattles, durationMs: Date.now() - startedAt })
    return secureResponse(
      JSON.stringify({
        ok: true,
        battleCount,
        activeBattles,
        uptimeMs: Math.round(uptimeMs),
      }),
    )
  } catch (error) {
    logger.error('health检查失败', { error: error instanceof Error ? error.message : String(error) })
    return secureResponse(
      JSON.stringify({
        ok: false,
        error: `健康检查失败：${error instanceof Error ? error.message : '未知错误'}`,
      }),
      { status: 503 },
    )
  }
}
