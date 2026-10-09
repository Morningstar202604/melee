import type { BattleEvent, BattlePhase } from './events.ts'

export type BattleEventListener = (event: BattleEvent, id: number) => void

export interface BattleHandle {
  readonly id: string
  readonly signal: AbortSignal
  readonly cancelled: boolean
  readonly settled: boolean
  emit(event: BattleEvent): number
  eventsSince(id: number): BattleEvent[]
  subscribe(listener: BattleEventListener): () => void
  nextId(): number
  cancel(): void
}

interface EventRecord {
  id: number
  event: BattleEvent
}

const TERMINAL_PHASES: readonly BattlePhase[] = ['completed', 'cancelled', 'failed']

export function isTerminalEvent(event: BattleEvent): boolean {
  if (event.type === 'battle.completed' || event.type === 'battle.failed') return true
  if (event.type === 'phase.changed') return TERMINAL_PHASES.includes(event.phase)
  return false
}

export function closesStream(event: BattleEvent): boolean {
  if (event.type === 'battle.completed' || event.type === 'battle.failed') return true
  return event.type === 'phase.changed' && event.phase === 'cancelled'
}

export function createBattleHandle(id: string): BattleHandle {
  const log: EventRecord[] = []
  const listeners = new Set<BattleEventListener>()
  const controller = new AbortController()
  let cancelled = false
  let settled = false

  return {
    id,
    get signal(): AbortSignal {
      return controller.signal
    },
    get cancelled(): boolean {
      return cancelled
    },
    get settled(): boolean {
      return settled
    },
    emit(event: BattleEvent): number {
      const eventId = log.length
      log.push({ id: eventId, event })
      if (isTerminalEvent(event)) settled = true
      for (const listener of [...listeners]) listener(event, eventId)
      return eventId
    },
    eventsSince(id: number): BattleEvent[] {
      return log.filter((record) => record.id > id).map((record) => record.event)
    },
    subscribe(listener: BattleEventListener): () => void {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
    nextId(): number {
      return log.length
    },
    cancel(): void {
      if (cancelled) return
      cancelled = true
      controller.abort()
    },
  }
}

const battles = new Map<string, BattleHandle>()

export function registerBattle(id: string, handle: BattleHandle): void {
  battles.set(id, handle)
}

export function getBattleHandle(id: string): BattleHandle | undefined {
  return battles.get(id)
}

export function unregisterBattle(id: string): void {
  battles.delete(id)
}

export function listBattleIds(): string[] {
  return [...battles.keys()]
}
