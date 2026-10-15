import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { hardenResponse } from './security.ts'
import { closeDb } from './db/client.ts'
import { GET as healthRoute } from '../app/api/health/route.ts'
import { POST as runBattle } from '../app/api/battle/run/route.ts'
import { POST as chatRoute } from '../app/api/chat/route.ts'
import { POST as voteBattle } from '../app/api/battle/[id]/vote/route.ts'
import { POST as predictBattle } from '../app/api/battle/[id]/predict/route.ts'
import { POST as cancelBattleRoute } from '../app/api/battle/[id]/cancel/route.ts'
import { GET as snapshotRoute } from '../app/api/battle/[id]/snapshot/route.ts'
import { POST as modelsRoute } from '../app/api/providers/models/route.ts'
import { POST as pingRoute } from '../app/api/providers/ping/route.ts'

const dbFile = join('data', `test-security-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

function post(url: string, body: unknown, ip = '203.0.113.200'): Request {
  return new Request(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-forwarded-for': ip },
    body: JSON.stringify(body),
  })
}

const params = { params: Promise.resolve({ id: 'no-such-battle' }) }

test('hardenResponse 叠加安全头且不覆盖已有头与状态', async () => {
  const base = new Response('{}', { status: 201, headers: { 'x-custom': 'keep' } })
  const hardened = hardenResponse(base)
  assert.equal(hardened.status, 201)
  assert.equal(hardened.headers.get('x-custom'), 'keep')
  assert.equal(hardened.headers.get('x-content-type-options'), 'nosniff')
  assert.ok(hardened.headers.get('content-security-policy'))
  assert.equal(await hardened.text(), '{}')
})

test('全部 API 路由的响应都带安全头（nosniff + CSP + frame-options）', async () => {
  const cases: Array<{ name: string; run: () => Promise<Response> }> = [
    { name: 'run 400', run: () => runBattle(post('http://localhost:3000/api/battle/run', {})) },
    { name: 'chat 400', run: () => chatRoute(post('http://localhost:3000/api/chat', {})) },
    {
      name: 'vote 404',
      run: () =>
        voteBattle(post('http://localhost:3000/api/battle/no-such-battle/vote', { contestantId: 'c', voterKey: 'v' }), params),
    },
    {
      name: 'predict 404',
      run: () =>
        predictBattle(post('http://localhost:3000/api/battle/no-such-battle/predict', { contestantId: 'c', voterKey: 'v' }), params),
    },
    {
      name: 'cancel 404',
      run: () =>
        cancelBattleRoute(
          new Request('http://localhost:3000/api/battle/no-such-battle/cancel', {
            method: 'POST',
            headers: { 'x-forwarded-for': '203.0.113.201' },
          }),
          params,
        ),
    },
    {
      name: 'snapshot 404',
      run: () =>
        snapshotRoute(
          new Request('http://localhost:3000/api/battle/no-such-battle/snapshot', {
            headers: { 'x-forwarded-for': '203.0.113.202' },
          }),
          params,
        ),
    },
    { name: 'models 400', run: () => modelsRoute(post('http://localhost:3000/api/providers/models', { providerId: 'no-such' }, '203.0.113.203')) },
    { name: 'ping 400', run: () => pingRoute(post('http://localhost:3000/api/providers/ping', { contestants: [] }, '203.0.113.204')) },
    { name: 'health 200', run: () => healthRoute() },
  ]
  for (const item of cases) {
    const response = await item.run()
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff', `${item.name} 缺 nosniff`)
    assert.ok(response.headers.get('content-security-policy'), `${item.name} 缺 CSP`)
    assert.equal(response.headers.get('x-frame-options'), 'DENY', `${item.name} 缺 X-Frame-Options`)
    await response.text().catch(() => {})
  }
})
