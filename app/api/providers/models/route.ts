import { getProvider, listModels } from '@/lib/providers/registry'

export const runtime = 'nodejs'

export async function POST(request: Request) {
  let body: unknown
  try {
    body = await request.json()
  } catch {
    return Response.json({ ok: false, reason: '请求体不是合法 JSON' }, { status: 400 })
  }

  const payload = (body ?? {}) as { providerId?: unknown; apiKey?: unknown }
  const providerId = typeof payload.providerId === 'string' ? payload.providerId : ''
  const apiKey = typeof payload.apiKey === 'string' ? payload.apiKey : ''

  const provider = getProvider(providerId)
  if (!provider) {
    return Response.json({ ok: false, reason: `未知 provider：${providerId}` }, { status: 400 })
  }

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 15000)
  try {
    const result = await listModels(provider, apiKey, { signal: controller.signal })
    return Response.json(result)
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return Response.json({ ok: false, reason: '获取模型列表超时（15s）' }, { status: 504 })
    }
    throw error
  } finally {
    clearTimeout(timeoutId)
  }
}
