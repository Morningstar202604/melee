import { spectatorLimiter } from '../../../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse } from '../../../../../lib/http/request.ts'
import { cancelBattle } from '../../../../../lib/battle/runner.ts'
import { secureResponse } from '../../../../../lib/security.ts'

export const runtime = 'nodejs'

export async function POST(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const ip = clientIp(request)
  if (!spectatorLimiter.allow(ip)) {
    return rateLimitedResponse(spectatorLimiter, '次取消')
  }

  const { id } = await ctx.params
  const cancelled = cancelBattle(id)
  if (!cancelled) {
    return secureResponse(JSON.stringify({ error: '对战不存在或已结束' }), { status: 404 })
  }
  return secureResponse(JSON.stringify({ ok: true }))
}
