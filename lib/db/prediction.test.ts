import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { POST as predictRoute } from '../../app/api/battle/[id]/predict/route.ts'
import { closeDb } from './client.ts'
import { createBattle, getSnapshot, insertContestant } from './repo.ts'

const dbFile = join(
  'data',
  `test-predict-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`,
)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

function predictRequest(battleId: string, body: unknown): Request {
  return new Request(`http://localhost:3000/api/battle/${battleId}/predict`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

async function setupRunningBattle(): Promise<{ battleId: string; contestantId: string }> {
  const battleId = await createBattle({ arena: 'soft', topic: '押注测试', phase: 'running' })
  const contestantId = await insertContestant({
    battleId,
    slot: 1,
    providerId: 'ollama',
    modelId: 'qwen3',
    positionIndex: 1,
    status: 'running',
  })
  await insertContestant({
    battleId,
    slot: 2,
    providerId: 'deepseek',
    modelId: 'deepseek-chat',
    positionIndex: 2,
    status: 'running',
  })
  return { battleId, contestantId }
}

test('押注写入成功、快照可见，同一 voterKey 第二次押注 409', async () => {
  const { battleId, contestantId } = await setupRunningBattle()
  const voterKey = `voter-${Math.random().toString(36).slice(2)}`

  const first = await predictRoute(predictRequest(battleId, { contestantId, voterKey }), {
    params: Promise.resolve({ id: battleId }),
  })
  assert.equal(first.status, 200)

  const snapshot = getSnapshot(battleId)
  assert.equal(snapshot?.predictions.length, 1)
  assert.equal(snapshot?.predictions[0]?.contestant_id, contestantId)
  assert.equal(snapshot?.predictions[0]?.position_index, 1)

  const second = await predictRoute(predictRequest(battleId, { contestantId, voterKey }), {
    params: Promise.resolve({ id: battleId }),
  })
  assert.equal(second.status, 409)
})

test('押注：未知对战 404、未知选手 404、终态拒收 409', async () => {
  const { battleId, contestantId } = await setupRunningBattle()

  const noBattle = await predictRoute(
    predictRequest('no-such-battle', { contestantId, voterKey: 'v1' }),
    { params: Promise.resolve({ id: 'no-such-battle' }) },
  )
  assert.equal(noBattle.status, 404)

  const noContestant = await predictRoute(
    predictRequest(battleId, { contestantId: 'ghost', voterKey: 'v1' }),
    { params: Promise.resolve({ id: battleId }) },
  )
  assert.equal(noContestant.status, 404)

  const finished = await createBattle({
    arena: 'soft',
    topic: '已结束',
    phase: 'completed',
    status: 'completed',
  })
  const finishedContestant = await insertContestant({
    battleId: finished,
    slot: 1,
    providerId: 'ollama',
    modelId: 'qwen3',
    positionIndex: 1,
    status: 'ok',
  })
  const closed = await predictRoute(
    predictRequest(finished, { contestantId: finishedContestant, voterKey: 'v2' }),
    { params: Promise.resolve({ id: finished }) },
  )
  assert.equal(closed.status, 409)
})
