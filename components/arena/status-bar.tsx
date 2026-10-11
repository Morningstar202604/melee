'use client'

import { useEffect, useState } from 'react'
import {
  battlePhaseLabels,
  contestantStatusLabels,
  type BattlePhase,
} from '@/lib/battle/events'
import type { ContestantView } from '@/lib/client/battle-store'

export interface StatusBarProps {
  phase: BattlePhase
  startedAt: number | null
  contestants: ContestantView[]
  failureReason: string | null
}

const LIVE_PHASES = new Set<BattlePhase>(['running', 'judging', 'scoring'])

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`
}

function useElapsedLabel(startedAt: number | null): string {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  if (startedAt === null) return '--:--'
  return formatElapsed(now - startedAt)
}

function segmentLabel(view: ContestantView): string {
  if (view.status === 'streaming') return `${view.slot} 号 ${view.providerId} 输出中`
  return `${view.slot} 号 ${view.providerId} ${contestantStatusLabels[view.status]}`
}

function ProgressSegment({ view }: { view: ContestantView }) {
  const streaming = view.status === 'streaming'
  const isOk = view.status === 'ok'
  const isFailed = view.status !== 'streaming' && view.status !== 'ok'

  let colorClasses = 'bg-zinc-300 dark:bg-zinc-600'
  if (streaming) colorClasses = 'bg-emerald-500 animate-glow-pulse'
  else if (isOk) colorClasses = 'bg-emerald-500'
  else if (isFailed) colorClasses = 'bg-red-500'

  return (
    <span
      title={segmentLabel(view)}
      aria-label={segmentLabel(view)}
      className={`relative flex flex-1 items-center justify-center overflow-hidden rounded text-xs font-black tabular-nums text-white min-w-0 h-[22px] ${colorClasses}`}
    >
      {streaming ? (
        <span className="animate-shimmer absolute inset-0 bg-white/20" />
      ) : null}
      <span className="relative">{view.slot}</span>
    </span>
  )
}

export function StatusBar({ phase, startedAt, contestants, failureReason }: StatusBarProps) {
  const elapsed = useElapsedLabel(startedAt)
  const total = contestants.length
  const finished = contestants.filter((view) => view.status !== 'streaming').length
  const streaming = contestants.filter((view) => view.status === 'streaming').length
  const live = LIVE_PHASES.has(phase)

  return (
    <div className="sticky top-0 z-30 border-b border-zinc-200 bg-white/95 backdrop-blur-md dark:border-zinc-700 dark:bg-zinc-900/95">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
        {/* Phase indicator */}
        <span className="inline-flex items-center gap-2">
          <span
            className={`inline-block h-2.5 w-2.5 rounded-full ${
              live ? 'bg-emerald-500 animate-glow-pulse' : 'bg-zinc-400'
            }`}
          />
          <span className="text-sm font-bold tracking-tight text-zinc-900 dark:text-zinc-50">
            {battlePhaseLabels[phase]}
          </span>
        </span>

        {/* Progress bar */}
        <span
          role="progressbar"
          aria-label={`已完成 ${finished} 名，共 ${total} 名选手`}
          aria-valuenow={finished}
          aria-valuemin={0}
          aria-valuemax={total}
          className="flex flex-1 gap-1 py-1"
        >
          {contestants.map((view) => (
            <ProgressSegment key={view.slot} view={view} />
          ))}
        </span>

        {/* Counter */}
        <span className="inline-flex items-baseline gap-1 tabular-nums">
          <span className="text-lg font-black leading-none text-orange-500">
            {finished}
          </span>
          <span className="text-xs text-zinc-400">/ {total}</span>
          {streaming > 0 ? (
            <span className="ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
              {streaming} 输出中
            </span>
          ) : null}
        </span>

        {/* Elapsed timer */}
        <span className="inline-flex items-baseline gap-1.5">
          <span className="text-[10px] font-medium tracking-wider text-zinc-400">用时</span>
          <span
            suppressHydrationWarning
            className="font-mono text-sm font-bold tabular-nums text-orange-500"
          >
            {elapsed}
          </span>
        </span>
      </div>

      {/* Failure banner */}
      {failureReason ? (
        <div className="bg-red-500 px-4 py-1.5 text-center text-xs font-bold text-white">
          {failureReason}
        </div>
      ) : null}
    </div>
  )
}
