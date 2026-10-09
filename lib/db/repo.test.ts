import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { closeDb } from './client.ts'
import {
  createBattle,
  getBattle,
  getSnapshot,
  insertContestant,
  recordJudgement,
  recordSandboxResult,
  recordVote,
  updateBattlePhase,
  updateContestantResult,
} from './repo.ts'

const dbFile = join('data', `test-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

async function seedBattle() {
  const battleId = await createBattle({ arena: 'soft', topic: '测试题目' })
  const first = await insertContestant({
    battleId,
    slot: 1,
    providerId: 'deepseek',
    modelId: 'deepseek-chat',
    positionIndex: 1,
    status: 'ok',
  })
  const second = await insertContestant({
    battleId,
    slot: 2,
    providerId: 'ollama',
    modelId: 'qwen3',
    positionIndex: 2,
    status: 'ok',
  })
  return { battleId, first, second }
}

test('createBattle 返回 id 并可读回', async () => {
  const id = await createBattle({ arena: 'soft', topic: '人类该不该躺平' })
  assert.ok(id.length > 0)
  const battle = getBattle(id)
  assert.ok(battle)
  assert.equal(battle?.topic, '人类该不该躺平')
  assert.equal(battle?.arena, 'soft')
  assert.equal(battle?.status, 'pending')
  assert.equal(battle?.phase, 'pending')
  assert.equal(battle?.error, null)
  assert.equal(battle?.completed_at, null)
})

test('同一 battle 下同一 voter_key 只能投一票', async () => {
  const { battleId, first } = await seedBattle()
  await recordVote({
    battleId,
    contestantId: first,
    voterKey: 'voter-1',
    positionIndex: 1,
  })
  await assert.rejects(
    () =>
      recordVote({
        battleId,
        contestantId: first,
        voterKey: 'voter-1',
        positionIndex: 1,
      }),
    /UNIQUE constraint failed/,
  )
})

test('vote 落库时写入 position_index', async () => {
  const { battleId, second } = await seedBattle()
  await recordVote({
    battleId,
    contestantId: second,
    voterKey: 'voter-position',
    positionIndex: 2,
  })
  const snapshot = getSnapshot(battleId)
  assert.equal(snapshot?.votes.length, 1)
  assert.equal(snapshot?.votes[0]?.position_index, 2)
  assert.equal(snapshot?.votes[0]?.contestant_id, second)
  assert.equal(snapshot?.votes[0]?.voter_key, 'voter-position')
})

test('contestants 落库 slot 与 position_index', async () => {
  const { battleId, second } = await seedBattle()
  const rows = getSnapshot(battleId)?.contestants ?? []
  assert.equal(rows.length, 2)
  assert.deepEqual(
    rows.map((r) => [r.slot, r.position_index]),
    [
      [1, 1],
      [2, 2],
    ],
  )
  assert.equal(rows[0]?.model_id, 'deepseek-chat')
  await updateContestantResult(second, {
    status: 'ok',
    outputText: '完整输出',
    code: null,
    promptTokens: 11,
    completionTokens: 22,
    latencyMs: 333,
  })
  const updated = getSnapshot(battleId)?.contestants.find((r) => r.id === second)
  assert.equal(updated?.status, 'ok')
  assert.equal(updated?.output_text, '完整输出')
  assert.equal(updated?.prompt_tokens, 11)
  assert.equal(updated?.completion_tokens, 22)
  assert.equal(updated?.latency_ms, 333)
})

test('updateBattlePhase 写入 phase 与 completed_at', async () => {
  const id = await createBattle({ arena: 'hard-code', topic: '两数之和' })
  await updateBattlePhase(id, 'running')
  assert.equal(getBattle(id)?.phase, 'running')
  await updateBattlePhase(id, 'failed', { completedAt: 1700000000000, error: '所有模型均不可达' })
  const battle = getBattle(id)
  assert.equal(battle?.phase, 'failed')
  assert.equal(battle?.status, 'failed')
  assert.equal(battle?.completed_at, 1700000000000)
  assert.equal(battle?.error, '所有模型均不可达')
})

test('recordJudgement 落库轮次、位置与 scores_json', async () => {
  const { battleId, first } = await seedBattle()
  await recordJudgement({
    battleId,
    contestantId: first,
    round: 1,
    judgeModel: 'deepseek-chat',
    positionIndex: 1,
    scoresJson: JSON.stringify({ logic: 8, evidence: 7, relevance: 9 }),
    rationale: '论证完整',
  })
  await recordJudgement({
    battleId,
    contestantId: first,
    round: 2,
    judgeModel: 'deepseek-chat',
    positionIndex: 2,
    scoresJson: JSON.stringify({ logic: 6, evidence: 6, relevance: 7 }),
    rationale: '位置对调后略降',
  })
  const judgements = getSnapshot(battleId)?.judgements ?? []
  assert.equal(judgements.length, 2)
  assert.deepEqual(
    judgements.map((j) => [j.round, j.position_index]),
    [
      [1, 1],
      [2, 2],
    ],
  )
  assert.equal(judgements[0]?.judge_model, 'deepseek-chat')
  assert.deepEqual(JSON.parse(judgements[0]?.scores_json ?? '{}'), {
    logic: 8,
    evidence: 7,
    relevance: 9,
  })
})

test('recordSandboxResult 落库 backend 与 isolation_level', async () => {
  const { battleId, first } = await seedBattle()
  await recordSandboxResult({
    contestantId: first,
    backend: 'pyodide',
    isolationLevel: 'wasm-worker',
    language: 'python',
    status: 'ok',
    passed: 3,
    total: 3,
    timeMs: 120,
    memoryKb: 2048,
    compileOutput: null,
    stdoutTail: 'ok\nok\nok\n',
    degraded: 0,
  })
  const results = getSnapshot(battleId)?.sandboxResults ?? []
  assert.equal(results.length, 1)
  assert.equal(results[0]?.backend, 'pyodide')
  assert.equal(results[0]?.isolation_level, 'wasm-worker')
  assert.equal(results[0]?.passed, 3)
  assert.equal(results[0]?.total, 3)
  assert.equal(results[0]?.degraded, 0)
})

test('getSnapshot 聚合四张表，未知 id 返回 undefined', async () => {
  const { battleId, first, second } = await seedBattle()
  await recordVote({ battleId, contestantId: second, voterKey: 'v-a', positionIndex: 1 })
  await recordVote({ battleId, contestantId: first, voterKey: 'v-b', positionIndex: 2 })
  await recordSandboxResult({
    contestantId: first,
    backend: 'pyodide',
    isolationLevel: 'wasm-worker',
    language: 'python',
    status: 'ok',
    passed: 1,
    total: 2,
    degraded: 0,
  })
  const snapshot = getSnapshot(battleId)
  assert.ok(snapshot)
  assert.equal(snapshot?.contestants.length, 2)
  assert.equal(snapshot?.votes.length, 2)
  assert.equal(snapshot?.sandboxResults.length, 1)
  assert.equal(getSnapshot('no-such-battle'), undefined)
})

test('并发写经 writeQueue 串行化，不丢不错', async () => {
  const battleId = await createBattle({ arena: 'soft', topic: '并发' })
  const results = await Promise.allSettled(
    Array.from({ length: 20 }, (_, i) =>
      insertContestant({
        battleId,
        slot: i,
        providerId: 'p',
        modelId: 'm',
        positionIndex: i,
        status: 'ok',
      }),
    ),
  )
  assert.ok(results.every((r) => r.status === 'fulfilled'))
  assert.equal(getSnapshot(battleId)?.contestants.length, 20)
})
