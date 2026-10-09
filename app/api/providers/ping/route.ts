import { createModel, getProvider } from '@/lib/providers/registry'
import { streamModel } from '@/lib/providers/stream'
import { redact } from '@/lib/log/redact'

export const runtime = 'nodejs'

const PING_TIMEOUT_MS = 20000

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
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return Response.json({ error: '请求体不是合法 JSON' }, { status: 400 })
  }

  const raw = (body as { contestants?: unknown } | null)?.contestants
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
    return Response.json({ error: 'contestants 不能为空' }, { status: 400 })
  }

  const results = await Promise.all(contestants.map((c) => pingOne(c)))
  return Response.json({ results })
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
