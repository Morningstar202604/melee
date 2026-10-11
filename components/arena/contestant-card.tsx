'use client'

import { contestantStatusLabels } from '@/lib/battle/events'
import type { ContestantView } from '@/lib/client/battle-store'
import { statusTone, toneClasses } from './theme'

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
  const tone = toneClasses[statusTone(view.status)]
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${tone.bg} ${tone.text}`}
    >
      {view.status === 'streaming' ? (
        <span className={`inline-block h-1.5 w-1.5 rounded-full animate-glow-pulse ${tone.dot}`} />
      ) : null}
      {statusLabel(view)}
    </span>
  )
}

function LatencyMeter({ view, maxLatencyMs }: { view: ContestantView; maxLatencyMs: number }) {
  if (view.latencyMs === null) {
    return (
      <span className="text-xs font-medium tabular-nums text-zinc-500 dark:text-zinc-400">
        {view.status === 'streaming' ? '计时中…' : '—'}
      </span>
    )
  }
  const ratio = maxLatencyMs > 0 ? view.latencyMs / maxLatencyMs : 1
  const percent = Math.max(4, Math.min(100, Math.round(ratio * 100)))
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="meter"
        aria-label={`响应耗时 ${(view.latencyMs / 1000).toFixed(1)} 秒`}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        className="block h-2 w-20 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"
      >
        <span
          className="block h-full rounded-full bg-gradient-to-r from-orange-500 to-amber-400 transition-[width] duration-300"
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="text-xs font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
        {(view.latencyMs / 1000).toFixed(1)}s
      </span>
    </span>
  )
}

const TONE_BAR: Record<string, string> = {
  live: 'bg-emerald-500',
  success: 'bg-emerald-500',
  failure: 'bg-red-500',
  timeout: 'bg-amber-500',
}

const TONE_SHADOW: Record<string, string> = {
  live: 'shadow-[0_0_0_2px_rgba(16,185,129,0.2),0_8px_24px_rgba(16,185,129,0.1)]',
  success: 'shadow-[0_0_0_2px_rgba(16,185,129,0.1)]',
  failure: 'shadow-[0_0_0_2px_rgba(239,68,68,0.15)]',
  timeout: 'shadow-[0_0_0_2px_rgba(245,158,11,0.15)]',
}

export function ContestantCard({ view, providerLabel, maxLatencyMs }: ContestantCardProps) {
  const tone = statusTone(view.status)
  const streaming = view.status === 'streaming'
  const failed = view.status !== 'streaming' && view.status !== 'ok'

  return (
    <article
      className={`relative flex min-h-[16rem] flex-col overflow-hidden rounded-2xl border border-zinc-200 bg-white transition-all dark:border-zinc-800 dark:bg-zinc-900 ${
        streaming ? TONE_SHADOW[tone] : 'shadow-sm'
      } ${streaming ? 'animate-fade-in-up' : ''}`}
    >
      {/* Top accent bar */}
      <div className={`h-1 ${TONE_BAR[tone]}`} />

      {/* Header */}
      <header className="flex flex-wrap items-center gap-2 px-5 pt-4 pb-2">
        <span
          title={`${view.slot} 号选手`}
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-zinc-900 text-xs font-black tabular-nums text-amber-400 dark:bg-zinc-800"
        >
          {view.slot}
        </span>
        {view.isFirstFinisher ? (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
            ⚡ 先手
          </span>
        ) : null}
        <span className="text-sm font-bold text-zinc-900 dark:text-zinc-50">
          {providerLabel ?? view.providerId}
        </span>
        <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          {view.modelId}
        </span>
        <span className="ml-auto">
          <StatusChip view={view} />
        </span>
      </header>

      {/* Failure banner */}
      {failed ? (
        <div className={`px-5 py-2 ${toneClasses[tone].bg} border-t ${toneClasses[tone].border}`}>
          <p className={`text-xs font-bold ${toneClasses[tone].text}`}>
            {failureTitle(view)}
          </p>
          {view.failureMessage ? (
            <p className={`mt-0.5 text-xs font-medium ${toneClasses[tone].text} opacity-85`}>
              {view.failureMessage}
            </p>
          ) : null}
        </div>
      ) : null}

      {/* Content area */}
      {view.text ? (
        <div className="whitespace-pre-wrap break-words px-5 py-3 text-[15px] leading-[1.7] text-zinc-800 dark:text-zinc-200 flex-1 overflow-y-auto max-h-96">
          {view.text}
          {streaming ? (
            <span className="ml-0.5 inline-block h-[1em] w-[3px] translate-y-[2px] rounded-sm bg-orange-500 animate-glow-pulse align-baseline" />
          ) : null}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center px-5 py-8">
          <p className="text-sm text-zinc-400">
            {streaming ? (
              <span className="inline-flex items-center gap-2">
                <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-glow-pulse" />
                等待第一行输出…
              </span>
            ) : (
              '本场没有留下任何输出'
            )}
          </p>
        </div>
      )}

      {/* Footer */}
      <footer className="flex items-center gap-2 border-t border-zinc-100 px-5 py-2 text-[11px] text-zinc-400 dark:border-zinc-800">
        <span>{view.text.length} 字</span>
        <span className="ml-auto">
          <LatencyMeter view={view} maxLatencyMs={maxLatencyMs} />
        </span>
      </footer>
    </article>
  )
}
