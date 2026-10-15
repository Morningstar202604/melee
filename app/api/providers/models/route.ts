import { getProvider, listModels } from '../../../../lib/providers/registry.ts'
import { redact } from '../../../../lib/log/redact.ts'
import { logger } from '../../../../lib/log/logger.ts'
import { probeLimiter } from '../../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse, readJsonCapped } from '../../../../lib/http/request.ts'
import { secureResponse } from '../../../../lib/security.ts'

export const runtime = 'nodejs'

const MAX_BODY_BYTES = 8 * 1024

export async function POST(request: Request) {
  const ip = clientIp(request)
  if (!probeLimiter.allow(ip)) {
    return rateLimitedResponse(probeLimiter, '次拉取请求')
  }

  const read = await readJsonCapped<{ providerId?: unknown; apiKey?: unknown }>(
    request,
    MAX_BODY_BYTES,
  )
  if (!read.ok) return read.response
  const payload = read.body ?? {}
  const providerId = typeof payload.providerId === 'string' ? payload.providerId : ''
  const apiKey = typeof payload.apiKey === 'string' ? payload.apiKey : ''

  const provider = getProvider(providerId)
  if (!provider) {
    return secureResponse(JSON.stringify({ ok: false, reason: `未知 provider：${providerId}` }), {
      status: 400,
    })
  }

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 15000)
  try {
    const result = await listModels(provider, apiKey, { signal: controller.signal })
    return secureResponse(JSON.stringify(result))
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return secureResponse(JSON.stringify({ ok: false, reason: '获取模型列表超时（15s）' }), {
        status: 504,
      })
    }
    logger.error('模型列表拉取失败', {
      providerId,
      error: redact(error instanceof Error ? error.message : String(error), [apiKey]),
    })
    return secureResponse(JSON.stringify({ ok: false, reason: '获取模型列表失败，请稍后重试' }), {
      status: 502,
    })
  } finally {
    clearTimeout(timeoutId)
  }
}
