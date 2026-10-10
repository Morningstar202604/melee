'use client'

import type { ReactNode } from 'react'
import type { ArenaId } from '@/lib/arena/types'
import type { BattleResult, ContestantOutcome } from '@/lib/battle/events'
import type { Snapshot } from '@/lib/db/repo'
import { VoteBar } from './vote-bar'
import {
  fontWeight,
  layout,
  radius,
  spacing,
  typeScale,
  v,
} from './theme'

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
    const winner = ranked[0]
    const allTie = ranked.every((o) => verdictPassed(result, o.slot) === verdictPassed(result, ranked[0].slot))
    if (allFailed) return '全员翻车，本场无人生还'
    if (allTie && failCount === 0) return `全员并列通过 ${verdictPassed(result, winner.slot)} 题，势均力敌`
    const winnerLabel = providerLabelOf(providerLabels, winner.providerId, winner.providerId)
    return `${winnerLabel} 以 ${verdictPassed(result, winner.slot)} 题通关${noCrash ? '，本场无人翻车' : `，${failCount} 人翻车`}`
  }

  if (allFailed) return '全员翻车，本场无人生还'
  const votedOutcomes = outcomes.filter((o) => o.status === 'ok')
  if (votedOutcomes.length === 1) {
    const sole = providerLabelOf(providerLabels, votedOutcomes[0].providerId, votedOutcomes[0].providerId)
    return `${sole} 技压全场，${timeoutCount} 人超时`
  }
  if (noCrash) return '全场健在，一场龙争虎斗'
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

function judgeVerdictForSlot(result: BattleResult, slot: number) {
  return result.verdicts.find((v) => v.contestantSlot === slot)
}

function judgeScores(verdict: { scores: Record<string, number> } | undefined) {
  if (!verdict) return null
  return {
    logic: verdict.scores.logic ?? 0,
    evidence: verdict.scores.evidence ?? 0,
    relevance: verdict.scores.relevance ?? 0,
  }
}

function Banner({ tone, children }: { tone: 'degraded' | 'failure'; children: ReactNode }) {
  const colorToken = tone === 'degraded' ? 'degraded' : 'failure'
  const softToken = tone === 'degraded' ? 'degraded-soft' : 'failure-soft'
  return (
    <p
      style={{
        margin: 0,
        padding: `${spacing.sm} ${spacing.md}`,
        border: `1px solid ${v(colorToken)}`,
        borderRadius: radius.md,
        backgroundColor: v(softToken),
        color: v(colorToken),
        fontSize: typeScale.label,
        fontWeight: fontWeight.bold,
      }}
    >
      {children}
    </p>
  )
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
    <section
      style={{
        maxWidth: layout.pageMaxWidth,
        margin: '0 auto',
        padding: `0 ${spacing.md} ${spacing.xxl}`,
        display: 'flex',
        flexDirection: 'column',
        gap: spacing.lg,
      }}
    >
      <header>
        <p
          style={{
            margin: 0,
            fontSize: typeScale.micro,
            fontWeight: fontWeight.bold,
            letterSpacing: '0.16em',
            color: v('ink-faint'),
          }}
        >
          战报 · {arena === 'soft' ? '软战场' : '硬战场'}
        </p>
        <h2
          style={{
            margin: `${spacing.xs} 0 0`,
            fontSize: typeScale.scoreboard,
            fontWeight: fontWeight.black,
            lineHeight: 1.25,
            color: v('ink'),
          }}
        >
          {summary}
        </h2>
        <p
          style={{
            marginTop: spacing.sm,
            fontSize: typeScale.body,
            color: v('ink-muted'),
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {topic}
        </p>
      </header>

      {hasDegradedJudge ? (
        <Banner tone="degraded">裁判不可用，本场仅人类投票</Banner>
      ) : null}

      {failedContestants.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
          {failedContestants.map((o) => (
            <p
              key={o.slot}
              style={{
                margin: 0,
                fontSize: typeScale.body,
                color: v('failure'),
                fontWeight: fontWeight.bold,
              }}
            >
              · {providerLabelOf(providerLabels, o.providerId, o.providerId)}（{o.modelId}）{statusText(o.status)}
              {o.errorMessage ? ` — ${o.errorMessage}` : ''}
            </p>
          ))}
        </div>
      ) : null}

      <VoteBar
        battleId={result.battleId}
        contestants={contestsForVote}
        tally={tally}
        disabled={arena === 'hard-code'}
      />

      <div
        style={{
          display: 'grid',
          gap: spacing.lg,
          gridTemplateColumns: arena === 'soft' ? 'repeat(auto-fit, minmax(18rem, 1fr))' : '1fr',
        }}
      >
        {arena === 'soft' ? (
          <JudgePanel result={result} providerLabels={providerLabels} />
        ) : null}

        {arena === 'hard-code' ? (
          <HardcodePanel result={result} snapshot={snapshot} providerLabels={providerLabels} />
        ) : null}
      </div>

      {crashes.length > 0 ? (
        <div>
          <p
            style={{
              margin: 0,
              fontSize: typeScale.micro,
              fontWeight: fontWeight.black,
              letterSpacing: '0.14em',
              color: v('ink-faint'),
            }}
          >
            翻车榜
          </p>
          <ul style={{ margin: `${spacing.sm} 0 0`, paddingLeft: spacing.md }}>
            {crashes.map((crash, index) => (
              <li key={`${crash.kind}-${crash.slot}-${index}`} style={{ fontSize: typeScale.body, color: v('ink-muted') }}>
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
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: spacing.sm,
        padding: spacing.md,
        border: `1px solid ${v('border')}`,
        borderRadius: radius.lg,
        backgroundColor: v('surface'),
      }}
    >
      <p style={{ margin: 0, fontSize: typeScale.label, fontWeight: fontWeight.black, letterSpacing: '0.12em', color: v('ink-faint') }}>
        AI 裁判（双轮位置对调）
      </p>
      {result.verdicts.length === 0 ? (
        <p style={{ margin: 0, fontSize: typeScale.body, color: v('ink-muted') }}>本场无裁判打分</p>
      ) : (
        result.verdicts.map((verdict) => {
          const scores = judgeScores(verdict)
          const outcome = result.contestants.find((c) => c.slot === verdict.contestantSlot)
          const label = outcome ? providerLabelOf(providerLabels, outcome.providerId, outcome.providerId) : `${verdict.contestantSlot} 号`
          return (
            <div key={verdict.contestantSlot} style={{ display: 'flex', flexDirection: 'column', gap: spacing.xs }}>
              <span style={{ fontSize: typeScale.label, fontWeight: fontWeight.bold, color: v('ink') }}>
                {label}
              </span>
              {scores ? (
                <div style={{ display: 'flex', gap: spacing.md, fontSize: typeScale.micro, color: v('ink-muted') }}>
                  <span>逻辑 {scores.logic}</span>
                  <span>论据 {scores.evidence}</span>
                  <span>切题 {scores.relevance}</span>
                </div>
              ) : null}
              {verdict.rationale ? (
                <p style={{ margin: 0, fontSize: typeScale.body, color: v('ink-muted'), lineHeight: 1.6 }}>
                  {verdict.rationale}
                </p>
              ) : null}
            </div>
          )
        })
      )}
      <p style={{ margin: 0, fontSize: typeScale.micro, color: v('ink-faint') }}>
        {result.judgeAvailable ? 'AI 裁判与观众票数：见上栏分项，不合成总分' : '本场仅人类投票'}
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
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: spacing.sm,
        padding: spacing.md,
        border: `1px solid ${v('border')}`,
        borderRadius: radius.lg,
        backgroundColor: v('surface'),
      }}
    >
      <p style={{ margin: 0, fontSize: typeScale.label, fontWeight: fontWeight.black, letterSpacing: '0.12em', color: v('ink-faint') }}>
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
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: spacing.sm,
              padding: `${spacing.sm} 0`,
              borderBottom: `1px solid ${v('border')}`,
            }}
          >
            <span
              title={`${outcome.slot} 号选手`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 22,
                height: 22,
                borderRadius: radius.sm,
                backgroundColor: v('scoreboard'),
                color: v('scoreboard-accent'),
                fontSize: typeScale.micro,
                fontWeight: fontWeight.black,
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {outcome.slot}
            </span>
            <span style={{ fontSize: typeScale.body, fontWeight: fontWeight.bold, color: v('ink') }}>
              {providerLabelOf(providerLabels, outcome.providerId, outcome.providerId)}
            </span>
            <span
              style={{
                fontFamily: 'var(--font-geist-mono, monospace)',
                fontSize: typeScale.micro,
                color: v('ink-faint'),
              }}
            >
              {outcome.modelId}
            </span>
            <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: spacing.sm }}>
              <span
                role="img"
                aria-label={`通过率 ${rate}%`}
                style={{
                  position: 'relative',
                  display: 'block',
                  width: 56,
                  height: 6,
                  borderRadius: radius.pill,
                  backgroundColor: v('surface-sunken'),
                  overflow: 'hidden',
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    inset: 0,
                    width: `${rate}%`,
                    backgroundColor: rate >= 80 ? v('success') : rate >= 40 ? v('scoreboard-accent') : v('failure'),
                    borderRadius: radius.pill,
                  }}
                />
              </span>
              <span
                style={{
                  minWidth: 48,
                  textAlign: 'right',
                  fontFamily: 'var(--font-geist-mono, monospace)',
                  fontSize: typeScale.label,
                  fontWeight: fontWeight.bold,
                  color: v('ink'),
                }}
              >
                {passed}/{total}
              </span>
            </span>
            {sandbox ? (
              <span style={{ fontSize: typeScale.micro, color: v('ink-faint') }}>
                {sandbox.time_ms ?? 0}ms · {sandbox.isolation_level === 'wasm-worker' ? 'WASM' : sandbox.isolation_level}
              </span>
            ) : null}
            {sandbox?.degraded ? (
              <span style={{ fontSize: typeScale.micro, color: v('degraded') }}>降级未真实执行</span>
            ) : null}
          </div>
        )
      })}
      <p style={{ margin: 0, fontSize: typeScale.micro, color: v('ink-faint') }}>
        隔离级别：{new Set(snapshot.sandboxResults.map((r) => r.isolation_level)).size === 1
          ? (snapshot.sandboxResults[0]?.isolation_level === 'wasm-worker'
            ? 'WASM 语言级沙箱，非 OS 级隔离'
            : snapshot.sandboxResults[0]?.isolation_level ?? '未知')
          : '混合'}
      </p>
    </div>
  )
}

function statusText(status: string): string {
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
    case 'no_code':
      return '未产出可执行代码'
    default:
      return '执行出错'
  }
}
