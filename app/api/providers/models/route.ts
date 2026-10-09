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

  const result = await listModels(provider, apiKey)
  return Response.json(result)
}
