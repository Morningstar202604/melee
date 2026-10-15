import assert from 'node:assert/strict'
import { test } from 'node:test'
import { POST as pingRoute } from './route.ts'

function pingRequest(body: unknown, ip = '203.0.113.210'): Request {
  return new Request('http://localhost:3000/api/providers/ping', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-forwarded-for': ip },
    body: typeof body === 'string' ? body : JSON.stringify(body),
  })
}

test('ping：选手数超过 8 名返回 400', async () => {
  const contestants = Array.from({ length: 9 }, (_, index) => ({
    slot: index + 1,
    providerId: 'no-such',
    modelId: 'm',
    apiKey: 'k',
  }))
  const response = await pingRoute(pingRequest({ contestants }))
  assert.equal(response.status, 400)
  const payload = (await response.json()) as { error?: string }
  assert.match(payload.error ?? '', /8/)
})

test('ping：modelId / apiKey 超长返回 400', async () => {
  const longModel = await pingRoute(
    pingRequest({ contestants: [{ providerId: 'no-such', modelId: 'm'.repeat(201), apiKey: 'k' }] }),
  )
  assert.equal(longModel.status, 400)
  const longKey = await pingRoute(
    pingRequest({ contestants: [{ providerId: 'no-such', modelId: 'm', apiKey: 'k'.repeat(501) }] }, '203.0.113.211'),
  )
  assert.equal(longKey.status, 400)
})

test('ping：请求体超 64KB 返回 413', async () => {
  const response = await pingRoute(pingRequest('x'.repeat(70_000), '203.0.113.212'))
  assert.equal(response.status, 413)
})
