'use client'

import { Brain, Zap } from 'lucide-react'
import { cn } from '@/lib/cn'
import { contestantStatusLabels } from '@/lib/battle/events'
import type { ArenaId } from '@/lib/arena/types'
import { applyCandidate, collectMoveCandidates } from '@/lib/arena/chess'
import { fullText, type ContestantView } from '@/lib/client/battle-store'
import {
  SANDBOX_LABELS,
  SANDBOX_TONE,
  TONE_CLASSES,
  isFailedStatus,
  providerAccent,
  statusTone,
} from './theme'

export interface ContestantCardProps {
  view: ContestantView
  arena: ArenaId
  providerLabel?: string
  maxLatencyMs: number
  puzzle?: { fen: string; mateIn: 1 | 2 } | null
  verdict?: { passed: number; total: number; rationale: string } | null
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
  const tone = statusTone(view.status)
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 font-mono text-[10px] font-semibold',
        TONE_CLASSES[tone].chip,
      )}
    >
      <span aria-hidden className={cn('h-1.5 w-1.5 rounded-full', TONE_CLASSES[tone].dot)} />
      {statusLabel(view)}
    </span>
  )
}

function ReasoningPanel({ reasoning, live }: { reasoning: string; live: boolean }) {
  if (reasoning.length === 0) return null
  if (live) {
    return (
      <div className="border-b border-hairline bg-void/60 px-4 py-2">
        <p className="flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-wider text-dust">
          <span aria-hidden className="h-1.5 w-1.5 animate-live rounded-full bg-blaze" />
          思考中 · {reasoning.length} 字
        </p>
        <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed text-dust">
          {reasoning}
        </p>
      </div>
    )
  }
  return (
    <details className="group border-b border-hairline bg-void/40">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-dust transition-colors hover:text-ash">
        <Brain className="h-3 w-3" />
        思考过程 · {reasoning.length} 字
        <span aria-hidden className="ml-auto transition-transform group-open:rotate-90">
          ›
        </span>
      </summary>
      <p className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words px-4 pb-3 text-[12px] leading-relaxed text-dust">
        {reasoning}
      </p>
    </details>
  )
}

function LatencyMeter({ view, maxLatencyMs }: { view: ContestantView; maxLatencyMs: number }) {
  if (view.latencyMs === null) {
    return (
      <span className="font-mono text-[11px] tabular-nums text-dust">
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
        className="block h-1 w-16 overflow-hidden rounded-full bg-raise"
      >
        <span
          className="block h-full rounded-full bg-blaze transition-[width] duration-300"
          style={{ width: `${percent}%` }}
        />
      </span>
      <span className="font-mono text-[11px] font-bold tabular-nums text-ink">
        {(view.latencyMs / 1000).toFixed(1)}s
      </span>
    </span>
  )
}

function firstLegalMove(fen: string, raw: string): string | null {
  for (const candidate of collectMoveCandidates(raw)) {
    const attempt = applyCandidate(fen, candidate)
    if (attempt) return attempt.san
  }
  return null
}

export function ContestantCard({
  view,
  arena,
  providerLabel,
  maxLatencyMs,
  puzzle,
  verdict,
}: ContestantCardProps) {
  const tone = statusTone(view.status)
  const streaming = view.status === 'streaming'
  const failed = isFailedStatus(view.status)
  const accent = providerAccent(view.providerId)
  const rawText = fullText(view)

  const className = cn(
    'relative flex min-h-64 flex-col overflow-hidden rounded-xl border bg-panel',
    streaming ? TONE_CLASSES[tone].edge : failed ? TONE_CLASSES[tone].edge : 'border-hairline',
  )

  return (
    <article className={className} style={{ borderLeftWidth: 2, borderLeftColor: accent }}>
      <header className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 px-4 pb-2 pt-3.5">
        <span
          title={`${view.slot} 号选手`}
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md font-mono text-[11px] font-bold tabular-nums"
          style={{ backgroundColor: `${accent}1f`, color: accent }}
        >
          {view.slot}
        </span>
        {view.isFirstFinisher ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-blaze/30 bg-blaze/10 px-2 py-0.5 text-[10px] font-bold text-blaze">
            <Zap className="h-3 w-3" />
            先手
          </span>
        ) : null}
        <span className="text-sm font-bold">{providerLabel ?? view.providerId}</span>
        <span className="truncate font-mono text-[11px] text-dust">{view.modelId}</span>
        <span className="ml-auto">
          <StatusChip view={view} />
        </span>
      </header>

      {failed ? (
        <div className={cn('border-y px-4 py-2', TONE_CLASSES[tone].banner)}>
          <p className="text-xs font-bold">{failureTitle(view)}</p>
          {view.failureMessage ? (
            <p className="mt-0.5 text-xs leading-relaxed opacity-85">{view.failureMessage}</p>
          ) : null}
        </div>
      ) : null}

      <ReasoningPanel
        reasoning={view.turns.map((turn) => turn.reasoning).join('')}
        live={streaming && rawText.length === 0}
      />

      {arena === 'chess' && puzzle ? (
        <div className="flex flex-1 flex-col gap-2 px-4 py-3">
          {rawText ? (
            <>
              <span className="font-mono text-[11px] uppercase tracking-wider text-dust">着法</span>
              <span className="font-mono text-xl font-bold text-ink">
                {firstLegalMove(puzzle.fen, rawText) ?? '未能识别出合法着法'}
              </span>
              {verdict ? (
                <span
                  className={cn(
                    'inline-flex w-fit items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-semibold',
                    verdict.passed > 0 ? TONE_CLASSES.success.chip : TONE_CLASSES.failure.chip,
                  )}
                >
                  {verdict.passed > 0 ? `命中 ${puzzle.mateIn} 步杀` : '未命中杀法'}
                </span>
              ) : null}
              {verdict?.rationale ? (
                <p className="text-[12px] leading-relaxed text-ash">{verdict.rationale}</p>
              ) : null}
              <details className="mt-auto">
                <summary className="cursor-pointer font-mono text-[11px] text-dust">
                  原始输出
                </summary>
                <pre className="mt-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-void p-2.5 font-mono text-[11.5px] leading-relaxed text-ash">
                  {rawText}
                </pre>
              </details>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center py-8">
              <p className="text-[13px] text-dust">
                {streaming ? (
                  <span className="inline-flex items-center gap-2">
                    <span aria-hidden className="h-1.5 w-1.5 animate-live rounded-full bg-ok" />
                    落子中…
                  </span>
                ) : (
                  '本场没有留下输出'
                )}
              </p>
            </div>
          )}
        </div>
      ) : rawText ? (
        <div
          className={cn(
            'max-h-[26rem] flex-1 overflow-y-auto whitespace-pre-wrap break-words px-4 py-3 text-ink/90',
            arena === 'hard-code'
              ? 'font-mono text-[12.5px] leading-[1.7]'
              : 'text-[14.5px] leading-[1.75]',
          )}
        >
          {rawText}
          {streaming ? (
            <span
              aria-hidden
              className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-caret bg-blaze align-baseline"
            />
          ) : null}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center px-4 py-10">
          <p className="text-[13px] text-dust">
            {streaming ? (
              <span className="inline-flex items-center gap-2">
                <span aria-hidden className="h-1.5 w-1.5 animate-live rounded-full bg-ok" />
                等待第一行输出…
              </span>
            ) : (
              '本场没有留下任何输出'
            )}
          </p>
        </div>
      )}

      <footer className="flex items-center gap-x-3 border-t border-hairline px-4 py-2 font-mono text-[11px] text-dust">
        <span className="tabular-nums">{rawText.length} 字</span>
        {view.sandbox ? (
          <span
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-semibold',
              TONE_CLASSES[SANDBOX_TONE[view.sandbox.status]].chip,
            )}
          >
            <span
              aria-hidden
              className={cn('h-1.5 w-1.5 rounded-full', TONE_CLASSES[SANDBOX_TONE[view.sandbox.status]].dot)}
            />
            {view.sandbox.status === 'unavailable'
              ? SANDBOX_LABELS.unavailable
              : `沙箱 ${view.sandbox.passed}/${view.sandbox.total}`}
            {view.sandbox.timeMs !== null && view.sandbox.status !== 'unavailable'
              ? ` · ${view.sandbox.timeMs}ms`
              : ''}
          </span>
        ) : null}
        <span className="ml-auto">
          <LatencyMeter view={view} maxLatencyMs={maxLatencyMs} />
        </span>
      </footer>
    </article>
  )
}
