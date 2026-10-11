'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useShallow } from 'zustand/react/shallow'
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
import { ContestantCard } from './contestant-card'
import { StatusBar } from './status-bar'
import { Settlement } from './settlement'
import { arenaPalette } from './theme'

const MAX_RECONNECT_FAILURES = 3
const RECONNECT_DELAY_MS = 2000

export interface BattleShellProps {
  battleId: string
  topic: string
  arena: ArenaId
  initialSnapshot: Snapshot
  providerLabels: Record<string, string>
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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
    let source: EventSource | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let failures = 0

    const teardown = () => {
      if (retryTimer !== null) {
        clearTimeout(retryTimer)
        retryTimer = null
      }
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
        failures += 1
        teardown()
        if (isTerminalPhase(currentPhase)) return
        if (failures >= MAX_RECONNECT_FAILURES) {
          setConnectionLost(true)
          void recoverFromSnapshot()
          return
        }
        retryTimer = setTimeout(connect, RECONNECT_DELAY_MS)
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
      setCancelNotice(`取消失败：${errorText(error)}`)
    } finally {
      setCancelling(false)
    }
  }

  const activeSnapshot = live ? snapshot : seed.snapshot

  return (
    <div className="min-h-full bg-[var(--background)] text-[var(--foreground)]">
      {/* ── Header ─────────────────────────────────────────── */}
      <header className="border-b border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-4 sm:px-6">
          <Link
            href="/"
            className="text-sm font-bold text-zinc-500 hover:text-orange-500 dark:text-zinc-400 dark:hover:text-orange-400"
          >
            ← 回首页
          </Link>
        </div>
      </header>

      {/* ── Status Bar ───────────────────────────────────── */}
      <StatusBar
        phase={shownPhase}
        startedAt={shownStartedAt}
        contestants={views}
        failureReason={shownFailureReason}
      />

      {/* ── Connection Lost Alert ─────────────────────────── */}
      {connectionLost ? (
        <div
          role="alert"
          className="mx-auto mt-6 max-w-5xl rounded-xl border-2 border-red-200 bg-red-50 px-4 py-3 text-center text-sm font-medium text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300"
        >
          连接已中断 — 请刷新页面恢复实时画面
        </div>
      ) : null}

      {/* ── Topic & Battle Info ─────────────────────────── */}
      <div className="mx-auto max-w-5xl px-4 pt-5 sm:px-6">
        <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
          <div className="flex flex-wrap items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
            <span className="font-mono text-xs text-zinc-400">{battleId}</span>
            <span
              className="rounded-full px-2.5 py-0.5 text-xs font-bold"
              style={{ backgroundColor: `${arenaPalette[arena]}18`, color: arenaPalette[arena] }}
            >
              {arena === 'soft' ? '🎭 软战场' : '🐍 硬战场'}
            </span>
            {cancelNotice ? <span className="text-xs">{cancelNotice}</span> : null}
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelling || settled}
              className="ml-auto rounded-lg border border-zinc-300 px-3 py-1.5 text-xs font-medium text-zinc-600 transition-colors hover:border-red-300 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-600 dark:text-zinc-400 dark:hover:border-red-700 dark:hover:text-red-400"
            >
              {cancelling ? '取消中…' : '取消对战'}
            </button>
          </div>
          <p className="mt-2 text-xs font-bold tracking-wider text-zinc-400">本场题目</p>
          <pre className="mt-1 whitespace-pre-wrap break-words border-l-4 pl-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300" style={{ borderColor: arenaPalette[arena] }}>
            {topic}
          </pre>
        </div>
      </div>

      {/* ── Contestant Cards ─────────────────────────────── */}
      <main className="mx-auto grid max-w-5xl gap-4 px-4 py-6 sm:grid-cols-2 sm:px-6">
        {views.map((view) => (
          <ContestantCard
            key={view.slot}
            view={view}
            providerLabel={providerLabels[view.providerId]}
            maxLatencyMs={maxLatencyMs}
          />
        ))}
      </main>

      {/* ── Settlement ────────────────────────────────────── */}
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
