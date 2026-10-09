import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { POST as runBattle } from '../../app/api/battle/run/route.ts'
import { POST as voteBattle } from '../../app/api/battle/[id]/vote/route.ts'
import { POST as cancelBattleRoute } from '../../app/api/battle/[id]/cancel/route.ts'
import { GET as getSnapshotRoute } from '../../app/api/battle/[id]/snapshot/route.ts'
import { cancelBattle } from './runner.ts'
import {
  createBattleHandle,
  getBattleHandle,
  registerBattle,
  unregisterBattle,
} from './registry.ts'
import { closeDb } from '../db/client.ts'
import { createBattle, insertContestant, getBattle } from '../db/repo.ts'

const dbFile = join(
  'data',
  `test-api-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`,
)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

const runUrl = 'http://localhost:3000/api/battle/run'

function voteRequest(battleId: string, body: unknown): Request {
  return new Request(`http://localhost:3000/api/battle/${battleId}/vote`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function snapshotRequest(id: string): Request {
  return new Request(`http://localhost:3000/api/battle/${id}/snapshot`)
}

function cancelRequest(id: string): Request {
  return new Request(`http://localhost:3000/api/battle/${id}/cancel`, { method: 'POST' })
}

function runRequest(body: unknown): Request {
  return new Request(runUrl, {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-forwarded-for': '203.0.113.7' },
    body: JSON.stringify(body),
  })
}

function validRunBody() {
  return {
    arena: 'soft',
    topic: '测试题目：人类该不该躺平',
    contestants: [
      { slot: 1, providerId: 'ollama', modelId: 'qwen3', apiKey: 'ollama' },
      { slot: 2, providerId: 'ollama', modelId: 'llama3', apiKey: 'ollama' },
    ],
  }
}

test('POST /api/battle/run 返回 202 与 battleId，后台对战不阻塞返回', async () => {
  const response = await runBattle(runRequest(validRunBody()))
  assert.equal(response.status, 202)
  const payload = (await response.json()) as { battleId?: string }
  assert.ok(payload.battleId, '响应必须带 battleId')
  const battle = getBattle(payload.battleId)
  assert.ok(battle)
  assert.equal(battle.arena, 'soft')
  assert.equal(battle.topic, '测试题目：人类该不该躺平')
  cancelBattle(payload.battleId)
})

test('run 在同一 IP 第 6 次调用时返回 429', async () => {
  let sawLimit = false
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const response = await runBattle(runRequest(validRunBody()))
    if (response.status === 429) {
      sawLimit = true
      break
    }
    assert.equal(response.status, 202, '未达上限前应放行')
    const payload = (await response.json()) as { battleId?: string }
    if (payload.battleId) cancelBattle(payload.battleId)
  }
  assert.ok(sawLimit, '同 IP 每分钟第 6 次必须 429')
})

test('同 battle 同 voterKey 第二次投票返回 409', async () => {
  const battleId = await createBattle({
    arena: 'soft',
    topic: '投票去重',
    phase: 'completed',
    status: 'completed',
  })
  const firstContestantId = await insertContestant({
    battleId,
    slot: 1,
    providerId: 'ollama',
    modelId: 'qwen3',
    positionIndex: 1,
    status: 'ok',
  })
  const secondContestantId = await insertContestant({
    battleId,
    slot: 2,
    providerId: 'ollama',
    modelId: 'llama3',
    positionIndex: 2,
    status: 'ok',
  })
  const voterKey = 'voter-' + Math.random().toString(36).slice(2)

  const first = await voteBattle(
    voteRequest(battleId, { contestantId: firstContestantId, voterKey }),
    { params: Promise.resolve({ id: battleId }) },
  )
  assert.equal(first.status, 200)

  const second = await voteBattle(
    voteRequest(battleId, { contestantId: secondContestantId, voterKey }),
    { params: Promise.resolve({ id: battleId }) },
  )
  assert.equal(second.status, 409, '同一 voterKey 在已投过的战场再次投票必须 409')
})

test('snapshot 对未知 id 返回 404', async () => {
  const response = await getSnapshotRoute(snapshotRequest('no-such-battle'), {
    params: Promise.resolve({ id: 'no-such-battle' }),
  })
  assert.equal(response.status, 404)
})

test('cancel 对未知 id 返回 404', async () => {
  const response = await cancelBattleRoute(cancelRequest('no-such-battle'), {
    params: Promise.resolve({ id: 'no-such-battle' }),
  })
  assert.equal(response.status, 404)
})

test('终态后 unregisterBattle 移除 handle，事件日志不会无限堆积', () => {
  const id = 'unreg-' + Math.random().toString(36).slice(2)
  const handle = createBattleHandle(id)
  registerBattle(id, handle)
  assert.ok(getBattleHandle(id))
  handle.emit({
    type: 'battle.completed',
    result: {
      battleId: id,
      arena: 'soft',
      topic: 't',
      contestants: [],
      verdicts: [],
      judgeAvailable: false,
    },
  })
  unregisterBattle(id)
  assert.equal(getBattleHandle(id), undefined)
})

test('SSE 内存契约：先 subscribe 再发 backlog，重叠事件不重不漏且 id 对齐', () => {
  const id = 'sse-' + Math.random().toString(36).slice(2)
  const handle = createBattleHandle(id)
  registerBattle(id, handle)

  handle.emit({ type: 'phase.changed', phase: 'running' })
  handle.emit({ type: 'contestant.started', slot: 1, providerId: 'ollama', modelId: 'qwen3' })

  const live: Array<{ eventId: number; type: string }> = []
  const unsubscribe = handle.subscribe((event, eventId) => {
    live.push({ eventId, type: event.type })
  })

  const backlog = handle.eventsSince(-1)
  let nextId = handle.nextId() - backlog.length
  const stream: Array<{ eventId: number; type: string }> = []
  for (const event of backlog) {
    stream.push({ eventId: nextId, type: event.type })
    nextId += 1
  }

  handle.emit({ type: 'contestant.delta', slot: 1, text: '你' })

  const seen = new Set(stream.map((entry) => entry.eventId))
  for (const entry of live) {
    if (!seen.has(entry.eventId)) stream.push(entry)
  }

  assert.deepEqual(stream, [
    { eventId: 0, type: 'phase.changed' },
    { eventId: 1, type: 'contestant.started' },
    { eventId: 2, type: 'contestant.delta' },
  ])
  assert.equal(new Set(stream.map((entry) => entry.eventId)).size, stream.length)
  assert.equal(handle.nextId(), stream.length)
  assert.deepEqual(
    handle.eventsSince(stream.length - 1),
    [],
    '终局之后 eventsSince 必须为空，Last-Event-ID 语义才不会错位',
  )

  unsubscribe()
  unregisterBattle(id)
})
