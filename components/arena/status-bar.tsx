'use client'

import { useEffect, useState } from 'react'
import { cn } from '@/lib/cn'
import {
  battlePhaseLabels,
  contestantStatusLabels,
  type BattlePhase,
} from '@/lib/battle/events'
import type { ArenaId } from '@/lib/arena/types'
import type { ContestantView } from '@/lib/client/battle-store'
import { TONE_CLASSES, statusTone } from './theme'

export interface StatusBarProps {
  phase: BattlePhase
  arena: ArenaId
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
  const tone = statusTone(view.status)
  return (
    <span
      title={segmentLabel(view)}
      aria-label={segmentLabel(view)}
      className={cn(
        'flex h-5 min-w-0 flex-1 items-center justify-center rounded font-mono text-[10px] font-bold tabular-nums text-void',
        TONE_CLASSES[tone].bar,
        view.status === 'streaming' && 'animate-live',
        tone === 'failure' && 'text-ink',
      )}
    >
      {view.slot}
    </span>
  )
}

export function StatusBar({ phase, arena, startedAt, contestants, failureReason }: StatusBarProps) {
  const elapsed = useElapsedLabel(startedAt)
  const total = contestants.length
  const finished = contestants.filter((view) => view.status !== 'streaming').length
  const live = LIVE_PHASES.has(phase)
  const phaseLabel =
    arena === 'hard-code' && phase === 'judging' ? '沙箱判定中' : battlePhaseLabels[phase]

  return (
    <div className="sticky top-0 z-30 border-b border-hairline bg-void/90 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 px-5 py-2.5">
        <span className="inline-flex items-center gap-2">
          <span
            aria-hidden
            className={cn('h-2 w-2 rounded-full', live ? 'bg-ok animate-live' : 'bg-dust')}
          />
          <span className="text-[13px] font-bold tracking-tight">{phaseLabel}</span>
        </span>

        <span
          role="progressbar"
          aria-label={`已完成 ${finished} 名，共 ${total} 名选手`}
          aria-valuenow={finished}
          aria-valuemin={0}
          aria-valuemax={total}
          className="flex min-w-32 flex-1 gap-1"
        >
          {contestants.map((view) => (
            <ProgressSegment key={view.slot} view={view} />
          ))}
        </span>

        <span className="inline-flex items-baseline gap-1 font-mono text-sm font-bold tabular-nums">
          <span className="text-blaze">{finished}</span>
          <span className="text-xs font-medium text-dust">/ {total}</span>
        </span>

        <span className="inline-flex items-baseline gap-1.5">
          <span className="font-mono text-[10px] uppercase tracking-wider text-dust">用时</span>
          <span
            suppressHydrationWarning
            className="font-mono text-sm font-bold tabular-nums text-ink"
          >
            {elapsed}
          </span>
        </span>
      </div>

      {failureReason ? (
        <div className="border-t border-bad/30 bg-bad/15 px-5 py-1.5 text-center text-xs font-semibold text-bad">
          {failureReason}
        </div>
      ) : null}
    </div>
  )
}
