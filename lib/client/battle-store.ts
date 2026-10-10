import { create, type StoreApi, type UseBoundStore } from 'zustand'
import { isTerminalEvent } from '../battle/registry.ts'
import type { BattleEvent, BattlePhase, BattleResult } from '../battle/events.ts'
import type { ContestantStatus } from '../arena/types.ts'
import type { Snapshot } from '../db/repo.ts'

export type ContestantStreamStatus = 'streaming'

export interface ContestantView {
  slot: number
  contestantId: string | null
  providerId: string
  modelId: string
  text: string
  status: ContestantStreamStatus | ContestantStatus
  failureMessage: string | null
  latencyMs: number | null
  finishedAt: number | null
  isFirstFinisher: boolean
}

export interface BattleData {
  phase: BattlePhase
  startedAt: number | null
  contestants: Record<number, ContestantView>
  voteTally: Record<string, number>
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

export function isTerminalPhase(phase: BattlePhase): boolean {
  return TERMINAL_PHASES.includes(phase)
}

const EMPTY_DATA: BattleData = Object.freeze({
  phase: 'pending',
  startedAt: null,
  contestants: Object.freeze({}),
  voteTally: Object.freeze({}),
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
    text: '',
    status: 'streaming',
    failureMessage: null,
    latencyMs: null,
    finishedAt: null,
    isFirstFinisher: false,
  }
}

function hasOtherFinisher(contestants: Record<number, ContestantView>, slot: number): boolean {
  return Object.values(contestants).some(
    (view) => view.slot !== slot && view.status !== 'streaming',
  )
}

export function reduceEvent(data: BattleData, event: BattleEvent): BattleData {
  switch (event.type) {
    case 'phase.changed':
      return { ...data, phase: event.phase }

    case 'contestant.started': {
      const existing = data.contestants[event.slot]
      return {
        ...data,
        startedAt: data.startedAt ?? Date.now(),
        contestants: {
          ...data.contestants,
          [event.slot]: blankContestant(event.slot, event.providerId, event.modelId, existing?.contestantId ?? null),
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
          [event.slot]: { ...current, text: current.text + event.text },
        },
      }
    }

    case 'contestant.done': {
      const current = data.contestants[event.slot]
      if (!current) return data
      const first = !hasOtherFinisher(data.contestants, event.slot)
      return {
        ...data,
        contestants: {
          ...data.contestants,
          [event.slot]: {
            ...current,
            status: 'ok',
            latencyMs: event.latencyMs,
            finishedAt: Date.now(),
            isFirstFinisher: current.isFirstFinisher || first,
          },
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
          [event.slot]: {
            ...current,
            status: event.status,
            failureMessage: event.message,
            finishedAt: Date.now(),
            isFirstFinisher: current.isFirstFinisher || first,
          },
        },
      }
    }

    case 'vote.tally':
      return { ...data, voteTally: { ...event.tally } }

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

export function dataFromSnapshot(snapshot: Snapshot): BattleData {
  const contestants: Record<number, ContestantView> = {}
  for (const row of snapshot.contestants) {
    const running = row.status === 'running'
    contestants[row.slot] = {
      slot: row.slot,
      contestantId: row.id,
      providerId: row.provider_id,
      modelId: row.model_id,
      text: row.output_text ?? '',
      status: running ? 'streaming' : (row.status as ContestantStatus),
      failureMessage: row.error_message,
      latencyMs: row.latency_ms,
      finishedAt: null,
      isFirstFinisher: false,
    }
  }
  markFirstFinisherFromSnapshot(contestants)

  const voteTally: Record<string, number> = {}
  for (const vote of snapshot.votes) {
    voteTally[vote.contestant_id] = (voteTally[vote.contestant_id] ?? 0) + 1
  }

  return {
    phase: snapshot.battle.phase,
    startedAt: snapshot.battle.created_at,
    contestants,
    voteTally,
    summary: null,
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
      contestants: state.contestants,
      voteTally: state.voteTally,
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
