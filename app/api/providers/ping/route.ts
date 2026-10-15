import { createModel, getProvider } from '../../../../lib/providers/registry.ts'
import { streamModel } from '../../../../lib/providers/stream.ts'
import { redact } from '../../../../lib/log/redact.ts'
import { probeLimiter } from '../../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse, readJsonCapped } from '../../../../lib/http/request.ts'
import { secureResponse } from '../../../../lib/security.ts'

export const runtime = 'nodejs'

const PING_TIMEOUT_MS = 20000
const MAX_BODY_BYTES = 64 * 1024
const MAX_CONTESTANTS = 8
const MAX_MODEL_ID_LENGTH = 200
const MAX_API_KEY_LENGTH = 500

interface PingContestant {
  slot: number
  providerId: string
  modelId: string
  apiKey: string
}

interface PingResult {
  slot: number
  ok: boolean
  status: string
  message: string
}

export async function POST(request: Request) {
  const ip = clientIp(request)
  if (!probeLimiter.allow(ip)) {
    return rateLimitedResponse(probeLimiter, '次预检请求')
  }

  const read = await readJsonCapped<{ contestants?: unknown }>(request, MAX_BODY_BYTES)
  if (!read.ok) return read.response

  const raw = (read.body ?? {})?.contestants
  const contestants: PingContestant[] = Array.isArray(raw)
    ? raw.map((entry, index) => {
        const c = (entry ?? {}) as Record<string, unknown>
        return {
          slot: typeof c.slot === 'number' ? c.slot : index + 1,
          providerId: typeof c.providerId === 'string' ? c.providerId : '',
          modelId: typeof c.modelId === 'string' ? c.modelId : '',
          apiKey: typeof c.apiKey === 'string' ? c.apiKey : '',
        }
      })
    : []

  if (contestants.length === 0) {
    return secureResponse(JSON.stringify({ error: 'contestants 不能为空' }), { status: 400 })
  }
  if (contestants.length > MAX_CONTESTANTS) {
    return secureResponse(JSON.stringify({ error: `最多支持 ${MAX_CONTESTANTS} 名选手` }), {
      status: 400,
    })
  }
  for (const [index, contestant] of contestants.entries()) {
    if (contestant.modelId.length > MAX_MODEL_ID_LENGTH) {
      return secureResponse(
        JSON.stringify({ error: `第 ${index + 1} 名选手的模型 ID 超出长度上限` }),
        { status: 400 },
      )
    }
    if (contestant.apiKey.length > MAX_API_KEY_LENGTH) {
      return secureResponse(
        JSON.stringify({ error: `第 ${index + 1} 名选手的 API Key 超出长度上限` }),
        { status: 400 },
      )
    }
  }

  const results = await Promise.all(contestants.map((c) => pingOne(c)))
  return secureResponse(JSON.stringify({ results }))
}

async function pingOne(contestant: PingContestant): Promise<PingResult> {
  const { slot, providerId, modelId, apiKey } = contestant
  const provider = getProvider(providerId)
  if (!provider) {
    return { slot, ok: false, status: 'provider_error', message: `未知 provider：${providerId}` }
  }
  if (!modelId) {
    return {
      slot,
      ok: false,
      status: 'provider_error',
      message: `${provider.label} 没有填模型 id，请手填或从模型列表里选`,
    }
  }
  if (!apiKey) {
    return {
      slot,
      ok: false,
      status: 'auth_failed',
      message: `${provider.label} 没有填 API Key`,
    }
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), PING_TIMEOUT_MS)
  try {
    const model = createModel(provider, modelId, apiKey)
    let latencyMs = 0
    for await (const event of streamModel(model, [{ role: 'user', content: 'ping' }], {
      signal: controller.signal,
      apiKey,
      providerLabel: provider.label,
      maxOutputTokens: 16,
    })) {
      if (event.type === 'error') {
        return { slot, ok: false, status: event.status, message: event.message }
      }
      if (event.type === 'done') latencyMs = event.latencyMs
    }
    return {
      slot,
      ok: true,
      status: 'ok',
      message: `${provider.label} / ${modelId} 可达（延迟 ${latencyMs}ms）`,
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return {
      slot,
      ok: false,
      status: 'provider_error',
      message: redact(`${provider.label} 预检异常：${message}`, [apiKey]),
    }
  } finally {
    clearTimeout(timer)
  }
}
