import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { GET as getStream } from '../../app/api/battle/[id]/stream/route.ts'
import { closeDb } from '../db/client.ts'
import { createBattleHandle, registerBattle, unregisterBattle } from './registry.ts'
import { BATTLE_DATA_PART } from './ui-stream.ts'
import type { BattleEvent } from './events.ts'

const dbFile = join(
  'data',
  `test-stream-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`,
)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

function streamRequest(id: string, lastEventId?: string): Request {
  return new Request(`http://localhost:3000/api/battle/${id}/stream`, {
    headers: lastEventId !== undefined ? { 'last-event-id': lastEventId } : {},
  })
}

function parseSse(text: string): Array<{ id: number; chunk: { type: string; data: BattleEvent } }> {
  const entries: Array<{ id: number; chunk: { type: string; data: BattleEvent } }> = []
  for (const block of text.split('\n\n')) {
    const idLine = block.split('\n').find((line) => line.startsWith('id: '))
    const dataLine = block.split('\n').find((line) => line.startsWith('data: '))
    if (!idLine || !dataLine) continue
    entries.push({ id: Number(idLine.slice(4)), chunk: JSON.parse(dataLine.slice(6)) })
  }
  return entries
}

test('SSE 走官方数据部件协议：data-battle 包裹事件、id 行与事件日志对齐、终态后收流', async () => {
  const battleId = `stream-test-${Math.random().toString(36).slice(2, 8)}`
  const handle = createBattleHandle(battleId)
  registerBattle(battleId, handle)
  handle.emit({ type: 'phase.changed', phase: 'running' })
  handle.emit({ type: 'contestant.started', slot: 1, providerId: 'ollama', modelId: 'm' })
  handle.emit({ type: 'contestant.delta', slot: 1, text: '你好' })
  handle.emit({ type: 'battle.failed', reason: '测试收流' })

  try {
    const response = await getStream(streamRequest(battleId), {
      params: Promise.resolve({ id: battleId }),
    })
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('content-type'), 'text/event-stream')
    const text = await response.text()
    const entries = parseSse(text)

    assert.equal(entries.length, 4, `期望 4 条事件，实际 ${entries.length}`)
    assert.deepEqual(
      entries.map((entry) => entry.chunk.type),
      [BATTLE_DATA_PART, BATTLE_DATA_PART, BATTLE_DATA_PART, BATTLE_DATA_PART],
      '全部事件都应包裹在官方数据部件里',
    )
    assert.deepEqual(
      entries.map((entry) => entry.id),
      [0, 1, 2, 3],
      'id 行必须从 0 连续递增（Last-Event-ID 依赖它）',
    )
    assert.equal(entries[2]?.chunk.data.type, 'contestant.delta')
    assert.deepEqual(
      entries[2]?.chunk.data,
      { type: 'contestant.delta', slot: 1, text: '你好' },
    )
  } finally {
    unregisterBattle(battleId)
  }
})

test('Last-Event-ID 重放：只回补缺失部分且 id 对齐', async () => {
  const battleId = `stream-replay-${Math.random().toString(36).slice(2, 8)}`
  const handle = createBattleHandle(battleId)
  registerBattle(battleId, handle)
  for (let slot = 1; slot <= 3; slot += 1) {
    handle.emit({ type: 'contestant.started', slot, providerId: 'ollama', modelId: 'm' })
  }
  handle.emit({ type: 'battle.failed', reason: '测试收流' })

  try {
    const response = await getStream(streamRequest(battleId, '1'), {
      params: Promise.resolve({ id: battleId }),
    })
    const entries = parseSse(await response.text())
    assert.equal(entries.length, 2, '只应回补 id>1 的事件（起始 3 条 + 终态共 4 条）')
    assert.deepEqual(
      entries.map((entry) => entry.id),
      [2, 3],
    )
    const first = entries[0]?.chunk.data
    assert.equal(first?.type, 'contestant.started')
    if (first?.type === 'contestant.started') {
      assert.equal(first.slot, 3)
    }
  } finally {
    unregisterBattle(battleId)
  }
})
