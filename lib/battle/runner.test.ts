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
  TurnPlan,
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

test('arena 上报的沙箱进度以 sandbox.result 事件进流，并先于 judge.done', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(async (subs, ctx) => {
      for (const sub of subs) {
        ctx.onSandboxResult?.({
          slot: sub.contestant.slot,
          status: 'ok',
          passed: 2,
          total: 3,
          timeMs: 120,
          memoryKb: null,
        })
      }
      return subs.map((sub) => ({
        contestantSlot: sub.contestant.slot,
        scores: { pass_rate: 0.67 },
        passed: 2,
        total: 3,
        rationale: '通过 2/3',
      }))
    }),
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['print(1)'] }],
        [2, { chunks: ['print(2)'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const played = await playBattle(
    { arena: 'hard-code', topic: '两数之和', contestants: twoContestants() },
    deps,
  )

  const sandboxEvents = played.events.filter(
    (event): event is Extract<BattleEvent, { type: 'sandbox.result' }> =>
      event.type === 'sandbox.result',
  )
  assert.equal(sandboxEvents.length, 2)
  assert.deepEqual(
    sandboxEvents.map((event) => event.slot).sort((a, b) => a - b),
    [1, 2],
  )
  assert.ok(sandboxEvents.every((event) => event.passed === 2 && event.total === 3))

  const sandboxIndex = played.events.findIndex((event) => event.type === 'sandbox.result')
  const judgeDoneIndex = played.events.findIndex((event) => event.type === 'judge.done')
  assert.ok(sandboxIndex >= 0, '必须出现 sandbox.result')
  assert.ok(judgeDoneIndex > sandboxIndex, 'sandbox.result 必须排在 judge.done 之前')
  assert.equal(played.events.at(-1)?.type, 'battle.completed')
})

test('软战场裁判分写入 judgements 表（带 position_index 与裁判模型）', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: makeArena(),
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['甲组立论'] }],
        [2, { chunks: ['乙组立论'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const played = await playBattle(
    { arena: 'soft', topic: '裁判分落库', contestants: twoContestants() },
    deps,
  )

  const snapshot = getSnapshot(played.battleId)
  assert.ok(snapshot)
  assert.equal(snapshot.judgements.length, 2)
  assert.deepEqual(
    snapshot.judgements.map((row) => row.position_index).sort((a, b) => a - b),
    [1, 2],
  )
  assert.ok(snapshot.judgements.every((row) => row.judge_model === JUDGE_CONTEXT.judgeModelId))
  const scores = JSON.parse(snapshot.judgements[0]!.scores_json) as Record<string, number>
  assert.deepEqual(scores, { logic: 8, evidence: 7, relevance: 9 })
})

test('多轮编排：两轮顺序推进、回合事件齐全、第二轮可见对手首轮发言', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const promptCalls: Array<{ round: number; slot: number; others: string }> = []
  const plan: TurnPlan = {
    totalRounds: 2,
    roundLabel: (round) => (round === 1 ? '第一轮' : '第二轮'),
    buildTurnMessages: (topic, round, slot, _own, others) => {
      promptCalls.push({ round, slot, others: JSON.stringify(others) })
      return { messages: [{ role: 'user', content: `R${round}/S${slot} 题目：${topic}` }] }
    },
  }
  const multiArena: Arena = { ...makeArena(), id: 'banter', plan, maxContestants: 2 }
  const deps: BattleDeps = {
    arena: multiArena,
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['甲1', '甲2'] }],
        [2, { chunks: ['乙1'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const played = await playBattle(
    { arena: 'banter', topic: '互怼题目', contestants: twoContestants() },
    deps,
  )

  const roundEvents = played.events.filter(
    (event): event is Extract<BattleEvent, { type: 'round.changed' }> => event.type === 'round.changed',
  )
  assert.deepEqual(roundEvents.map((event) => event.round), [1, 2])
  assert.ok(roundEvents.every((event) => event.totalRounds === 2))

  const startedEvents = played.events.filter(
    (event): event is Extract<BattleEvent, { type: 'contestant.started' }> =>
      event.type === 'contestant.started',
  )
  assert.deepEqual(startedEvents.map((event) => event.round).sort(), [1, 1, 2, 2])

  const doneEvents = played.events.filter(
    (event): event is Extract<BattleEvent, { type: 'contestant.done' }> => event.type === 'contestant.done',
  )
  assert.deepEqual(doneEvents.map((event) => event.round).sort(), [1, 1, 2, 2])

  const round2Slot1 = promptCalls.find((call) => call.round === 2 && call.slot === 1)
  const round2Slot2 = promptCalls.find((call) => call.round === 2 && call.slot === 2)
  assert.ok(round2Slot1 && round2Slot2, '第二轮两名选手都应各拿到一次提示词')
  assert.ok(round2Slot1.others.includes('乙1'), '1 号在第二轮必须能看到 2 号首轮发言')
  assert.ok(!round2Slot1.others.includes('甲1'), '1 号不应把自己的发言当作对手内容')
  assert.ok(round2Slot2.others.includes('甲1 甲2') || round2Slot2.others.includes('甲1甲2'))
  assert.ok(!round2Slot2.others.includes('乙1'))

  const snapshot = getSnapshot(played.battleId)
  assert.ok(snapshot)
  const row = snapshot.contestants.find((contestant) => contestant.slot === 1)
  const turns = JSON.parse(row!.output_text ?? '[]') as Array<{ round: number; text: string }>
  assert.equal(turns.length, 2)
  assert.equal(turns[0]?.text, '甲1甲2')
  assert.equal(turns[1]?.round, 2)
  assert.equal(snapshot.judgements.length, 2, '多轮赛场的裁判分同样要落库')
})

test('对战级超时：挂在半路的整场会被中止并走失败收口', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: { ...makeArena(), battleTimeoutMs: 80, turnTimeoutMs: 60000 },
    createModel: createModelFor(
      new Map([
        [1, { chunks: ['不会', '到达'], hang: true }],
        [2, { chunks: ['也不会到达'], hang: true }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const played = await playBattle(
    { arena: 'soft', topic: '超时题', contestants: twoContestants() },
    deps,
  )
  assert.equal(played.events.at(-1)?.type, 'battle.failed')
  assert.ok(played.phases.includes('failed'))
  const failed = played.events.filter((event) => event.type === 'contestant.failed')
  assert.equal(failed.length, 2)
  assert.ok(failed.every((event) => event.type === 'contestant.failed' && event.status === 'timeout'))
})

test('单回合超时走官方 timeout 选项：挂死的回合判 timeout，其余回合不受影响', async () => {
  const recorder: FakeRecorder = { signals: [], abortedStreams: 0 }
  const deps: BattleDeps = {
    arena: { ...makeArena(), turnTimeoutMs: 80, battleTimeoutMs: 60000 },
    createModel: createModelFor(
      new Map([
        [1, { chunks: [], hang: true }],
        [2, { chunks: ['正常作答'] }],
      ]),
      recorder,
    ),
    judgeContext: JUDGE_CONTEXT,
  }
  const played = await playBattle(
    { arena: 'soft', topic: '回合超时', contestants: twoContestants() },
    deps,
  )
  const failedEvents = played.events.filter(
    (event): event is Extract<BattleEvent, { type: 'contestant.failed' }> => event.type === 'contestant.failed',
  )
  assert.equal(failedEvents.length, 1)
  assert.equal(failedEvents[0]?.slot, 1)
  assert.equal(failedEvents[0]?.status, 'timeout')
  assert.equal(played.events.at(-1)?.type, 'battle.completed')
})
