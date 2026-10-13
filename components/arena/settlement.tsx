'use client'

import { CircleCheck, CornerDownRight, ScrollText, Timer, TriangleAlert } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { ArenaId } from '@/lib/arena/types'
import type { BattleResult, ContestantOutcome, ContestantStatus } from '@/lib/battle/events'
import type { Snapshot } from '@/lib/db/repo'
import { ARENA_SKINS, providerAccent } from './theme'
import { VoteBar } from './vote-bar'

export interface SettlementProps {
  arena: ArenaId
  topic: string
  result: BattleResult
  snapshot: Snapshot
  providerLabels: Record<string, string>
}

interface CrashMoment {
  slot: number
  providerId: string
  modelId: string
  kind: 'timeout' | 'empty' | 'shortest' | 'latest'
  text: string
}

function providerLabelOf(labels: Record<string, string>, providerId: string, fallback: string): string {
  return labels[providerId] ?? fallback
}

function buildSummary(
  arena: ArenaId,
  result: BattleResult,
  crashes: CrashMoment[],
  providerLabels: Record<string, string>,
): string {
  const outcomes = result.contestants
  const allFailed = outcomes.length > 0 && outcomes.every((o) => o.status !== 'ok')
  const noCrash = crashes.length === 0
  const timeoutCount = outcomes.filter((o) => o.status === 'timeout').length
  const failCount = outcomes.filter((o) => o.status !== 'ok').length

  if (arena === 'hard-code') {
    const ranked = [...outcomes].sort((a, b) => {
      const aPassed = verdictPassed(result, a.slot)
      const bPassed = verdictPassed(result, b.slot)
      return bPassed - aPassed
    })
    if (ranked.length === 0) return '本场无有效选手'
    const winner = ranked[0]
    const topScore = verdictPassed(result, winner.slot)
    const allTie = ranked.every((o) => verdictPassed(result, o.slot) === topScore)
    if (allFailed) return '全员翻车，本场无人生还'
    if (allTie && failCount === 0) return `全员并列通过 ${topScore} 题，势均力敌`
    const winnerLabel = providerLabelOf(providerLabels, winner.providerId, winner.providerId)
    return `${winnerLabel} 以 ${topScore} 题通关${noCrash ? '，全场零失误' : `，${failCount} 人翻车`}`
  }

  if (allFailed) return '全员翻车，本场无人生还'
  const votedOutcomes = outcomes.filter((o) => o.status === 'ok')
  if (votedOutcomes.length === 1) {
    const sole = providerLabelOf(providerLabels, votedOutcomes[0].providerId, votedOutcomes[0].providerId)
    return `${sole} 技压全场，${timeoutCount} 人超时`
  }
  if (noCrash) return '全员完赛，一场龙争虎斗'
  if (timeoutCount > 0) return `${failCount} 人翻车，${timeoutCount} 人超时，场面惨烈`
  return `${failCount} 人翻车，${outcomes.length - failCount} 人站稳`
}

function verdictPassed(result: BattleResult, slot: number): number {
  const match = result.verdicts.find((v) => v.contestantSlot === slot)
  if (!match) return 0
  return match.passed
}

function verdictTotal(result: BattleResult, slot: number): number {
  const match = result.verdicts.find((v) => v.contestantSlot === slot)
  if (!match) return 0
  return match.total
}

function passRate(result: BattleResult, slot: number): number {
  const passed = verdictPassed(result, slot)
  const total = verdictTotal(result, slot)
  if (total <= 0) return 0
  return Math.round((passed / total) * 100)
}

function collectCrashes(result: BattleResult, labels: Record<string, string>): CrashMoment[] {
  const moments: CrashMoment[] = []
  let shortest: ContestantOutcome | null = null
  let latest: ContestantOutcome | null = null
  for (const o of result.contestants) {
    if (o.status === 'timeout') {
      moments.push({
        slot: o.slot,
        providerId: providerLabelOf(labels, o.providerId, o.providerId),
        modelId: o.modelId,
        kind: 'timeout',
        text: `${providerLabelOf(labels, o.providerId, o.providerId)}（${o.modelId}）超时未完成`,
      })
    } else if (o.status === 'empty') {
      moments.push({
        slot: o.slot,
        providerId: providerLabelOf(labels, o.providerId, o.providerId),
        modelId: o.modelId,
        kind: 'empty',
        text: `${providerLabelOf(labels, o.providerId, o.providerId)}（${o.modelId}）未返回内容`,
      })
    }
    if (o.status === 'ok' && o.outputLength > 0) {
      if (!shortest || o.outputLength < shortest.outputLength) shortest = o
    }
    if (o.latencyMs > 0) {
      if (!latest || o.latencyMs > latest.latencyMs) latest = o
    }
  }
  if (shortest && shortest.outputLength < 50) {
    moments.push({
      slot: shortest.slot,
      providerId: providerLabelOf(labels, shortest.providerId, shortest.providerId),
      modelId: shortest.modelId,
      kind: 'shortest',
      text: `${providerLabelOf(labels, shortest.providerId, shortest.providerId)} 输出最短（仅 ${shortest.outputLength} 字）`,
    })
  }
  if (latest && latest.latencyMs >= 60000) {
    moments.push({
      slot: latest.slot,
      providerId: providerLabelOf(labels, latest.providerId, latest.providerId),
      modelId: latest.modelId,
      kind: 'latest',
      text: `${providerLabelOf(labels, latest.providerId, latest.providerId)} 最后完成（${(latest.latencyMs / 1000).toFixed(1)} 秒）`,
    })
  }
  return moments
}

function statusText(status: ContestantStatus): string {
  switch (status) {
    case 'timeout':
      return '超时未完成'
    case 'rate_limited':
      return '被限流'
    case 'auth_failed':
      return '鉴权失败'
    case 'provider_error':
      return '调用失败'
    case 'empty':
      return '未返回内容'
    case 'ok':
      return '完成'
    default:
      return '执行出错'
  }
}

function Panel({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="rounded-xl border border-hairline bg-panel">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-hairline px-5 py-3">
        <h3 className="text-sm font-bold">{title}</h3>
        {hint ? <span className="font-mono text-[11px] text-dust">{hint}</span> : null}
      </header>
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

function ScoreRow({
  label,
  value,
  accentClass,
}: {
  label: string
  value: number
  accentClass: string
}) {
  return (
    <span className="flex items-center gap-2">
      <span className="w-8 shrink-0 text-[11px] text-dust">{label}</span>
      <span className="block h-1 flex-1 overflow-hidden rounded-full bg-raise">
        <span
          className={cn('block h-full rounded-full', accentClass)}
          style={{ width: `${Math.max(0, Math.min(100, value * 10))}%` }}
        />
      </span>
      <span className="w-8 shrink-0 text-right font-mono text-[11px] font-bold tabular-nums text-ink">
        {value}
      </span>
    </span>
  )
}

export function Settlement({ arena, topic, result, snapshot, providerLabels }: SettlementProps) {
  const crashes = collectCrashes(result, providerLabels)
  const summary = buildSummary(arena, result, crashes, providerLabels)
  const hasDegradedJudge = result.judgeAvailable === false && arena === 'soft'
  const skin = ARENA_SKINS[arena]
  const contestsForVote = result.contestants.map((o) => ({
    slot: o.slot,
    contestantId: snapshot.contestants.find((c) => c.slot === o.slot)?.id ?? null,
    providerId: o.providerId,
    modelId: o.modelId,
  }))
  const tally: Record<string, number> = {}
  for (const vote of snapshot.votes) {
    tally[vote.contestant_id] = (tally[vote.contestant_id] ?? 0) + 1
  }

  const failed = result.contestants.filter((o) => o.status !== 'ok')
  const extraMoments = crashes.filter((c) => c.kind === 'shortest' || c.kind === 'latest')

  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-5 px-5 pb-16">
      {/* ── 战报头条 ─────────────────────────────────────────── */}
      <header className="animate-rise overflow-hidden rounded-xl border border-hairline bg-panel">
        <div className="flex items-center gap-3 border-b border-hairline bg-raise/60 px-5 py-3">
          <ScrollText className="h-4 w-4 text-blaze" />
          <span className="font-mono text-[11px] font-bold uppercase tracking-wider text-dust">
            战报 · {skin.label}
          </span>
          <span className="ml-auto font-mono text-[11px] text-dust">{result.battleId}</span>
        </div>
        <div className="px-5 py-5">
          <h2 className="text-xl font-bold leading-snug sm:text-[22px]">{summary}</h2>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-[1.75] text-ash">{topic}</p>
        </div>
      </header>

      {/* ── 观众投票 ─────────────────────────────────────────── */}
      <div className="rounded-xl border border-hairline bg-panel p-5">
        <VoteBar
          battleId={result.battleId}
          contestants={contestsForVote}
          tally={tally}
          disabled={arena === 'hard-code'}
        />
      </div>

      {hasDegradedJudge ? (
        <p className="rounded-lg border border-warn/30 bg-warn/10 px-4 py-2.5 text-xs font-medium text-warn">
          裁判模型不可用，本场仅靠人类投票判定
        </p>
      ) : null}

      {/* ── 记分板 ───────────────────────────────────────────── */}
      <div className={cn('grid gap-4', arena === 'soft' ? 'lg:grid-cols-2' : 'grid-cols-1')}>
        {arena === 'soft' ? <JudgePanel result={result} providerLabels={providerLabels} /> : null}
        {arena === 'hard-code' ? (
          <HardcodePanel result={result} snapshot={snapshot} providerLabels={providerLabels} />
        ) : null}
      </div>

      {/* ── 翻车现场 ─────────────────────────────────────────── */}
      <Panel
        title="翻车现场"
        hint={failed.length + extraMoments.length > 0 ? `${failed.length + extraMoments.length} 起` : '全场零失误'}
      >
        {failed.length === 0 && extraMoments.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-ok">
            <CircleCheck className="h-4 w-4" />
            本场无人翻车，全员站稳
          </p>
        ) : (
          <ul className="flex flex-col gap-2.5">
            {failed.map((o) => (
              <li key={`failed-${o.slot}`} className="flex items-start gap-2.5 text-sm">
                <TriangleAlert
                  className={cn(
                    'mt-0.5 h-4 w-4 shrink-0',
                    o.status === 'timeout' ? 'text-warn' : 'text-bad',
                  )}
                />
                <span className="leading-relaxed">
                  <span className="font-semibold">
                    {providerLabelOf(providerLabels, o.providerId, o.providerId)}
                  </span>
                  <span className="ml-1.5 font-mono text-[11px] text-dust">{o.modelId}</span>
                  <span
                    className={cn('ml-2 text-xs font-semibold', o.status === 'timeout' ? 'text-warn' : 'text-bad')}
                  >
                    {statusText(o.status)}
                  </span>
                  {o.errorMessage ? (
                    <span className="ml-2 text-xs leading-relaxed text-dust">{o.errorMessage}</span>
                  ) : null}
                </span>
              </li>
            ))}
            {extraMoments.map((crash, index) => (
              <li
                key={`${crash.kind}-${crash.slot}-${index}`}
                className="flex items-start gap-2.5 text-sm"
              >
                {crash.kind === 'shortest' ? (
                  <CornerDownRight className="mt-0.5 h-4 w-4 shrink-0 text-dust" />
                ) : (
                  <Timer className="mt-0.5 h-4 w-4 shrink-0 text-dust" />
                )}
                <span className="leading-relaxed text-ash">{crash.text}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </section>
  )
}

function JudgePanel({
  result,
  providerLabels,
}: {
  result: BattleResult
  providerLabels: Record<string, string>
}) {
  return (
    <Panel title="AI 裁判" hint="双轮位置对调 · 分数独立展示，不合成总分">
      {result.verdicts.length === 0 ? (
        <p className="text-sm text-dust">本场无裁判打分</p>
      ) : (
        <ul className="flex flex-col gap-4">
          {result.verdicts.map((verdict) => {
            const outcome = result.contestants.find((c) => c.slot === verdict.contestantSlot)
            const label = outcome
              ? providerLabelOf(providerLabels, outcome.providerId, outcome.providerId)
              : `${verdict.contestantSlot} 号`
            const accent = outcome ? providerAccent(outcome.providerId) : '#8b8b94'
            return (
              <li key={verdict.contestantSlot} className="flex flex-col gap-2">
                <span className="flex items-baseline gap-2">
                  <span
                    className="h-2 w-2 shrink-0 self-center rounded-full"
                    style={{ backgroundColor: accent }}
                    aria-hidden
                  />
                  <span className="text-sm font-semibold">{label}</span>
                  {outcome ? (
                    <span className="truncate font-mono text-[11px] text-dust">{outcome.modelId}</span>
                  ) : null}
                </span>
                <div className="grid gap-1.5 pl-4 sm:grid-cols-3">
                  <ScoreRow label="逻辑" value={verdict.scores.logic ?? 0} accentClass="bg-soft-arena/80" />
                  <ScoreRow label="论据" value={verdict.scores.evidence ?? 0} accentClass="bg-soft-arena/80" />
                  <ScoreRow label="切题" value={verdict.scores.relevance ?? 0} accentClass="bg-soft-arena/80" />
                </div>
                {verdict.rationale ? (
                  <p className="pl-4 text-[13px] leading-relaxed text-ash">{verdict.rationale}</p>
                ) : null}
              </li>
            )
          })}
        </ul>
      )}
      {result.judgeAvailable ? null : (
        <p className="mt-3 text-[11px] text-dust">本场仅人类投票</p>
      )}
    </Panel>
  )
}

function HardcodePanel({
  result,
  snapshot,
  providerLabels,
}: {
  result: BattleResult
  snapshot: Snapshot
  providerLabels: Record<string, string>
}) {
  const isolationLevels = new Set(snapshot.sandboxResults.map((r) => r.isolation_level))
  const isolationNote =
    isolationLevels.size === 1
      ? snapshot.sandboxResults[0]?.isolation_level === 'wasm-worker'
        ? 'WASM 语言级沙箱，非 OS 级隔离'
        : snapshot.sandboxResults[0]?.isolation_level ?? '未知'
      : '混合'

  return (
    <Panel title="沙箱执行结果" hint={`隔离级别：${isolationNote}`}>
      <ul className="flex flex-col divide-y divide-hairline">
        {result.contestants.map((outcome) => {
          const passed = verdictPassed(result, outcome.slot)
          const total = verdictTotal(result, outcome.slot)
          const rate = passRate(result, outcome.slot)
          const sandbox = snapshot.sandboxResults.find((s) => {
            const contestant = snapshot.contestants.find((c) => c.slot === outcome.slot)
            return contestant && s.contestant_id === contestant.id
          })
          const accent = providerAccent(outcome.providerId)
          return (
            <li key={outcome.slot} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0">
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md font-mono text-[10px] font-bold tabular-nums"
                style={{ backgroundColor: `${accent}1f`, color: accent }}
              >
                {outcome.slot}
              </span>
              <span className="flex min-w-0 flex-1 items-baseline gap-2">
                <span className="truncate text-sm font-semibold">
                  {providerLabelOf(providerLabels, outcome.providerId, outcome.providerId)}
                </span>
                <span className="truncate font-mono text-[11px] text-dust">{outcome.modelId}</span>
              </span>
              <span className="flex items-center gap-2.5">
                <span className="relative block h-1.5 w-20 overflow-hidden rounded-full bg-raise">
                  <span
                    className={cn(
                      'absolute inset-0 h-full rounded-full transition-[width] duration-500 ease-out',
                      rate >= 80 ? 'bg-ok' : rate >= 40 ? 'bg-warn' : 'bg-bad',
                    )}
                    style={{ width: `${rate}%` }}
                  />
                </span>
                <span className="min-w-12 text-right font-mono text-xs font-bold tabular-nums text-ink">
                  {passed}/{total}
                </span>
              </span>
              <span className="font-mono text-[11px] tabular-nums text-dust">
                {sandbox?.time_ms ?? 0}ms
                {sandbox?.degraded ? <span className="ml-2 text-warn">降级</span> : null}
              </span>
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}
