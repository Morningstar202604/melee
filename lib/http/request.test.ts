import assert from 'node:assert/strict'
import { test } from 'node:test'
import { clientIp, rateLimitedResponse, readJsonCapped } from './request.ts'
import { battleLimiter } from '../battle/ratelimit.ts'

function requestWithBody(body: string, headers: Record<string, string> = {}): Request {
  return new Request('http://localhost:3000/api/x', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body,
  })
}

test('clientIp：默认单层反代取 XFF 链尾，客户端伪造首段无效', () => {
  const saved = process.env.MELEE_TRUSTED_PROXY_HOPS
  try {
    delete process.env.MELEE_TRUSTED_PROXY_HOPS
    // 客户端伪造 'bogus'，append 型反代追加真实 IP → 链尾才是真实客户端
    const spoofed = new Request('http://localhost:3000', {
      headers: { 'x-forwarded-for': 'bogus, 1.2.3.4' },
    })
    assert.equal(clientIp(spoofed), '1.2.3.4')
    delete process.env.MELEE_TRUSTED_PROXY_HOPS
  } finally {
    if (saved === undefined) delete process.env.MELEE_TRUSTED_PROXY_HOPS
    else process.env.MELEE_TRUSTED_PROXY_HOPS = saved
  }
})

test('clientIp：MELEE_TRUSTED_PROXY_HOPS=2 取倒数第二项（两层代理链）', () => {
  const saved = process.env.MELEE_TRUSTED_PROXY_HOPS
  try {
    process.env.MELEE_TRUSTED_PROXY_HOPS = '2'
    const chained = new Request('http://localhost:3000', {
      headers: { 'x-forwarded-for': 'spoofed, 9.9.9.9, proxy-inner' },
    })
    assert.equal(clientIp(chained), '9.9.9.9')
  } finally {
    if (saved === undefined) delete process.env.MELEE_TRUSTED_PROXY_HOPS
    else process.env.MELEE_TRUSTED_PROXY_HOPS = saved
  }
})

test('clientIp：无 XFF 退回 x-real-ip，都没有则 unknown', () => {
  const viaReal = new Request('http://localhost:3000', { headers: { 'x-real-ip': '9.9.9.9' } })
  assert.equal(clientIp(viaReal), '9.9.9.9')
  assert.equal(clientIp(new Request('http://localhost:3000')), 'unknown')
})

test('readJsonCapped：正常解析 JSON', async () => {
  const read = await readJsonCapped<{ a: number }>(requestWithBody('{"a":1}'), 1024)
  assert.equal(read.ok, true)
  if (read.ok) assert.deepEqual(read.body, { a: 1 })
})

test('readJsonCapped：content-length 声明超限时直接 413（不读体）', async () => {
  const request = requestWithBody('{}', { 'content-length': '99999' })
  const read = await readJsonCapped(request, 1024)
  assert.equal(read.ok, false)
  if (!read.ok) {
    assert.equal(read.response.status, 413)
    assert.equal(read.response.headers.get('x-content-type-options'), 'nosniff')
    const payload = (await read.response.json()) as { error?: string }
    assert.match(payload.error ?? '', /上限/)
  }
})

test('readJsonCapped：无 content-length（chunked 形态）超限同样 413', async () => {
  const read = await readJsonCapped(requestWithBody('x'.repeat(2048)), 1024)
  assert.equal(read.ok, false)
  if (!read.ok) assert.equal(read.response.status, 413)
})

test('readJsonCapped：非法 JSON / 空体返回 400', async () => {
  const bad = await readJsonCapped(requestWithBody('not-json'), 1024)
  assert.equal(bad.ok, false)
  if (!bad.ok) assert.equal(bad.response.status, 400)
  const empty = await readJsonCapped(new Request('http://localhost:3000'), 1024)
  assert.equal(empty.ok, false)
  if (!empty.ok) assert.equal(empty.response.status, 400)
})

test('rateLimitedResponse：429 + 安全头 + 档位文案', async () => {
  const response = rateLimitedResponse(battleLimiter, '场对战')
  assert.equal(response.status, 429)
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff')
  const payload = (await response.json()) as { error?: string }
  assert.match(payload.error ?? '', /5 场对战/)
})
