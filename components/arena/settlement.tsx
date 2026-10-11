'use client'

import type { ArenaId } from '@/lib/arena/types'
import type { BattleResult, ContestantOutcome, ContestantStatus } from '@/lib/battle/events'
import type { Snapshot } from '@/lib/db/repo'
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

function judgeScores(verdict: { scores: Record<string, number> } | undefined) {
  if (!verdict) return null
  return {
    logic: verdict.scores.logic ?? 0,
    evidence: verdict.scores.evidence ?? 0,
    relevance: verdict.scores.relevance ?? 0,
  }
}

export function Settlement({ arena, topic, result, snapshot, providerLabels }: SettlementProps) {
  const crashes = collectCrashes(result, providerLabels)
  const summary = buildSummary(arena, result, crashes, providerLabels)
  const hasDegradedJudge = result.judgeAvailable === false && arena === 'soft'
  const failedContestants = result.contestants.filter((o) => o.status !== 'ok')
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

  return (
    <section className="mx-auto flex max-w-5xl flex-col gap-6 px-4 pb-16 sm:px-6">
      {/* ── Summary Banner ─────────────────────────────────── */}
      <header className="animate-fade-in-up overflow-hidden rounded-2xl bg-gradient-to-br from-orange-500 to-amber-500 p-6 shadow-xl shadow-orange-500/20 sm:p-8 dark:shadow-orange-500/10">
        <p className="text-xs font-bold tracking-widest text-white/80 uppercase">
          {arena === 'soft' ? '🎭 软战场战报' : '🐍 硬战场战报'}
        </p>
        <h2 className="mt-2 text-2xl font-black leading-tight text-white sm:text-3xl">
          {summary}
        </h2>
        <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-white/85">
          {topic}
        </p>
      </header>

      {/* ── Vote Bar ──────────────────────────────────────── */}
      <VoteBar
        battleId={result.battleId}
        contestants={contestsForVote}
        tally={tally}
        disabled={arena === 'hard-code'}
      />

      {/* ── Banners ───────────────────────────────────────── */}
      {hasDegradedJudge ? (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs font-medium text-amber-700 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
          裁判模型不可用，本场仅靠人类投票判定
        </div>
      ) : null}

      {failedContestants.length > 0 ? (
        <div className="flex flex-col gap-1">
          {failedContestants.map((o) => (
            <p key={o.slot} className="text-sm font-medium text-red-600 dark:text-red-400">
              · {providerLabelOf(providerLabels, o.providerId, o.providerId)}（{o.modelId}）{statusText(o.status)}
              {o.errorMessage ? ` — ${o.errorMessage}` : ''}
            </p>
          ))}
        </div>
      ) : null}

      {/* ── Judge / Hardcode Panels ───────────────────────── */}
      <div className={`grid gap-4 ${arena === 'soft' ? 'sm:grid-cols-2' : 'grid-cols-1'}`}>
        {arena === 'soft' ? (
          <JudgePanel result={result} providerLabels={providerLabels} />
        ) : null}

        {arena === 'hard-code' ? (
          <HardcodePanel result={result} snapshot={snapshot} providerLabels={providerLabels} />
        ) : null}
      </div>

      {/* ── Crash Moments ────────────────────────────────── */}
      {crashes.length > 0 ? (
        <div>
          <p className="text-xs font-bold tracking-wider text-zinc-400 uppercase">翻车榜</p>
          <ul className="mt-2 list-disc pl-5 space-y-1">
            {crashes.map((crash, index) => (
              <li
                key={`${crash.kind}-${crash.slot}-${index}`}
                className="text-sm leading-relaxed text-zinc-500"
              >
                {crash.text}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
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
    <div className="flex flex-col gap-2 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-xs font-bold tracking-wider text-zinc-400 uppercase">
        AI 裁判（双轮位置对调）
      </p>
      {result.verdicts.length === 0 ? (
        <p className="text-sm text-zinc-500">本场无裁判打分</p>
      ) : (
        result.verdicts.map((verdict) => {
          const scores = judgeScores(verdict)
          const outcome = result.contestants.find((c) => c.slot === verdict.contestantSlot)
          const label = outcome ? providerLabelOf(providerLabels, outcome.providerId, outcome.providerId) : `${verdict.contestantSlot} 号`
          return (
            <div
              key={verdict.contestantSlot}
              className="flex flex-col gap-1 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-800"
            >
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-50">{label}</span>
              {scores ? (
                <div className="flex gap-4">
                  {[
                    { key: 'logic', label: '逻辑', value: scores.logic },
                    { key: 'evidence', label: '论据', value: scores.evidence },
                    { key: 'relevance', label: '切题', value: scores.relevance },
                  ].map((item) => (
                    <span key={item.key} className="text-[11px] text-zinc-500 font-medium">
                      {item.label} <strong className="text-zinc-900 dark:text-zinc-50">{item.value}</strong>
                    </span>
                  ))}
                </div>
              ) : null}
              {verdict.rationale ? (
                <p className="text-sm leading-relaxed text-zinc-500">{verdict.rationale}</p>
              ) : null}
            </div>
          )
        })
      )}
      <p className="text-[11px] text-zinc-400">
        {result.judgeAvailable ? '分数独立展示，不合成总分' : '本场仅人类投票'}
      </p>
    </div>
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
  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <p className="text-xs font-bold tracking-wider text-zinc-400 uppercase">
        沙箱执行结果
      </p>
      {result.contestants.map((outcome) => {
        const passed = verdictPassed(result, outcome.slot)
        const total = verdictTotal(result, outcome.slot)
        const rate = passRate(result, outcome.slot)
        const sandbox = snapshot.sandboxResults.find((s) => {
          const contestant = snapshot.contestants.find((c) => c.slot === outcome.slot)
          return contestant && s.contestant_id === contestant.id
        })
        return (
          <div
            key={outcome.slot}
            className="flex items-center gap-4 border-b border-zinc-100 py-3 last:border-0 dark:border-zinc-800"
          >
            <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-zinc-900 text-[11px] font-black tabular-nums text-amber-400 dark:bg-zinc-800">
              {outcome.slot}
            </span>
            <span className="min-w-0 flex-1">
              <span className="text-sm font-bold text-zinc-900 dark:text-zinc-50">
                {providerLabelOf(providerLabels, outcome.providerId, outcome.providerId)}
              </span>
              <span className="ml-2 font-mono text-[11px] text-zinc-400">
                {outcome.modelId}
              </span>
            </span>
            {/* Rate bar */}
            <span className="flex items-center gap-2">
              <span className="relative block h-2 w-16 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                <span
                  className={`absolute inset-0 h-full rounded-full transition-[width] duration-500 ease-out ${
                    rate >= 80 ? 'bg-emerald-500' : rate >= 40 ? 'bg-amber-400' : 'bg-red-500'
                  }`}
                  style={{ width: `${rate}%` }}
                />
              </span>
              <span className="min-w-12 text-right font-mono text-xs font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
                {passed}/{total}
              </span>
            </span>
            {sandbox ? (
              <span className="text-[11px] text-zinc-400">
                {sandbox.time_ms ?? 0}ms · {sandbox.isolation_level === 'wasm-worker' ? 'WASM' : sandbox.isolation_level}
              </span>
            ) : null}
            {sandbox?.degraded ? (
              <span className="text-[11px] text-amber-600 dark:text-amber-400">降级</span>
            ) : null}
          </div>
        )
      })}
      <p className="text-[11px] text-zinc-400">
        隔离级别：{new Set(snapshot.sandboxResults.map((r) => r.isolation_level)).size === 1
          ? (snapshot.sandboxResults[0]?.isolation_level === 'wasm-worker'
            ? 'WASM 语言级沙箱，非 OS 级隔离'
            : snapshot.sandboxResults[0]?.isolation_level ?? '未知')
          : '混合'}
      </p>
    </div>
  )
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
