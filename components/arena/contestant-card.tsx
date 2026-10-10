'use client'

import { contestantStatusLabels } from '@/lib/battle/events'
import type { ContestantView } from '@/lib/client/battle-store'
import {
  fontWeight,
  layout,
  lineHeight,
  metricBar,
  radius,
  spacing,
  statusTone,
  tonePair,
  typeScale,
  v,
} from './theme'

export interface ContestantCardProps {
  view: ContestantView
  providerLabel?: string
  maxLatencyMs: number
}

function statusLabel(view: ContestantView): string {
  if (view.status === 'streaming') return '输出中'
  if (view.status === 'ok') return '完成'
  return contestantStatusLabels[view.status]
}

function failureTitle(view: ContestantView): string {
  if (view.status === 'streaming' || view.status === 'ok') return ''
  return view.status === 'timeout' ? '超时未完成' : contestantStatusLabels[view.status]
}

function StatusChip({ view }: { view: ContestantView }) {
  const tone = tonePair[statusTone(view.status)]
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: spacing.xs,
        padding: `2px ${spacing.sm}`,
        borderRadius: radius.sm,
        backgroundColor: v(tone.soft),
        color: v(tone.base),
        fontSize: typeScale.micro,
        fontWeight: fontWeight.black,
        whiteSpace: 'nowrap',
      }}
    >
      {view.status === 'streaming' ? (
        <span
          className="motion-safe:animate-pulse"
          style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: radius.pill,
            backgroundColor: v(tone.base),
          }}
        />
      ) : null}
      {statusLabel(view)}
    </span>
  )
}

function LatencyMeter({ view, maxLatencyMs }: { view: ContestantView; maxLatencyMs: number }) {
  if (view.latencyMs === null) {
    return (
      <span style={{ fontVariantNumeric: 'tabular-nums' }}>
        {view.status === 'streaming' ? '计时中' : '—'}
      </span>
    )
  }
  const ratio = maxLatencyMs > 0 ? view.latencyMs / maxLatencyMs : 1
  const percent = Math.max(4, Math.min(100, Math.round(ratio * 100)))
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: spacing.sm }}>
      <span
        role="meter"
        aria-label={`响应耗时 ${(view.latencyMs / 1000).toFixed(1)} 秒，为本场最慢的 ${percent}%`}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{
          display: 'block',
          width: metricBar.width,
          height: metricBar.height,
          borderRadius: radius.pill,
          backgroundColor: v('surface-sunken'),
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            display: 'block',
            height: '100%',
            width: `${percent}%`,
            borderRadius: radius.pill,
            backgroundColor: v('ink-muted'),
            transition: 'width 240ms ease-out',
          }}
        />
      </span>
      <span
        style={{
          fontVariantNumeric: 'tabular-nums',
          fontWeight: fontWeight.bold,
          color: v('ink-muted'),
        }}
      >
        {(view.latencyMs / 1000).toFixed(1)}s
      </span>
    </span>
  )
}

export function ContestantCard({ view, providerLabel, maxLatencyMs }: ContestantCardProps) {
  const tone = tonePair[statusTone(view.status)]
  const streaming = view.status === 'streaming'
  const failed = view.status !== 'streaming' && view.status !== 'ok'

  return (
    <article
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        minHeight: layout.cardMinHeight,
        overflow: 'hidden',
        border: `1px solid ${v('border')}`,
        borderRadius: radius.lg,
        backgroundColor: v('surface'),
      }}
    >
      <div style={{ height: 4, backgroundColor: v(tone.base) }} />

      <header
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: spacing.sm,
          padding: spacing.md,
          borderBottom: `1px solid ${v('border')}`,
        }}
      >
        <span
          title={`${view.slot} 号选手`}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: layout.badge.size,
            height: layout.badge.size,
            borderRadius: radius.md,
            backgroundColor: v('scoreboard'),
            color: v('scoreboard-accent'),
            fontSize: typeScale.label,
            fontWeight: fontWeight.black,
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {view.slot}
        </span>
        {view.isFirstFinisher ? (
          <span
            style={{
              padding: `2px ${spacing.sm}`,
              borderRadius: radius.pill,
              border: `1px solid ${v('first')}`,
              backgroundColor: v('first-soft'),
              color: v('first'),
              fontSize: typeScale.micro,
              fontWeight: fontWeight.black,
              letterSpacing: '0.08em',
            }}
          >
            先手
          </span>
        ) : null}
        <span style={{ fontSize: typeScale.label, fontWeight: fontWeight.black, color: v('ink') }}>
          {providerLabel ?? view.providerId}
        </span>
        <span
          style={{
            padding: `1px ${spacing.xs}`,
            borderRadius: radius.sm,
            backgroundColor: v('surface-alt'),
            color: v('ink-faint'),
            fontFamily: 'var(--font-geist-mono, monospace)',
            fontSize: typeScale.micro,
          }}
        >
          {view.modelId}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <StatusChip view={view} />
        </span>
      </header>

      {failed ? (
        <div
          style={{
            padding: `${spacing.sm} ${spacing.md}`,
            borderBottom: `1px solid ${v('border')}`,
            backgroundColor: v(tone.soft),
            color: v(tone.base),
          }}
        >
          <p style={{ fontSize: typeScale.label, fontWeight: fontWeight.black }}>
            {failureTitle(view)}
          </p>
          {view.failureMessage ? (
            <p
              style={{
                marginTop: 2,
                fontSize: typeScale.body,
                fontWeight: fontWeight.medium,
                opacity: 0.92,
              }}
            >
              {view.failureMessage}
            </p>
          ) : null}
        </div>
      ) : null}

      {view.text ? (
        <pre
          className="whitespace-pre-wrap break-words"
          style={{
            margin: 0,
            padding: spacing.md,
            fontFamily: 'inherit',
            fontSize: typeScale.body,
            lineHeight: lineHeight.body,
            color: v('ink'),
          }}
        >
          {view.text}
          {streaming ? (
            <span
              className="motion-safe:animate-pulse"
              style={{
                display: 'inline-block',
                width: 3,
                height: '0.9em',
                marginLeft: 2,
                verticalAlign: 'baseline',
                backgroundColor: v('live'),
              }}
            />
          ) : null}
        </pre>
      ) : (
        <p
          style={{
            margin: 0,
            padding: spacing.md,
            fontSize: typeScale.body,
            color: v('ink-faint'),
          }}
        >
          {streaming ? '正在等待第一行输出…' : '本场没有留下任何输出'}
        </p>
      )}

      <footer
        style={{
          marginTop: 'auto',
          display: 'flex',
          alignItems: 'center',
          gap: spacing.sm,
          padding: `${spacing.sm} ${spacing.md}`,
          borderTop: `1px solid ${v('border')}`,
          color: v('ink-faint'),
          fontSize: typeScale.micro,
        }}
      >
        <span>{view.text.length} 字</span>
        <span style={{ marginLeft: 'auto' }}>
          <LatencyMeter view={view} maxLatencyMs={maxLatencyMs} />
        </span>
      </footer>
    </article>
  )
}
