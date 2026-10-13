import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { isTerminalEvent } from '../battle/registry.ts'
import type { BattleEvent, BattlePhase, BattleResult } from '../battle/events.ts'
import { parseTurns, type ArenaId, type ContestantStatus, type SandboxProgress } from '../arena/types.ts'
import type { Snapshot } from '../db/repo.ts'

export type ContestantStreamStatus = 'streaming'

export interface ContestantTurn {
  round: number
  text: string
  status: ContestantStreamStatus | ContestantStatus
  failureMessage: string | null
  latencyMs: number | null
}

export interface ContestantView {
  slot: number
  contestantId: string | null
  providerId: string
  modelId: string
  turns: ContestantTurn[]
  status: ContestantStreamStatus | ContestantStatus
  failureMessage: string | null
  latencyMs: number | null
  finishedAt: number | null
  isFirstFinisher: boolean
  sandbox: SandboxProgress | null
}

export interface BattleData {
  phase: BattlePhase
  startedAt: number | null
  currentRound: number
  totalRounds: number
  contestants: Record<number, ContestantView>
  voteTally: Record<string, number>
  predictionTally: Record<string, number>
  summary: BattleResult | null
  failureReason: string | null
  hydratedBattleId: string | null
  snapshot: Snapshot | null
}

export interface BattleStoreState extends BattleData {
  apply: (event: BattleEvent) => void
  hydrate: (snapshot: Snapshot) => void
}

export interface FrameScheduler {
  schedule: (flush: () => void) => { cancel: () => void }
}

export type BattleStore = UseBoundStore<StoreApi<BattleStoreState>>

const TERMINAL_PHASES: readonly BattlePhase[] = ['completed', 'cancelled', 'failed']

const MULTI_ROUND_ARENAS: readonly ArenaId[] = ['debate', 'banter']

export function isTerminalPhase(phase: BattlePhase): boolean {
  return TERMINAL_PHASES.includes(phase)
}

export function isMultiRoundArena(arena: string): boolean {
  return (MULTI_ROUND_ARENAS as readonly string[]).includes(arena)
}

export function fullText(view: ContestantView): string {
  return view.turns.map((turn) => turn.text).join('')
}

const EMPTY_DATA: BattleData = Object.freeze({
  phase: 'pending',
  startedAt: null,
  currentRound: 1,
  totalRounds: 1,
  contestants: Object.freeze({}),
  voteTally: Object.freeze({}),
  predictionTally: Object.freeze({}),
  summary: null,
  failureReason: null,
  hydratedBattleId: null,
  snapshot: null,
})

const rafScheduler: FrameScheduler = {
  schedule: (flush) => {
    const handle = requestAnimationFrame(flush)
    return {
      cancel: () => {
        cancelAnimationFrame(handle)
      },
    }
  },
}

function blankContestant(
  slot: number,
  providerId: string,
  modelId: string,
  contestantId: string | null,
): ContestantView {
  return {
    slot,
    contestantId,
    providerId,
    modelId,
    turns: [],
    status: 'streaming',
    failureMessage: null,
    latencyMs: null,
    finishedAt: null,
    isFirstFinisher: false,
    sandbox: null,
  }
}

function hasOtherFinisher(contestants: Record<number, ContestantView>, slot: number): boolean {
  return Object.values(contestants).some(
    (view) => view.slot !== slot && view.status !== 'streaming',
  )
}

function startTurn(view: ContestantView, round: number): ContestantView {
  const turns = view.turns.filter((turn) => turn.round !== round)
  turns.push({ round, text: '', status: 'streaming', failureMessage: null, latencyMs: null })
  turns.sort((a, b) => a.round - b.round)
  return { ...view, turns, status: 'streaming', failureMessage: null }
}

function appendDelta(view: ContestantView, text: string): ContestantView {
  const last = view.turns[view.turns.length - 1]
  if (!last) return view
  const turns = [
    ...view.turns.slice(0, -1),
    { ...last, text: last.text + text },
  ]
  return { ...view, turns }
}

function closeTurn(
  view: ContestantView,
  patch: Partial<ContestantTurn>,
  viewPatch: Partial<ContestantView>,
): ContestantView {
  const last = view.turns[view.turns.length - 1]
  const turns = last
    ? [...view.turns.slice(0, -1), { ...last, ...patch }]
    : view.turns
  return { ...view, turns, ...viewPatch }
}

export function reduceEvent(data: BattleData, event: BattleEvent): BattleData {
  switch (event.type) {
    case 'phase.changed':
      return { ...data, phase: event.phase }

    case 'round.changed':
      return { ...data, currentRound: event.round, totalRounds: event.totalRounds }

    case 'contestant.started': {
      const existing = data.contestants[event.slot] ?? blankContestant(
        event.slot,
        event.providerId,
        event.modelId,
        null,
      )
      const base: ContestantView = {
        ...existing,
        providerId: event.providerId,
        modelId: event.modelId,
      }
      return {
        ...data,
        startedAt: data.startedAt ?? Date.now(),
        contestants: {
          ...data.contestants,
          [event.slot]: startTurn(base, event.round ?? 1),
        },
      }
    }

    case 'contestant.delta': {
      const current = data.contestants[event.slot]
      if (!current) return data
      return {
        ...data,
        contestants: {
          ...data.contestants,
          [event.slot]: appendDelta(current, event.text),
        },
      }
    }

    case 'contestant.done': {
      const current = data.contestants[event.slot]
      if (!current) return data
      const round = event.round ?? 1
      const isFinalTurn = round >= data.totalRounds
      if (!isFinalTurn) {
        return {
          ...data,
          contestants: {
            ...data.contestants,
            [event.slot]: closeTurn(
              current,
              { status: 'ok', latencyMs: event.latencyMs },
              {},
            ),
          },
        }
      }
      const first = !hasOtherFinisher(data.contestants, event.slot)
      return {
        ...data,
        contestants: {
          ...data.contestants,
          [event.slot]: closeTurn(
            current,
            { status: 'ok', latencyMs: event.latencyMs },
            {
              status: 'ok',
              latencyMs: event.latencyMs,
              finishedAt: Date.now(),
              isFirstFinisher: current.isFirstFinisher || first,
            },
          ),
        },
      }
    }

    case 'contestant.failed': {
      const current = data.contestants[event.slot]
      if (!current) return data
      const first = !hasOtherFinisher(data.contestants, event.slot)
      return {
        ...data,
        contestants: {
          ...data.contestants,
          [event.slot]: closeTurn(
            current,
            { status: event.status, failureMessage: event.message },
            {
              status: event.status,
              failureMessage: event.message,
              finishedAt: Date.now(),
              isFirstFinisher: current.isFirstFinisher || first,
            },
          ),
        },
      }
    }

    case 'sandbox.result': {
      const current = data.contestants[event.slot]
      if (!current) return data
      return {
        ...data,
        contestants: {
          ...data.contestants,
          [event.slot]: {
            ...current,
            sandbox: {
              slot: event.slot,
              status: event.status,
              passed: event.passed,
              total: event.total,
              timeMs: event.timeMs,
              memoryKb: event.memoryKb,
            },
          },
        },
      }
    }

    case 'vote.tally':
      return { ...data, voteTally: { ...event.tally } }

    case 'prediction.tally':
      return { ...data, predictionTally: { ...event.tally } }

    case 'battle.completed':
      return { ...data, phase: 'completed', summary: event.result }

    case 'battle.failed':
      return { ...data, phase: 'failed', failureReason: event.reason }

    default:
      return data
  }
}

export function sortedContestants(
  contestants: Record<number, ContestantView>,
): ContestantView[] {
  return Object.values(contestants).sort((a, b) => a.slot - b.slot)
}

function markFirstFinisherFromSnapshot(contestants: Record<number, ContestantView>): void {
  const finished = Object.values(contestants).filter(
    (view) => view.status === 'ok' && view.latencyMs !== null,
  )
  if (finished.length === 0) return
  const fastest = finished.reduce((best, view) =>
    (view.latencyMs ?? 0) < (best.latencyMs ?? 0) ? view : best,
  )
  contestants[fastest.slot] = { ...fastest, isFirstFinisher: true }
}

function parseScores(json: string): Record<string, number> {
  try {
    const parsed = JSON.parse(json) as Record<string, unknown>
    const scores: Record<string, number> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'number' && Number.isFinite(value)) scores[key] = value
    }
    return scores
  } catch {
    return {}
  }
}

function averageScores(rows: Array<{ scores_json: string }>): Record<string, number> {
  const totals = new Map<string, { sum: number; count: number }>()
  for (const row of rows) {
    for (const [key, value] of Object.entries(parseScores(row.scores_json))) {
      const entry = totals.get(key) ?? { sum: 0, count: 0 }
      entry.sum += value
      entry.count += 1
      totals.set(key, entry)
    }
  }
  const scores: Record<string, number> = {}
  for (const [key, entry] of totals) {
    scores[key] = Math.round((entry.sum / entry.count) * 10) / 10
  }
  return scores
}

/** 从快照重建赛果，让结束后的对战重开页面也能看到战报。 */
export function summaryFromSnapshot(snapshot: Snapshot): BattleResult | null {
  if (snapshot.battle.phase !== 'completed') return null

  const multiRound = isMultiRoundArena(snapshot.battle.arena)
  const contestants = snapshot.contestants.map((row) => {
    const turns = parseTurns(row.output_text, multiRound)
    const outputLength = turns.reduce((sum, turn) => sum + turn.text.length, 0)
    return {
      slot: row.slot,
      providerId: row.provider_id,
      modelId: row.model_id,
      status: (row.status === 'running' ? 'provider_error' : row.status) as ContestantStatus,
      usage:
        row.prompt_tokens === null && row.completion_tokens === null
          ? null
          : { prompt: row.prompt_tokens ?? 0, completion: row.completion_tokens ?? 0 },
      latencyMs: row.latency_ms ?? 0,
      outputLength,
      code: row.code,
      errorMessage: row.error_message,
    }
  })

  const contestantById = new Map(snapshot.contestants.map((row) => [row.id, row]))
  const judgementsById = new Map<string, Array<{ scores_json: string; rationale: string }>>()
  for (const row of snapshot.judgements) {
    const list = judgementsById.get(row.contestant_id) ?? []
    list.push({ scores_json: row.scores_json, rationale: row.rationale })
    judgementsById.set(row.contestant_id, list)
  }

  const verdicts: BattleResult['verdicts'] = []
  for (const [contestantId, rows] of judgementsById) {
    const contestant = contestantById.get(contestantId)
    if (!contestant) continue
    const scores = averageScores(rows)
    const rationale = rows
      .map((row) => row.rationale)
      .filter((text) => text.length > 0)
      .join(' ｜ ')
    const solved = snapshot.battle.arena === 'chess' ? (scores.solved ?? 0) >= 0.5 ? 1 : 0 : 0
    verdicts.push({
      contestantSlot: contestant.slot,
      scores,
      passed: solved,
      total: snapshot.battle.arena === 'chess' ? 1 : 0,
      rationale: rationale || '未提供理由',
    })
  }

  if (snapshot.battle.arena === 'hard-code') {
    for (const result of snapshot.sandboxResults) {
      const contestant = contestantById.get(result.contestant_id)
      if (!contestant || verdicts.some((v) => v.contestantSlot === contestant.slot)) continue
      verdicts.push({
        contestantSlot: contestant.slot,
        scores: {
          pass_rate: result.total > 0 ? Math.round((result.passed / result.total) * 100) / 100 : 0,
        },
        passed: result.passed,
        total: result.total,
        rationale:
          result.total > 0 ? `通过 ${result.passed}/${result.total} 个测试用例` : '本题无测试用例',
      })
    }
  }
  verdicts.sort((a, b) => a.contestantSlot - b.contestantSlot)

  return {
    battleId: snapshot.battle.id,
    arena: snapshot.battle.arena as ArenaId,
    topic: snapshot.battle.topic,
    contestants,
    verdicts,
    judgeAvailable: snapshot.judgements.length > 0,
  }
}

export function dataFromSnapshot(snapshot: Snapshot): BattleData {
  const multiRound = isMultiRoundArena(snapshot.battle.arena)

  const sandboxByContestant = new Map<
    string,
    { status: string; passed: number; total: number; timeMs: number | null; memoryKb: number | null }
  >()
  for (const row of snapshot.sandboxResults) {
    sandboxByContestant.set(row.contestant_id, {
      status: row.status,
      passed: row.passed,
      total: row.total,
      timeMs: row.time_ms,
      memoryKb: row.memory_kb,
    })
  }

  const contestants: Record<number, ContestantView> = {}
  for (const row of snapshot.contestants) {
    const running = row.status === 'running'
    const sandbox = sandboxByContestant.get(row.id)
    const parsed = parseTurns(row.output_text, multiRound)
    const turns: ContestantTurn[] = parsed.map((turn, index) => ({
      round: turn.round,
      text: turn.text,
      status: running
        ? 'streaming'
        : index === parsed.length - 1
          ? (row.status as ContestantStatus)
          : 'ok',
      failureMessage: index === parsed.length - 1 ? row.error_message : null,
      latencyMs: index === parsed.length - 1 ? row.latency_ms : null,
    }))
    contestants[row.slot] = {
      slot: row.slot,
      contestantId: row.id,
      providerId: row.provider_id,
      modelId: row.model_id,
      turns,
      status: running ? 'streaming' : (row.status as ContestantStatus),
      failureMessage: row.error_message,
      latencyMs: row.latency_ms,
      finishedAt: null,
      isFirstFinisher: false,
      sandbox: sandbox
        ? {
            slot: row.slot,
            status: sandbox.status as SandboxProgress['status'],
            passed: sandbox.passed,
            total: sandbox.total,
            timeMs: sandbox.timeMs,
            memoryKb: sandbox.memoryKb,
          }
        : null,
    }
  }
  markFirstFinisherFromSnapshot(contestants)

  const voteTally: Record<string, number> = {}
  for (const vote of snapshot.votes) {
    voteTally[vote.contestant_id] = (voteTally[vote.contestant_id] ?? 0) + 1
  }
  const predictionTally: Record<string, number> = {}
  for (const prediction of snapshot.predictions) {
    predictionTally[prediction.contestant_id] = (predictionTally[prediction.contestant_id] ?? 0) + 1
  }

  const baseTurns = multiRound ? 3 : 1
  const terminal =
    snapshot.battle.phase === 'completed' ||
    snapshot.battle.phase === 'failed' ||
    snapshot.battle.phase === 'cancelled'

  return {
    phase: snapshot.battle.phase,
    startedAt: snapshot.battle.created_at,
    currentRound: terminal ? baseTurns : 1,
    totalRounds: baseTurns,
    contestants,
    voteTally,
    predictionTally,
    summary: summaryFromSnapshot(snapshot),
    failureReason: snapshot.battle.error,
    hydratedBattleId: null,
    snapshot,
  }
}

export function createBattleStore(scheduler: FrameScheduler = rafScheduler): BattleStore {
  const store = create<BattleStoreState>()(() => ({
    ...EMPTY_DATA,
    apply: (event) => applyEvent(event),
    hydrate: (snapshot) => hydrateSnapshot(snapshot),
  }))

  let draft: BattleData | null = null
  let frame: { cancel: () => void } | null = null

  function readData(): BattleData {
    const state = store.getState()
    return {
      phase: state.phase,
      startedAt: state.startedAt,
      currentRound: state.currentRound,
      totalRounds: state.totalRounds,
      contestants: state.contestants,
      voteTally: state.voteTally,
      predictionTally: state.predictionTally,
      summary: state.summary,
      failureReason: state.failureReason,
      hydratedBattleId: state.hydratedBattleId,
      snapshot: state.snapshot,
    }
  }

  function commit(next: BattleData): void {
    store.setState(next)
  }

  function cancelFrame(): void {
    if (!frame) return
    const pending = frame
    frame = null
    pending.cancel()
  }

  function flushFrame(): void {
    frame = null
    const next = draft
    draft = null
    if (next) commit(next)
  }

  function enqueue(event: BattleEvent): void {
    draft = reduceEvent(draft ?? readData(), event)
    if (frame) return
    frame = scheduler.schedule(flushFrame)
  }

  function writeNow(event: BattleEvent): void {
    cancelFrame()
    const next = reduceEvent(draft ?? readData(), event)
    draft = null
    commit(next)
  }

  function applyEvent(event: BattleEvent): void {
    if (isTerminalEvent(event)) {
      writeNow(event)
      return
    }
    enqueue(event)
  }

  function hydrateSnapshot(snapshot: Snapshot): void {
    cancelFrame()
    draft = null
    commit({ ...dataFromSnapshot(snapshot), hydratedBattleId: snapshot.battle.id })
  }

  return store
}

export const useBattleStore: BattleStore = createBattleStore()
