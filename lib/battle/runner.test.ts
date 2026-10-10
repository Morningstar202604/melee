import { APICallError } from 'ai'
import { MockLanguageModelV4 } from 'ai/test'
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import type { LanguageModel } from 'ai'
import type { LanguageModelV4StreamPart } from '@ai-sdk/provider'
import { closeDb } from '../db/client.ts'
import { getBattle, getSnapshot } from '../db/repo.ts'
import type {
  Arena,
  ContestantInput,
  ContestantStatus,
  JudgeContext,
  Submission,
} from '../arena/types.ts'
import type { BattleEvent, BattlePhase } from './events.ts'
import { getBattleHandle, type BattleHandle } from './registry.ts'
import { cancelBattle, startBattle, type BattleDeps, type StartBattleInput } from './runner.ts'

const dbFile = join(
  'data',
  `test-runner-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`,
)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

interface FakeScript {
  chunks: string[]
  fail?: ContestantStatus
  hang?: boolean
}

interface FakeRecorder {
  signals: Array<AbortSignal | undefined>
  abortedStreams: number
}

const USAGE = {
  inputTokens: { total: 11, noCache: undefined, cacheRead: undefined, cacheWrite: undefined },
  outputTokens: { total: 5, text: undefined, reasoning: undefined },
}

const PHASE_EVENTS = 'phase.changed'

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function scriptedStream(chunks: string[], close: boolean): ReadableStream<LanguageModelV4StreamPart> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: 'stream-start', warnings: [] })
      controller.enqueue({ type: 'text-start', id: '0' })
      for (const chunk of chunks) {
        controller.enqueue({ type: 'text-delta', id: '0', delta: chunk })
      }
      controller.enqueue({ type: 'text-end', id: '0' })
      if (close) {
        controller.enqueue({ type: 'finish', finishReason: { unified: 'stop', raw: undefined }, usage: USAGE })
        controller.close()
      }
    },
  })
}

function emptyStream(): ReadableStream<LanguageModelV4StreamPart> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: 'stream-start', warnings: [] })
      controller.enqueue({ type: 'finish', finishReason: { unified: 'stop', raw: undefined }, usage: USAGE })
      controller.close()
    },
  })
}

function failureError(status: ContestantStatus): unknown {
  if (status === 'auth_failed') {
    return new APICallError({
      message: 'invalid api key',
      url: 'https://fake.test/v1/chat/completions',
      requestBodyValues: {},
      statusCode: 401,
    })
  }
  if (status === 'rate_limited') {
    return new APICallError({
      message: 'too many requests',
      url: 'https://fake.test/v1/chat/completions',
      requestBodyValues: {},
      statusCode: 429,
    })
  }
  if (status === 'timeout') {
    return Object.assign(new Error('aborted by signal'), { name: 'AbortError' })
  }
  return new Error('provider exploded')
}

function failureStream(chunks: string[], status: ContestantStatus): ReadableStream<LanguageModelV4StreamPart> {
  return new ReadableStream({
    start(controller) {
      controller.enqueue({ type: 'stream-start', warnings: [] })
      controller.enqueue({ type: 'text-start', id: '0' })
      for (const chunk of chunks) {
        controller.enqueue({ type: 'text-delta', id: '0', delta: chunk })
      }
      controller.enqueue({ type: 'text-end', id: '0' })
      controller.enqueue({ type: 'error', error: failureError(status) })
    },
  })
}

function makeFakeModel(script: FakeScript, recorder: FakeRecorder): LanguageModel {
  return new MockLanguageModelV4({
    provider: 'fake',
    modelId: 'fake-model',
    doStream: async (options) => {
      recorder.signals.push(options.abortSignal)
      if (script.hang) {
        options.abortSignal?.addEventListener('abort', () => {
          recorder.abortedStreams += 1
        })
      }
      if (script.fail === 'empty') return { stream: emptyStream() }
      if (script.fail) return { stream: failureStream(script.chunks, script.fail) }
      return { stream: scriptedStream(script.chunks, !script.hang) }
    },
  })
}

function makeArena(judge?: Arena['judge']): Arena {
  return {
    id: 'soft',
    buildPrompt: (topic) => ({ system: '你是参赛选手', user: `题目：${topic}` }),
    extractCode: () => null,
    judge:
      judge ??
      (async (subs: Submission[]) =>
        subs.map((sub) => ({
          contestantSlot: sub.contestant.slot,
          scores: { logic: 8, evidence: 7, relevance: 9 },
          passed: 0,
          total: 0,
          rationale: '假裁判点评',
        }))),
  }
}

function contestant(slot: number, providerId: string, modelId: string): ContestantInput {
  return { slot, providerId, modelId, apiKey: `sk-test-${slot}` }
}

function twoContestants(): ContestantInput[] {
  return [
    contestant(1, 'deepseek', 'deepseek-chat'),
    contestant(2, 'ollama', 'qwen3'),
  ]
}

function createModelFor(scripts: Map<number, FakeScript>, recorder: FakeRecorder) {
  return (c: ContestantInput): LanguageModel =>
    makeFakeModel(scripts.get(c.slot) ?? { chunks: [] }, recorder)
}

const JUDGE_CONTEXT: JudgeContext = {
  topic: '人类该不该躺平',
  judgeModelId: 'judge-model',
  judgeApiKey: 'sk-judge',
}

interface Played {
  battleId: string
  handle: BattleHandle
  events: BattleEvent[]
  phases: BattlePhase[]
}

function isTerminal(events: BattleEvent[]): boolean {
  const last = events.at(-1)
  if (!last) return false
  if (last.type === 'battle.completed' || last.type === 'battle.failed') return true
  return last.type === PHASE_EVENTS && last.phase === 'cancelled'
}

async function playBattle(input: StartBattleInput, deps: BattleDeps): Promise<Played> {
  const battleId = await startBattle(input, deps)
  const handle = getBattleHandle(battleId)
  assert.ok(handle, 'startBattle 之后必须能按 id 取到 handle')

  const deadline = Date.now() + 15000
  for (;;) {
    if (isTerminal(handle.eventsSince(-1))) break
    if (Date.now() > deadline) assert.fail('对战未在 15s 内进入终态')
    await sleep(5)
  }
  await sleep(30)

  const events = handle.eventsSince(-1)
  return {
    battleId,
    handle,
    events,
    phases: events
      .filter((event): event is Extract<BattleEvent, { type: 'phase.changed' }> => event.type === PHASE_EVENTS)
      .map((event) => event.phase),
  }
}

function kinds(events: BattleEvent[]): string[] {
  return events.map((event) => event.type)
}

function slotKinds(events: BattleEvent[], slot: number): string[] {
  return events
    .filter((event) => 'slot' in event && event.slot === slot)
    .map((event) => event.type)
}

test('全部选手成功 → phase 依次 running/completed，每个选手恰好一次 contestant.done', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['你', '好'] }],
        [2, { chunks: ['世', '界'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
    now: () => 1700000000000,
  }
  const played = await playBattle(
    { arena: 'soft', topic: '人类该不该躺平', contestants: twoContestants() },
    deps,
  )

  assert.deepEqual(played.phases, ['running', 'judging', 'scoring', 'completed'])
  assert.equal(kinds(played.events).at(-1), 'battle.completed')
  assert.equal(kinds(played.events).filter((kind) => kind === 'contestant.done').length, 2)
  assert.deepEqual(slotKinds(played.events, 1), [
    'contestant.started',
    'contestant.delta',
    'contestant.delta',
    'contestant.done',
  ])
  assert.deepEqual(slotKinds(played.events, 2), [
    'contestant.started',
    'contestant.delta',
    'contestant.delta',
    'contestant.done',
  ])

  const firstStarted = played.events.findIndex((event) => event.type === 'contestant.started')
  const firstDelta = played.events.findIndex((event) => event.type === 'contestant.delta')
  const firstDone = played.events.findIndex((event) => event.type === 'contestant.done')
  assert.equal(firstStarted < firstDelta, true)
  assert.equal(firstDelta < firstDone, true)

  const completed = played.events.at(-1)
  assert.equal(completed?.type, 'battle.completed')
  if (completed?.type !== 'battle.completed') return
  assert.deepEqual(
    completed.result.contestants.map((c) => c.status),
    ['ok', 'ok'],
  )
  assert.equal(completed.result.judgeAvailable, true)
  assert.equal(completed.result.verdicts.length, 2)
  assert.equal(completed.result.arena, 'soft')
  assert.equal(
    completed.result.contestants.map((c) => c.outputLength).reduce((a, b) => a + b, 0),
    4,
  )

  const battle = getBattle(played.battleId)
  assert.equal(battle?.phase, 'completed')
  assert.equal(battle?.completed_at, 1700000000000)
  assert.equal(battle?.created_at, 1700000000000)

  const snapshot = getSnapshot(played.battleId)
  assert.deepEqual(
    snapshot?.contestants.map((c) => [c.slot, c.position_index, c.status]),
    [
      [1, 1, 'ok'],
      [2, 2, 'ok'],
    ],
  )
  assert.equal(snapshot?.contestants[0]?.output_text, '你好')
  assert.equal(snapshot?.contestants[1]?.output_text, '世界')
  assert.equal(snapshot?.contestants[0]?.prompt_tokens, 11)
  assert.equal(snapshot?.contestants[0]?.completion_tokens, 5)
  assert.equal("apiKey" in (snapshot?.contestants[0] ?? {}), false)
})

test('一个选手失败 → 其余仍完成，battle 仍 completed', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map<number, FakeScript>([
        [1, { chunks: ['答'], fail: 'auth_failed' }],
        [2, { chunks: ['正', '常'] }],
      ]),
      recorder,
    ),
  }
  const played = await playBattle(
    { arena: 'soft', topic: '躺平', contestants: twoContestants() },
    deps,
  )

  assert.deepEqual(played.phases, ['running', 'scoring', 'completed'])
  assert.equal(kinds(played.events).at(-1), 'battle.completed')
  assert.equal(kinds(played.events).includes('judge.done'), false)
  assert.deepEqual(slotKinds(played.events, 1), [
    'contestant.started',
    'contestant.delta',
    'contestant.failed',
  ])
  assert.deepEqual(slotKinds(played.events, 2), [
    'contestant.started',
    'contestant.delta',
    'contestant.delta',
    'contestant.done',
  ])

  const failedEvent = played.events.find((event) => event.type === 'contestant.failed')
  assert.equal(failedEvent?.type, 'contestant.failed')
  if (failedEvent?.type !== 'contestant.failed') return
  assert.equal(failedEvent.status, 'auth_failed')
  assert.ok(failedEvent.message.includes('鉴权失败'), failedEvent.message)
  assert.equal(failedEvent.message.includes('sk-test-1'), false)

  const completed = played.events.at(-1)
  assert.equal(completed?.type, 'battle.completed')
  if (completed?.type !== 'battle.completed') return
  assert.deepEqual(
    completed.result.contestants.map((c) => c.status),
    ['auth_failed', 'ok'],
  )
  assert.equal(completed.result.judgeAvailable, false)
  assert.deepEqual(completed.result.verdicts, [])

  const snapshot = getSnapshot(played.battleId)
  assert.equal(snapshot?.contestants[0]?.status, 'auth_failed')
  assert.ok(snapshot?.contestants[0]?.error_message?.includes('鉴权失败'))
  assert.equal(snapshot?.contestants[0]?.error_message?.includes('sk-test-1'), false)
  assert.equal(snapshot?.contestants[0]?.output_text, '答')
  assert.equal(snapshot?.contestants[1]?.status, 'ok')
  assert.equal(snapshot?.contestants[1]?.output_text, '正常')
})

test('全部失败 → battle 变 failed 且带可读 reason', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map<number, FakeScript>([
        [1, { chunks: [], fail: 'provider_error' }],
        [2, { chunks: [], fail: 'rate_limited' }],
      ]),
      recorder,
    ),
  }
  const played = await playBattle(
    { arena: 'soft', topic: '躺平', contestants: twoContestants() },
    deps,
  )

  assert.deepEqual(played.phases, ['running', 'failed'])
  assert.equal(kinds(played.events).at(-1), 'battle.failed')
  assert.equal(kinds(played.events).includes('contestant.done'), false)
  assert.equal(kinds(played.events).includes('contestant.started'), true)
  assert.equal(kinds(played.events).filter((kind) => kind === 'contestant.failed').length, 2)

  const failed = played.events.at(-1)
  assert.equal(failed?.type, 'battle.failed')
  if (failed?.type !== 'battle.failed') return
  assert.ok(failed.reason.includes('全部选手均失败'), failed.reason)
  assert.ok(failed.reason.includes('deepseek'), failed.reason)
  assert.ok(failed.reason.includes('ollama'), failed.reason)
  assert.equal(failed.reason.includes('sk-test'), false)

  const battle = getBattle(played.battleId)
  assert.equal(battle?.phase, 'failed')
  assert.ok(battle?.error?.includes('全部选手均失败'))
})

test('cancelBattle → phase 变 cancelled，在飞的流被 abort', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map<number, FakeScript>([
        [1, { chunks: ['一'], hang: true }],
        [2, { chunks: ['二'], hang: true }],
      ]),
      recorder,
    ),
    timeoutMs: 30000,
  }
  const battleId = await startBattle(
    { arena: 'soft', topic: '取消', contestants: twoContestants() },
    deps,
  )
  const handle = getBattleHandle(battleId)
  assert.ok(handle)

  const deadline = Date.now() + 10000
  while (recorder.signals.length < 2) {
    if (Date.now() > deadline) assert.fail('两个假 model 未在 10s 内开流')
    await sleep(5)
  }

  assert.equal(cancelBattle(battleId), true)
  assert.equal(cancelBattle(battleId), false)

  while (!isTerminal(handle.eventsSince(-1))) {
    if (Date.now() > deadline) assert.fail('取消后对战未进入终态')
    await sleep(5)
  }
  await sleep(30)

  const events = handle.eventsSince(-1)
  assert.deepEqual(
    events
      .filter((event): event is Extract<BattleEvent, { type: 'phase.changed' }> => event.type === PHASE_EVENTS)
      .map((event) => event.phase),
    ['running', 'cancelled'],
  )
  assert.equal(events.at(-1)?.type, PHASE_EVENTS)
  assert.equal(events.some((event) => event.type === 'battle.completed'), false)
  assert.equal(events.some((event) => event.type === 'battle.failed'), false)
  assert.equal(recorder.abortedStreams, 2)
  assert.ok(
    recorder.signals.every((signal) => signal?.aborted === true),
    '所有在飞流都应收到 abort',
  )

  const battle = getBattle(battleId)
  assert.equal(battle?.phase, 'cancelled')
})

test('事件 id 从 0 单调递增且不重复（Last-Event-ID 依赖它）', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['甲', '乙', '丙'] }],
        [2, { chunks: ['丁'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const battleId = await startBattle(
    { arena: 'soft', topic: '事件 id', contestants: twoContestants() },
    deps,
  )
  const handle = getBattleHandle(battleId)
  assert.ok(handle)

  const seenIds: number[] = []
  const seenTypes: string[] = []
  const unsubscribe = handle.subscribe((event, id) => {
    seenIds.push(id)
    seenTypes.push(event.type)
  })

  const deadline = Date.now() + 15000
  while (!isTerminal(handle.eventsSince(-1))) {
    if (Date.now() > deadline) assert.fail('对战未在 15s 内进入终态')
    await sleep(5)
  }
  await sleep(30)
  unsubscribe()

  assert.ok(seenIds.length >= 10, `事件数量应足够多，实际 ${seenIds.length}`)
  assert.equal(seenIds[0], 0)
  assert.deepEqual(
    seenIds,
    seenIds.map((_, index) => index),
  )
  assert.equal(new Set(seenIds).size, seenIds.length)

  const all = handle.eventsSince(-1)
  assert.equal(all.length, handle.nextId())
  assert.equal(all.length, seenIds.length)
  assert.deepEqual(
    all.map((event) => event.type),
    seenTypes,
  )
  assert.deepEqual(
    handle.eventsSince(0).length,
    all.length - 1,
  )
  assert.deepEqual(handle.eventsSince(all.length - 1), [])
  assert.equal(all.at(-1)?.type, 'battle.completed')
})
