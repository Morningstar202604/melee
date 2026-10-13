import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  createBattleStore,
  fullText,
  isTerminalPhase,
  type FrameScheduler,
} from './battle-store.ts'
import type { BattleEvent } from '../battle/events.ts'
import type { Snapshot } from '../db/repo.ts'

interface FakeScheduler extends FrameScheduler {
  readonly pending: boolean
  readonly schedules: number
  readonly cancels: number
  runFrame(): void
}

function makeScheduler(): FakeScheduler {
  let flush: (() => void) | null = null
  let schedules = 0
  let cancels = 0
  return {
    get pending() {
      return flush !== null
    },
    get schedules() {
      return schedules
    },
    get cancels() {
      return cancels
    },
    schedule(callback: () => void) {
      flush = callback
      schedules += 1
      return {
        cancel: () => {
          cancels += 1
          if (flush === callback) flush = null
        },
      }
    },
    runFrame() {
      const callback = flush
      flush = null
      callback?.()
    },
  }
}

function delta(slot: number, text: string): BattleEvent {
  return { type: 'contestant.delta', slot, text }
}

function started(slot: number, providerId: string, modelId: string): BattleEvent {
  return { type: 'contestant.started', slot, providerId, modelId }
}

function done(slot: number, latencyMs: number): BattleEvent {
  return {
    type: 'contestant.done',
    slot,
    usage: { prompt: 10, completion: 20 },
    latencyMs,
  }
}

function failed(slot: number, status: 'timeout' | 'auth_failed', message: string): BattleEvent {
  return { type: 'contestant.failed', slot, status, message }
}

function makeSnapshot(): Snapshot {
  return {
    battle: {
      id: 'b1',
      arena: 'soft',
      topic: '辩论：AI 会不会做梦',
      status: 'running',
      phase: 'running',
      created_at: 1700000000000,
      completed_at: null,
      error: null,
    },
    contestants: [
      {
        id: 'c1',
        battle_id: 'b1',
        slot: 1,
        provider_id: 'ollama',
        model_id: 'qwen3',
        position_index: 1,
        prompt_tokens: null,
        completion_tokens: null,
        latency_ms: 1200,
        output_text: '会被撤回的旧文本',
        code: null,
        status: 'ok',
        error_message: null,
      },
      {
        id: 'c2',
        battle_id: 'b1',
        slot: 2,
        provider_id: 'deepseek',
        model_id: 'deepseek-chat',
        position_index: 2,
        prompt_tokens: null,
        completion_tokens: null,
        latency_ms: null,
        output_text: null,
        code: null,
        status: 'running',
        error_message: null,
      },
    ],
    votes: [
      {
        id: 'v1',
        battle_id: 'b1',
        contestant_id: 'c1',
        voter_key: 'voter',
        position_index: 1,
        created_at: 1700000005000,
      },
    ],
    predictions: [],
    judgements: [],
    sandboxResults: [],
  }
}

function applyAll(store: ReturnType<typeof createBattleStore>, events: BattleEvent[]): void {
  for (const event of events) store.getState().apply(event)
}

test('delta 在帧边界合并为一次提交，文本按顺序累积', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  let commits = 0
  store.subscribe(() => {
    commits += 1
  })

  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, '你'), delta(1, '好'), delta(1, '，世界')])
  assert.equal(commits, 0)
  assert.equal(scheduler.pending, true)
  assert.equal(scheduler.schedules, 1)

  scheduler.runFrame()
  assert.equal(commits, 1)
  const view = store.getState().contestants[1]
  assert.equal(fullText(view), '你好，世界')
  assert.equal(view.status, 'streaming')
  assert.equal(view.providerId, 'ollama')
  assert.equal(view.modelId, 'qwen3')
})

test('开始事件后重放同一批 delta 不会重复追加', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)

  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, 'AB')])
  scheduler.runFrame()
  assert.equal(fullText(store.getState().contestants[1]), 'AB')

  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, 'AB')])
  scheduler.runFrame()
  assert.equal(fullText(store.getState().contestants[1]), 'AB')
})

test('断线后补发的 delta 接在已有文本后面，第一手不会重复', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)

  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, 'AB'), done(1, 500)])
  scheduler.runFrame()
  assert.equal(store.getState().contestants[1].isFirstFinisher, true)
})

test('先完成者拿到先手标记，后来者拿不到', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [
    started(1, 'ollama', 'qwen3'),
    started(2, 'deepseek', 'deepseek-chat'),
    done(2, 800),
    done(1, 1500),
  ])
  scheduler.runFrame()

  const contestants = store.getState().contestants
  assert.equal(contestants[2].isFirstFinisher, true)
  assert.equal(contestants[1].isFirstFinisher, false)
  assert.equal(contestants[2].status, 'ok')
  assert.equal(contestants[2].latencyMs, 800)
})

test('失败保留后端给的可读原因，timeout 走超时状态', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [
    started(1, 'deepseek', 'deepseek-chat'),
    failed(1, 'auth_failed', 'deepseek 鉴权失败（HTTP 401），请检查 Key 是否有效'),
    started(2, 'ollama', 'qwen3'),
    failed(2, 'timeout', 'ollama 超时未完成'),
  ])
  scheduler.runFrame()

  const contestants = store.getState().contestants
  assert.equal(contestants[1].status, 'auth_failed')
  assert.equal(
    contestants[1].failureMessage,
    'deepseek 鉴权失败（HTTP 401），请检查 Key 是否有效',
  )
  assert.equal(contestants[2].status, 'timeout')
  assert.equal(contestants[2].failureMessage, 'ollama 超时未完成')
})

test('投票事件用整表覆盖，重复事件不累加', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [{ type: 'vote.tally', tally: { c1: 2, c2: 1 } }])
  scheduler.runFrame()
  applyAll(store, [{ type: 'vote.tally', tally: { c1: 2, c2: 1 } }])
  scheduler.runFrame()
  assert.deepEqual(store.getState().voteTally, { c1: 2, c2: 1 })
})

test('hydrate 用服务端快照整体覆盖（phase、起算时间、票数、文本）', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, '实时流里的文本')])
  scheduler.runFrame()

  store.getState().hydrate(makeSnapshot())
  const state = store.getState()
  assert.equal(state.phase, 'running')
  assert.equal(state.startedAt, 1700000000000)
  assert.deepEqual(state.voteTally, { c1: 1 })
  assert.equal(fullText(state.contestants[1]), '会被撤回的旧文本')
  assert.equal(state.contestants[1].contestantId, 'c1')
  assert.equal(state.contestants[1].status, 'ok')
  assert.equal(state.contestants[2].status, 'streaming')
  assert.equal(scheduler.cancels, 0)
})

test('hydrate 取消尚未提交的帧，快照之后的内容不被草稿冲掉', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, '实时流里的文本')])
  store.getState().hydrate(makeSnapshot())
  scheduler.runFrame()

  assert.equal(fullText(store.getState().contestants[1]), '会被撤回的旧文本')
})

test('终态事件立即提交并取消帧调度', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, '第一段')])

  applyAll(store, [
    {
      type: 'battle.completed',
      result: {
        battleId: 'b1',
        arena: 'soft',
        topic: '辩论：AI 会不会做梦',
        contestants: [
          {
            slot: 1,
            providerId: 'ollama',
            modelId: 'qwen3',
            status: 'ok',
            usage: { prompt: 10, completion: 20 },
            latencyMs: 900,
            outputLength: 3,
            code: null,
            errorMessage: null,
          },
        ],
        verdicts: [],
        judgeAvailable: false,
      },
    },
  ])

  assert.equal(scheduler.pending, false)
  assert.equal(scheduler.cancels, 1)
  assert.equal(store.getState().phase, 'completed')
  assert.equal(fullText(store.getState().contestants[1]), '第一段')
  assert.notEqual(store.getState().summary, null)
  scheduler.runFrame()
  assert.equal(store.getState().phase, 'completed')
})

test('整场失败写入可读原因并落终态', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [{ type: 'battle.failed', reason: '全部选手均失败，本场无法继续' }])
  assert.equal(store.getState().phase, 'failed')
  assert.equal(store.getState().failureReason, '全部选手均失败，本场无法继续')
})

test('phase 事件更新阶段，isTerminalPhase 判定三个终态', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [{ type: 'phase.changed', phase: 'judging' }])
  scheduler.runFrame()
  assert.equal(store.getState().phase, 'judging')

  applyAll(store, [{ type: 'phase.changed', phase: 'cancelled' }])
  assert.equal(store.getState().phase, 'cancelled')
  assert.equal(isTerminalPhase('cancelled'), true)
  assert.equal(isTerminalPhase('completed'), true)
  assert.equal(isTerminalPhase('failed'), true)
  assert.equal(isTerminalPhase('running'), false)
})

test('首个开始事件回填起算时间，之后不被覆盖', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [started(1, 'ollama', 'qwen3'), started(2, 'deepseek', 'deepseek-chat')])
  scheduler.runFrame()
  const first = store.getState().startedAt
  assert.notEqual(first, null)
  assert.ok(typeof first === 'number')
  assert.ok(first > 0)
  assert.equal(store.getState().startedAt, first)
})

test('每帧最多一次 set，多帧连续推进各提交一次', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  let commits = 0
  store.subscribe(() => {
    commits += 1
  })

  applyAll(store, [started(1, 'ollama', 'qwen3'), delta(1, '甲')])
  scheduler.runFrame()
  applyAll(store, [delta(1, '乙'), delta(1, '丙')])
  scheduler.runFrame()
  applyAll(store, [delta(1, '丁')])
  scheduler.runFrame()

  assert.equal(commits, 3)
  assert.equal(scheduler.schedules, 3)
  assert.equal(fullText(store.getState().contestants[1]), '甲乙丙丁')
})

test('hydrate 记录 hydratedBattleId，别的对战快照不会误判为已 hydration', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  assert.equal(store.getState().hydratedBattleId, null)
  store.getState().hydrate(makeSnapshot())
  assert.equal(store.getState().hydratedBattleId, 'b1')
})

test('delta 先于 started 到达时被丢弃，重连补发不会错位到别的选手', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [delta(1, '孤儿文本')])
  scheduler.runFrame()
  assert.equal(store.getState().contestants[1], undefined)
})

test('快照兜底时按已落库耗时把先手标给最快的完成者', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  const snapshot = makeSnapshot()
  snapshot.contestants[1].latency_ms = 2400
  snapshot.contestants.push({
    id: 'c3',
    battle_id: 'b1',
    slot: 3,
    provider_id: 'zhipu',
    model_id: 'glm-4',
    position_index: 3,
    prompt_tokens: null,
    completion_tokens: null,
    latency_ms: 800,
    output_text: '第三名',
    code: null,
    status: 'ok',
    error_message: null,
  })
  store.getState().hydrate(snapshot)
  const contestants = store.getState().contestants
  assert.equal(contestants[3].isFirstFinisher, true)
  assert.equal(contestants[1].isFirstFinisher, false)
  assert.equal(contestants[2].isFirstFinisher, false)
})

test('已结束的软战场快照能重建战报：裁判分取均值、标注裁判可用', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  const snapshot = makeSnapshot()
  snapshot.battle.phase = 'completed'
  snapshot.battle.status = 'completed'
  snapshot.contestants[1].status = 'ok'
  snapshot.judgements = [
    {
      id: 'j1',
      battle_id: 'b1',
      contestant_id: 'c1',
      round: 0,
      judge_model: 'deepseek:deepseek-chat',
      position_index: 1,
      scores_json: JSON.stringify({ logic: 8, evidence: 7, relevance: 9 }),
      rationale: '立论稳',
      created_at: 1700000006000,
    },
    {
      id: 'j2',
      battle_id: 'b1',
      contestant_id: 'c1',
      round: 0,
      judge_model: 'deepseek:deepseek-chat',
      position_index: 1,
      scores_json: JSON.stringify({ logic: 6, evidence: 5, relevance: 7 }),
      rationale: '二轮收敛',
      created_at: 1700000007000,
    },
  ]
  store.getState().hydrate(snapshot)

  const summary = store.getState().summary
  assert.ok(summary, '已结束对战必须能重建战报')
  assert.equal(summary.judgeAvailable, true)
  assert.equal(summary.contestants.length, 2)
  assert.equal(summary.verdicts.length, 1)
  assert.deepEqual(summary.verdicts[0]?.scores, { logic: 7, evidence: 6, relevance: 8 })
  assert.ok(summary.verdicts[0]?.rationale.includes('立论稳'))
  assert.ok(summary.verdicts[0]?.rationale.includes('二轮收敛'))
})

test('已结束的硬战场快照用沙箱结果重建通过率', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  const snapshot = makeSnapshot()
  snapshot.battle.phase = 'completed'
  snapshot.battle.status = 'completed'
  snapshot.battle.arena = 'hard-code'
  snapshot.contestants[0].status = 'ok'
  snapshot.contestants[0].code = 'print(1)'
  snapshot.contestants[1].status = 'ok'
  snapshot.sandboxResults = [
    {
      id: 's1',
      contestant_id: 'c1',
      backend: 'pyodide',
      isolation_level: 'wasm-worker',
      language: 'python',
      status: 'ok',
      passed: 3,
      total: 3,
      time_ms: 412,
      memory_kb: null,
      compile_output: null,
      stdout_tail: '',
      degraded: 0,
    },
  ]
  store.getState().hydrate(snapshot)

  const summary = store.getState().summary
  assert.ok(summary)
  assert.equal(summary.arena, 'hard-code')
  const verdict = summary.verdicts.find((v) => v.contestantSlot === 1)
  assert.equal(verdict?.passed, 3)
  assert.equal(verdict?.total, 3)
})

test('运行中的快照不重建战报', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  store.getState().hydrate(makeSnapshot())
  assert.equal(store.getState().summary, null)
})

test('多轮对战：按回合切分 turns，非终回合完成不结束选手', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [
    { type: 'round.changed', round: 1, totalRounds: 2 },
    { type: 'contestant.started', slot: 1, providerId: 'ollama', modelId: 'm', round: 1 },
    delta(1, '甲'),
    { type: 'contestant.done', slot: 1, usage: null, latencyMs: 100, round: 1 },
  ])
  scheduler.runFrame()

  let view = store.getState().contestants[1]!
  assert.equal(view.turns.length, 1)
  assert.equal(fullText(view), '甲')
  assert.equal(view.status, 'streaming')
  assert.equal(store.getState().currentRound, 1)
  assert.equal(store.getState().totalRounds, 2)

  applyAll(store, [
    { type: 'round.changed', round: 2, totalRounds: 2 },
    { type: 'contestant.started', slot: 1, providerId: 'ollama', modelId: 'm', round: 2 },
    delta(1, '乙'),
    { type: 'contestant.done', slot: 1, usage: null, latencyMs: 250, round: 2 },
  ])
  scheduler.runFrame()

  view = store.getState().contestants[1]!
  assert.equal(view.turns.length, 2)
  assert.deepEqual(
    view.turns.map((turn) => turn.text),
    ['甲', '乙'],
  )
  assert.equal(store.getState().currentRound, 2)
  assert.equal(view.status, 'ok')
  assert.equal(view.latencyMs, 250)
  assert.notEqual(view.finishedAt, null)
})

test('多轮对战：中途失败终结该选手，剩余回合不再发 started', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [
    { type: 'round.changed', round: 1, totalRounds: 3 },
    { type: 'contestant.started', slot: 1, providerId: 'ollama', modelId: 'm', round: 1 },
    delta(1, '半句'),
    { type: 'contestant.failed', slot: 1, status: 'timeout', message: '第 1 回合超时', round: 1 },
  ])
  scheduler.runFrame()

  const view = store.getState().contestants[1]!
  assert.equal(view.status, 'timeout')
  assert.equal(view.turns.length, 1)
  assert.equal(view.turns[0]!.status, 'timeout')
  assert.equal(view.turns[0]!.failureMessage, '第 1 回合超时')
  assert.equal(fullText(view), '半句')
})

test('prediction.tally 进入 store，并可从快照重建', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  applyAll(store, [{ type: 'prediction.tally', tally: { c1: 3, c2: 1 } }])
  scheduler.runFrame()
  assert.deepEqual(store.getState().predictionTally, { c1: 3, c2: 1 })

  const snapshot = makeSnapshot()
  snapshot.predictions = [
    {
      id: 'p1',
      battle_id: 'b1',
      contestant_id: 'c1',
      voter_key: 'v1',
      position_index: 1,
      created_at: 1700000005000,
    },
  ]
  store.getState().hydrate(snapshot)
  assert.deepEqual(store.getState().predictionTally, { c1: 1 })
})

test('快照重建：多轮 transcript 解析为 turns，回合数按赛场默认', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  const snapshot = makeSnapshot()
  snapshot.battle.arena = 'banter'
  snapshot.battle.phase = 'completed'
  snapshot.battle.status = 'completed'
  snapshot.contestants[0]!.output_text = JSON.stringify([
    { round: 1, text: '开局炸场' },
    { round: 2, text: '接招反击' },
  ])
  snapshot.contestants[1]!.output_text = null
  store.getState().hydrate(snapshot)

  const view = store.getState().contestants[1]!
  assert.equal(view.turns.length, 2)
  assert.equal(view.turns[0]!.text, '开局炸场')
  assert.equal(view.turns[1]!.round, 2)
  assert.equal(store.getState().totalRounds, 3)

  const summary = store.getState().summary
  assert.ok(summary)
  const rebuilt = summary.contestants.find((item) => item.slot === 1)
  assert.equal(rebuilt?.outputLength, '开局炸场接招反击'.length)
})

test('快照重建：残局用 judgements 的 solved 分重建命中战报', () => {
  const scheduler = makeScheduler()
  const store = createBattleStore(scheduler)
  const snapshot = makeSnapshot()
  snapshot.battle.arena = 'chess'
  snapshot.battle.phase = 'completed'
  snapshot.battle.status = 'completed'
  snapshot.contestants[0]!.output_text = 'Qe8#'
  snapshot.contestants[0]!.status = 'ok'
  snapshot.contestants[1]!.status = 'ok'
  snapshot.judgements = [
    {
      id: 'j1',
      battle_id: 'b1',
      contestant_id: 'c1',
      round: 0,
      judge_model: 'chess-rules',
      position_index: 1,
      scores_json: JSON.stringify({ solved: 1 }),
      rationale: '找到 1 步杀法（Qe8#）',
      created_at: 1700000006000,
    },
  ]
  store.getState().hydrate(snapshot)

  const summary = store.getState().summary
  assert.ok(summary)
  assert.equal(summary.judgeAvailable, true)
  assert.equal(summary.verdicts[0]?.passed, 1)
  assert.equal(summary.verdicts[0]?.total, 1)
  assert.equal(summary.verdicts[0]?.scores.solved, 1)
})
