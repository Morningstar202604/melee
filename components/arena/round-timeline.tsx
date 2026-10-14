'use client'

import { Brain, Zap } from 'lucide-react'
import { cn } from '@/lib/cn'
import { contestantStatusLabels } from '@/lib/battle/events'
import type { ArenaId } from '@/lib/arena/types'
import type { ContestantTurn, ContestantView } from '@/lib/client/battle-store'
import { ARENA_SKINS, TONE_CLASSES, providerAccent } from './theme'

export interface RoundTimelineProps {
  arena: ArenaId
  views: ContestantView[]
  currentRound: number
  totalRounds: number
  providerLabels: Record<string, string>
}

const ROUND_TITLES: Record<string, string[]> = {
  debate: ['立论', '驳斥', '结辩'],
  banter: ['开局炸场', '接招反击', '收场补刀'],
}

function turnOf(view: ContestantView, round: number): ContestantTurn | null {
  return view.turns.find((turn) => turn.round === round) ?? null
}

function turnStatusLabel(turn: ContestantTurn): string {
  if (turn.status === 'streaming') return '输出中'
  if (turn.status === 'ok') return '完成'
  return contestantStatusLabels[turn.status]
}

function Bubble({
  view,
  turn,
  providerLabel,
}: {
  view: ContestantView
  turn: ContestantTurn | null
  providerLabel?: string
}) {
  const accent = providerAccent(view.providerId)
  const streaming = turn?.status === 'streaming'
  const failed = turn ? turn.status !== 'streaming' && turn.status !== 'ok' : false
  const tone = streaming ? 'live' : failed ? 'failure' : 'success'

  return (
    <article
      className={cn(
        'flex min-w-0 flex-col rounded-xl border bg-panel',
        streaming ? TONE_CLASSES[tone].edge : failed ? TONE_CLASSES.failure.edge : 'border-hairline',
      )}
      style={{ borderTopWidth: 2, borderTopColor: accent }}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3.5 pb-1.5 pt-2.5">
        <span
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold tabular-nums"
          style={{ backgroundColor: `${accent}1f`, color: accent }}
        >
          {view.slot}
        </span>
        <span className="truncate text-[13px] font-bold">{providerLabel ?? view.providerId}</span>
        <span className="truncate font-mono text-[10px] text-dust">{view.modelId}</span>
        {view.isFirstFinisher ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-blaze/30 bg-blaze/10 px-1.5 py-0.5 text-[9px] font-bold text-blaze">
            <Zap className="h-2.5 w-2.5" />
            先手
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-2">
          {turn?.latencyMs !== null && turn?.latencyMs !== undefined && turn.status === 'ok' ? (
            <span className="font-mono text-[10px] tabular-nums text-dust">
              {(turn.latencyMs / 1000).toFixed(1)}s
            </span>
          ) : null}
          <span
            className={cn(
              'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-1.5 py-0.5 font-mono text-[9px] font-semibold',
              TONE_CLASSES[tone].chip,
            )}
          >
            <span aria-hidden className={cn('h-1 w-1 rounded-full', TONE_CLASSES[tone].dot)} />
            {turn ? turnStatusLabel(turn) : '未开始'}
          </span>
        </span>
      </header>

      {turn && turn.reasoning.length > 0 ? (
        streaming && turn.text.length === 0 ? (
          <div className="border-b border-hairline px-3.5 py-1.5">
            <p className="flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-wider text-dust">
              <span aria-hidden className="h-1.5 w-1.5 animate-live rounded-full bg-blaze" />
              思考中 · {turn.reasoning.length} 字
            </p>
            <p className="mt-1 max-h-32 overflow-y-auto whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-dust">
              {turn.reasoning}
            </p>
          </div>
        ) : (
          <details className="group border-b border-hairline">
            <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-dust transition-colors hover:text-ash">
              <Brain className="h-3 w-3" />
              思考过程 · {turn.reasoning.length} 字
              <span aria-hidden className="ml-auto transition-transform group-open:rotate-90">
                ›
              </span>
            </summary>
            <p className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words px-3.5 pb-2 text-[11.5px] leading-relaxed text-dust">
              {turn.reasoning}
            </p>
          </details>
        )
      ) : null}

      {turn?.text ? (
        <div className="whitespace-pre-wrap break-words px-3.5 pb-3 pt-1 text-[13.5px] leading-[1.72] text-ink/90">
          {turn.text}
          {streaming ? (
            <span
              aria-hidden
              className="ml-0.5 inline-block h-[1em] w-[2px] translate-y-[2px] animate-caret bg-blaze align-baseline"
            />
          ) : null}
        </div>
      ) : (
        <div className="px-3.5 pb-3 pt-1">
          <p className="text-[12px] text-dust">
            {turn?.failureMessage
              ? turn.failureMessage
              : streaming
                ? '酝酿中…'
                : failed
                  ? '本回合失败'
                  : '尚未发言'}
          </p>
        </div>
      )}
      {turn?.failureMessage && turn.text ? (
        <p className={cn('border-t px-3.5 py-1.5 text-[11px]', TONE_CLASSES.failure.banner)}>
          {turn.failureMessage}
        </p>
      ) : null}
    </article>
  )
}

export function RoundTimeline({
  arena,
  views,
  currentRound,
  totalRounds,
  providerLabels,
}: RoundTimelineProps) {
  const titles = ROUND_TITLES[arena] ?? []
  const skin = ARENA_SKINS[arena]
  const rounds = Array.from({ length: totalRounds }, (_, index) => index + 1)

  return (
    <div className="flex flex-col gap-5">
      {rounds.map((round) => {
        const hasAnyTurn = views.some((view) => turnOf(view, round) !== null)
        const pending = round > currentRound && !hasAnyTurn
        return (
          <section key={round} className="flex flex-col gap-3">
            <header className="flex items-center gap-3">
              <span
                className={cn(
                  'rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-bold',
                  pending ? 'border-hairline text-dust' : skin.accentChip,
                )}
              >
                第 {round} 轮{hasAnyTurn || !pending ? ` · ${titles[round - 1] ?? ''}` : ''}
              </span>
              <span className="h-px flex-1 bg-hairline" aria-hidden />
            </header>
            <div
              className={cn(
                'grid gap-3',
                views.length > 2 ? 'sm:grid-cols-2' : 'sm:grid-cols-2',
              )}
            >
              {views.map((view) => (
                <Bubble
                  key={view.slot}
                  view={view}
                  turn={turnOf(view, round)}
                  providerLabel={providerLabels[view.providerId]}
                />
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
