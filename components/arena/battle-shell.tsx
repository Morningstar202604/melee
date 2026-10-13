'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useShallow } from 'zustand/react/shallow'
import { ChevronLeft, Radio, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { closesStream } from '@/lib/battle/registry'
import { parseBattleEvent } from '@/lib/battle/events'
import type { ArenaId } from '@/lib/arena/types'
import type { Snapshot } from '@/lib/db/repo'
import {
  dataFromSnapshot,
  isTerminalPhase,
  sortedContestants,
  useBattleStore,
} from '@/lib/client/battle-store'
import { ARENA_SKINS } from './theme'
import { ContestantCard } from './contestant-card'
import { StatusBar } from './status-bar'
import { Settlement } from './settlement'

const MAX_RECONNECT_FAILURES = 3

export interface BattleShellProps {
  battleId: string
  topic: string
  arena: ArenaId
  initialSnapshot: Snapshot
  providerLabels: Record<string, string>
}

export function BattleShell({
  battleId,
  topic,
  arena,
  initialSnapshot,
  providerLabels,
}: BattleShellProps) {
  const { apply, hydrate, hydratedBattleId, phase, startedAt, contestants, summary, snapshot, failureReason } =
    useBattleStore(
      useShallow((state) => ({
        apply: state.apply,
        hydrate: state.hydrate,
        hydratedBattleId: state.hydratedBattleId,
        phase: state.phase,
        startedAt: state.startedAt,
        contestants: state.contestants,
        summary: state.summary,
        snapshot: state.snapshot,
        failureReason: state.failureReason,
      })),
    )

  const seed = useMemo(() => dataFromSnapshot(initialSnapshot), [initialSnapshot])
  const live = hydratedBattleId === battleId

  const [connectionLost, setConnectionLost] = useState(false)
  const [cancelNotice, setCancelNotice] = useState('')
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    hydrate(initialSnapshot)
  }, [hydrate, initialSnapshot])

  const views = useMemo(
    () => (live ? sortedContestants(contestants) : sortedContestants(seed.contestants)),
    [live, contestants, seed],
  )
  const shownPhase = live ? phase : seed.phase
  const shownStartedAt = live ? startedAt : seed.startedAt
  const shownFailureReason = live ? failureReason : seed.failureReason
  const settled = isTerminalPhase(shownPhase)
  const maxLatencyMs = views.reduce((max, view) => Math.max(max, view.latencyMs ?? 0), 0)

  useEffect(() => {
    if (isTerminalPhase(seed.phase)) return

    let disposed = false
    let failures = 0
    let source: EventSource | null = null

    const teardown = () => {
      if (source) {
        source.close()
        source = null
      }
    }

    const recoverFromSnapshot = async () => {
      try {
        const response = await fetch(`/api/battle/${battleId}/snapshot`)
        if (!response.ok) return
        const fresh = (await response.json()) as Snapshot
        if (!isTerminalPhase(fresh.battle.phase)) return
        hydrate(fresh)
      } catch {
        setConnectionLost(true)
      }
    }

    const connect = () => {
      if (disposed) return
      source = new EventSource(`/api/battle/${battleId}/stream`)
      source.onopen = () => {
        failures = 0
      }
      source.onmessage = (message) => {
        let event
        try {
          event = parseBattleEvent(JSON.parse(message.data))
        } catch {
          return
        }
        apply(event)
        if (closesStream(event)) teardown()
      }
      source.onerror = () => {
        if (disposed) return
        const current = useBattleStore.getState()
        const currentPhase =
          current.hydratedBattleId === battleId ? current.phase : seed.phase
        if (isTerminalPhase(currentPhase)) {
          teardown()
          return
        }
        failures += 1
        if (failures >= MAX_RECONNECT_FAILURES) {
          teardown()
          setConnectionLost(true)
          void recoverFromSnapshot()
        }
      }
    }

    connect()
    return () => {
      disposed = true
      teardown()
    }
  }, [apply, battleId, hydrate, seed.phase])

  const handleCancel = async () => {
    if (cancelling) return
    setCancelling(true)
    setCancelNotice('')
    try {
      const response = await fetch(`/api/battle/${battleId}/cancel`, { method: 'POST' })
      if (response.ok) {
        setCancelNotice('已通知取消，本场即将收尾')
        return
      }
      const payload = (await response.json().catch(() => ({}))) as { error?: string }
      setCancelNotice(payload.error ?? `取消失败：HTTP ${response.status}`)
    } catch (error) {
      setCancelNotice(`取消失败：${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setCancelling(false)
    }
  }

  const activeSnapshot = live ? snapshot : seed.snapshot
  const skin = ARENA_SKINS[arena]

  return (
    <div className="flex min-h-full flex-col">
      <StatusBar
        phase={shownPhase}
        arena={arena}
        startedAt={shownStartedAt}
        contestants={views}
        failureReason={shownFailureReason}
      />

      <header className="border-b border-hairline">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-sm font-semibold text-ash transition-colors hover:text-ink"
          >
            <ChevronLeft className="h-4 w-4" />
            回擂台
          </Link>
          <span
            className={cn(
              'rounded-full border px-2.5 py-0.5 text-xs font-semibold',
              skin.accentChip,
            )}
          >
            {skin.label}
          </span>
          <span className="font-mono text-[11px] text-dust">{battleId}</span>
          {cancelNotice ? <span className="text-xs text-warn">{cancelNotice}</span> : null}
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelling || settled}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-hairline px-3 py-1.5 text-xs font-medium text-ash transition-colors hover:border-bad/50 hover:text-bad disabled:cursor-not-allowed disabled:opacity-40"
          >
            <X className="h-3.5 w-3.5" />
            {cancelling ? '取消中…' : '取消对战'}
          </button>
        </div>
      </header>

      {connectionLost ? (
        <div
          role="alert"
          className="border-b border-bad/30 bg-bad/10 px-5 py-2.5 text-center text-[13px] font-medium text-bad"
        >
          <Radio className="mr-1.5 inline h-3.5 w-3.5" />
          实时连接已中断 — 刷新页面可恢复画面
        </div>
      ) : null}

      <div className="mx-auto w-full max-w-6xl px-5 pt-5">
        <section className="rounded-xl border border-hairline bg-panel p-4 sm:p-5">
          <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
            本场题面
          </span>
          <pre
            className={cn(
              'mt-2 whitespace-pre-wrap break-words border-l-2 pl-3.5 font-sans text-sm leading-[1.75] text-ink/90',
              arena === 'soft' ? 'border-soft-arena/50' : 'border-hard-arena/50',
            )}
          >
            {topic}
          </pre>
        </section>
      </div>

      <main className="mx-auto grid w-full max-w-6xl gap-4 px-5 py-5 sm:grid-cols-2">
        {views.map((view) => (
          <ContestantCard
            key={view.slot}
            view={view}
            arena={arena}
            providerLabel={providerLabels[view.providerId]}
            maxLatencyMs={maxLatencyMs}
          />
        ))}
      </main>

      {summary && activeSnapshot ? (
        <Settlement
          arena={arena}
          topic={topic}
          result={summary}
          snapshot={activeSnapshot}
          providerLabels={providerLabels}
        />
      ) : null}
    </div>
  )
}
