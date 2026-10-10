'use client'

import { useEffect, useState } from 'react'
import {
  battlePhaseLabels,
  contestantStatusLabels,
  type BattlePhase,
} from '@/lib/battle/events'
import type { ContestantView } from '@/lib/client/battle-store'
import {
  fontWeight,
  layout,
  radius,
  spacing,
  statusTone,
  tonePair,
  typeScale,
  v,
} from './theme'

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
  const tone = tonePair[statusTone(view.status)]
  const streaming = view.status === 'streaming'
  return (
    <span
      role="img"
      title={segmentLabel(view)}
      aria-label={segmentLabel(view)}
      style={{
        position: 'relative',
        display: 'flex',
        flex: 1,
        minWidth: 0,
        height: 18,
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
        borderRadius: radius.sm,
        backgroundColor: v('scoreboard-line'),
        color: v('scoreboard-ink'),
        fontSize: typeScale.micro,
        fontWeight: fontWeight.black,
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {streaming ? (
        <span
          className="motion-safe:animate-pulse"
          style={{
            position: 'absolute',
            inset: 0,
            backgroundColor: v(tone.base),
            opacity: 0.45,
          }}
        />
      ) : (
        <span
          style={{
            position: 'absolute',
            inset: 0,
            backgroundColor: v(tone.base),
          }}
        />
      )}
      <span style={{ position: 'relative' }}>{view.slot}</span>
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
    <div
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 30,
        backgroundColor: v('scoreboard'),
        color: v('scoreboard-ink'),
        borderBottom: `2px solid ${v('scoreboard-accent')}`,
      }}
    >
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: `${spacing.sm} ${spacing.lg}`,
          maxWidth: layout.pageMaxWidth,
          margin: '0 auto',
          padding: `${spacing.sm} ${spacing.md}`,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: spacing.sm }}>
          <span
            className={live ? 'motion-safe:animate-pulse' : undefined}
            style={{
              display: 'inline-block',
              width: 10,
              height: 10,
              borderRadius: radius.pill,
              backgroundColor: v(live ? 'scoreboard-accent' : 'success'),
            }}
          />
          <span
            style={{
              fontSize: typeScale.phase,
              fontWeight: fontWeight.black,
              letterSpacing: '0.02em',
            }}
          >
            {battlePhaseLabels[phase]}
          </span>
        </span>

        <span
          role="progressbar"
          aria-label={`已完成 ${finished} 名，共 ${total} 名选手`}
          aria-valuenow={finished}
          aria-valuemin={0}
          aria-valuemax={total}
          style={{
            display: 'flex',
            flex: 1,
            minWidth: 120,
            gap: 3,
            padding: `${spacing.xs} 0`,
          }}
        >
          {contestants.map((view) => (
            <ProgressSegment key={view.slot} view={view} />
          ))}
        </span>

        <span
          style={{
            display: 'inline-flex',
            alignItems: 'baseline',
            gap: spacing.xs,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <span
            style={{
              fontSize: typeScale.scoreboard,
              fontWeight: fontWeight.black,
              color: v('scoreboard-accent'),
              lineHeight: 1,
            }}
          >
            {finished}
          </span>
          <span style={{ fontSize: typeScale.label, color: v('scoreboard-muted') }}>
            / {total} 名选手完成
          </span>
          {streaming > 0 ? (
            <span
              style={{
                marginLeft: spacing.xs,
                padding: `1px ${spacing.sm}`,
                borderRadius: radius.pill,
                backgroundColor: v('live-soft'),
                color: v('live'),
                fontSize: typeScale.micro,
                fontWeight: fontWeight.bold,
              }}
            >
              {streaming} 人仍在输出
            </span>
          ) : null}
        </span>

        <span
          style={{
            marginLeft: 'auto',
            display: 'inline-flex',
            alignItems: 'baseline',
            gap: spacing.sm,
          }}
        >
          <span
            style={{
              fontSize: typeScale.micro,
              letterSpacing: '0.14em',
              color: v('scoreboard-muted'),
            }}
          >
            本场用时
          </span>
          <span
            suppressHydrationWarning
            style={{
              fontFamily: 'var(--font-geist-mono, monospace)',
              fontSize: typeScale.title,
              fontWeight: fontWeight.black,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {elapsed}
          </span>
        </span>
      </div>

      {failureReason ? (
        <p
          style={{
            margin: 0,
            padding: `${spacing.xs} ${spacing.md}`,
            backgroundColor: v('failure'),
            color: v('on-tone'),
            fontSize: typeScale.label,
            fontWeight: fontWeight.bold,
          }}
        >
          {failureReason}
        </p>
      ) : null}
    </div>
  )
}
