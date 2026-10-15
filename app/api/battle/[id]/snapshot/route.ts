import { spectatorLimiter } from '../../../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse } from '../../../../../lib/http/request.ts'
import { getSnapshot } from '../../../../../lib/db/repo.ts'
import { secureResponse } from '../../../../../lib/security.ts'

export const runtime = 'nodejs'

export async function GET(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const ip = clientIp(request)
  if (!spectatorLimiter.allow(ip)) {
    return rateLimitedResponse(spectatorLimiter, '次观战读取')
  }

  const { id } = await ctx.params
  const snapshot = getSnapshot(id)
  if (!snapshot) {
    return secureResponse(JSON.stringify({ error: '对战不存在' }), { status: 404 })
  }
  return secureResponse(JSON.stringify(snapshot))
}
