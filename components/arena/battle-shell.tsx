'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useShallow } from 'zustand/react/shallow'
import { ChevronLeft, Radio, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import { closesStream } from '@/lib/battle/registry'
import { unwrapBattleChunk } from '@/lib/battle/ui-stream'
import type { ArenaId } from '@/lib/arena/types'
import { puzzleFromTopic } from '@/lib/arena/chess'
import type { Snapshot } from '@/lib/db/repo'
import {
  dataFromSnapshot,
  isTerminalPhase,
  sortedContestants,
  useBattleStore,
} from '@/lib/client/battle-store'
import { ARENA_SKINS } from './theme'
import { ChessBoard } from './chess-board'
import { ContestantCard } from './contestant-card'
import { PredictionBar } from './prediction-bar'
import { RoundTimeline } from './round-timeline'
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
  const {
    apply,
    hydrate,
    hydratedBattleId,
    phase,
    startedAt,
    currentRound,
    totalRounds,
    contestants,
    voteTally,
    predictionTally,
    summary,
    snapshot,
    failureReason,
  } = useBattleStore(
    useShallow((state) => ({
      apply: state.apply,
      hydrate: state.hydrate,
      hydratedBattleId: state.hydratedBattleId,
      phase: state.phase,
      startedAt: state.startedAt,
      currentRound: state.currentRound,
      totalRounds: state.totalRounds,
      contestants: state.contestants,
      voteTally: state.voteTally,
      predictionTally: state.predictionTally,
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
  const shownRound = live ? currentRound : seed.currentRound
  const shownTotalRounds = live ? totalRounds : seed.totalRounds
  const settled = isTerminalPhase(shownPhase)
  const maxLatencyMs = views.reduce((max, view) => Math.max(max, view.latencyMs ?? 0), 0)
  const skin = ARENA_SKINS[arena]
  const multiRound = skin.multiRound
  const puzzle = useMemo(() => (arena === 'chess' ? puzzleFromTopic(topic) : null), [arena, topic])

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
        let raw: unknown
        try {
          raw = JSON.parse(message.data)
        } catch {
          return
        }
        const event = unwrapBattleChunk(raw)
        if (!event) return
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
  const verdictBySlot = useMemo(() => {
    if (!summary) return new Map<number, { passed: number; total: number; rationale: string }>()
    return new Map(
      summary.verdicts.map((verdict) => [
        verdict.contestantSlot,
        { passed: verdict.passed, total: verdict.total, rationale: verdict.rationale },
      ]),
    )
  }, [summary])

  const chessLines = arena === 'chess' ? topic.split('\n') : []
  const chessTitle = chessLines[0] ?? ''
  const chessBrief = chessLines[1] ?? ''

  return (
    <div className="flex min-h-full flex-col">
      <StatusBar
        phase={shownPhase}
        arena={arena}
        startedAt={shownStartedAt}
        contestants={views}
        failureReason={shownFailureReason}
        currentRound={shownRound}
        totalRounds={shownTotalRounds}
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
          <span className={cn('rounded-full border px-2.5 py-0.5 text-xs font-semibold', skin.accentChip)}>
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

      {arena === 'chess' ? (
        <div className="mx-auto w-full max-w-6xl px-5 pt-5">
          <section className="flex flex-col gap-5 rounded-xl border border-hairline bg-panel p-4 sm:flex-row sm:p-5">
            {puzzle ? (
              <div className="w-full max-w-96 shrink-0 sm:max-w-80">
                <ChessBoard fen={puzzle.fen} />
              </div>
            ) : null}
            <div className="flex min-w-0 flex-col gap-2">
              <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
                本场残局
              </span>
              <h2 className="text-base font-bold">{chessTitle.replace(/^国际象棋残局/, '')}</h2>
              <p className="text-[13.5px] leading-relaxed text-ash">{chessBrief}</p>
              <p className="mt-auto font-mono text-[11px] text-dust">
                白先 · 大写=白方 小写=黑方 · 着法真校验
              </p>
            </div>
          </section>
        </div>
      ) : (
        <div className="mx-auto w-full max-w-6xl px-5 pt-5">
          <section className="rounded-xl border border-hairline bg-panel p-4 sm:p-5">
            <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
              {multiRound ? '本场辩题' : '本场题面'}
            </span>
            <pre
              className={cn(
                'mt-2 whitespace-pre-wrap break-words border-l-2 pl-3.5 font-sans text-sm leading-[1.75] text-ink/90',
                arena === 'soft' && 'border-soft-arena/50',
                arena === 'debate' && 'border-arena-debate/50',
                arena === 'banter' && 'border-arena-banter/50',
                arena === 'hard-code' && 'border-hard-arena/50',
              )}
            >
              {topic}
            </pre>
          </section>
        </div>
      )}

      {multiRound ? (
        <main className="mx-auto w-full max-w-6xl px-5 py-5">
          <RoundTimeline
            arena={arena}
            views={views}
            currentRound={shownRound}
            totalRounds={shownTotalRounds}
            providerLabels={providerLabels}
          />
        </main>
      ) : (
        <main className="mx-auto grid w-full max-w-6xl gap-4 px-5 py-5 sm:grid-cols-2">
          {views.map((view) => (
            <ContestantCard
              key={view.slot}
              view={view}
              arena={arena}
              providerLabel={providerLabels[view.providerId]}
              maxLatencyMs={maxLatencyMs}
              puzzle={arena === 'chess' ? puzzle : null}
              verdict={arena === 'chess' ? verdictBySlot.get(view.slot) ?? null : null}
            />
          ))}
        </main>
      )}

      <div className="mx-auto w-full max-w-6xl px-5 pb-5">
        <PredictionBar
          battleId={battleId}
          arena={arena}
          phase={shownPhase}
          views={views}
          tally={live ? predictionTally : seed.predictionTally}
          voteTally={live ? voteTally : seed.voteTally}
          summary={summary ?? seed.summary}
          snapshot={activeSnapshot}
          providerLabels={providerLabels}
        />
      </div>

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
