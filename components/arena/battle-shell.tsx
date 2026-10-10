'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { closesStream } from '@/lib/battle/registry'
import { parseBattleEvent } from '@/lib/battle/events'
import type { ArenaId } from '@/lib/arena/types'
import type { Snapshot } from '@/lib/db/repo'
import {
  dataFromSnapshot,
  isTerminalPhase,
  sortedContestants,
  useBattleStore,
} from '@/lib/client/battle-store'
import { ContestantCard } from './contestant-card'
import { StatusBar } from './status-bar'
import { Settlement } from './settlement'
import { arenaPalette, fontWeight, layout, radius, spacing, themeCss, typeScale, v } from './theme'

const MAX_RECONNECT_FAILURES = 3
const RECONNECT_DELAY_MS = 2000

export interface BattleShellProps {
  battleId: string
  topic: string
  arena: ArenaId
  initialSnapshot: Snapshot
  providerLabels: Record<string, string>
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function BattleShell({
  battleId,
  topic,
  arena,
  initialSnapshot,
  providerLabels,
}: BattleShellProps) {
  const apply = useBattleStore((state) => state.apply)
  const hydrate = useBattleStore((state) => state.hydrate)
  const hydratedBattleId = useBattleStore((state) => state.hydratedBattleId)
  const phase = useBattleStore((state) => state.phase)
  const startedAt = useBattleStore((state) => state.startedAt)
  const contestants = useBattleStore((state) => state.contestants)
  const summary = useBattleStore((state) => state.summary)
  const snapshot = useBattleStore((state) => state.snapshot)
  const failureReason = useBattleStore((state) => state.failureReason)

  const seed = useMemo(() => dataFromSnapshot(initialSnapshot), [initialSnapshot])
  const live = hydratedBattleId === battleId

  const [connectionLost, setConnectionLost] = useState(false)
  const [cancelNotice, setCancelNotice] = useState('')
  const [cancelling, setCancelling] = useState(false)

  useEffect(() => {
    hydrate(initialSnapshot)
  }, [hydrate, initialSnapshot])

  const views = useMemo(
    () => (live ? sortedContestants(contestants) : sortedContestants(seed.contestants)),
    [live, contestants, seed],
  )
  const shownPhase = live ? phase : seed.phase
  const shownStartedAt = live ? startedAt : seed.startedAt
  const shownFailureReason = live ? failureReason : seed.failureReason
  const settled = isTerminalPhase(shownPhase)
  const maxLatencyMs = views.reduce((max, view) => Math.max(max, view.latencyMs ?? 0), 0)

  useEffect(() => {
    if (isTerminalPhase(seed.phase)) return

    let disposed = false
    let source: EventSource | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    let failures = 0

    const teardown = () => {
      if (retryTimer !== null) {
        clearTimeout(retryTimer)
        retryTimer = null
      }
      if (source) {
        source.close()
        source = null
      }
    }

    const recoverFromSnapshot = async () => {
      try {
        const response = await fetch(`/api/battle/${battleId}/snapshot`)
        if (!response.ok) return
        const fresh = (await response.json()) as Snapshot
        if (!isTerminalPhase(fresh.battle.phase)) return
        hydrate(fresh)
      } catch {
        setConnectionLost(true)
      }
    }

    const connect = () => {
      if (disposed) return
      source = new EventSource(`/api/battle/${battleId}/stream`)
      source.onopen = () => {
        failures = 0
      }
      source.onmessage = (message) => {
        let event
        try {
          event = parseBattleEvent(JSON.parse(message.data))
        } catch {
          return
        }
        apply(event)
        if (closesStream(event)) teardown()
      }
      source.onerror = () => {
        if (disposed) return
        const current = useBattleStore.getState()
        const currentPhase =
          current.hydratedBattleId === battleId ? current.phase : seed.phase
        failures += 1
        teardown()
        if (isTerminalPhase(currentPhase)) return
        if (failures >= MAX_RECONNECT_FAILURES) {
          setConnectionLost(true)
          void recoverFromSnapshot()
          return
        }
        retryTimer = setTimeout(connect, RECONNECT_DELAY_MS)
      }
    }

    connect()
    return () => {
      disposed = true
      teardown()
    }
  }, [apply, battleId, hydrate, seed.phase])

  const handleCancel = async () => {
    if (cancelling) return
    setCancelling(true)
    setCancelNotice('')
    try {
      const response = await fetch(`/api/battle/${battleId}/cancel`, { method: 'POST' })
      if (response.ok) {
        setCancelNotice('已通知取消，本场即将收尾')
        return
      }
      const payload = (await response.json().catch(() => ({}))) as { error?: string }
      setCancelNotice(payload.error ?? `取消失败：HTTP ${response.status}`)
    } catch (error) {
      setCancelNotice(`取消失败：${errorText(error)}`)
    } finally {
      setCancelling(false)
    }
  }

  const activeSnapshot = live ? snapshot : seed.snapshot

  return (
    <div
      style={{
        minHeight: '100%',
        backgroundColor: v('page-bg'),
        color: v('ink'),
      }}
    >
      <style dangerouslySetInnerHTML={{ __html: themeCss }} />

      <header
        style={{
          borderBottom: `2px solid ${v('border-strong')}`,
          backgroundColor: v('surface'),
        }}
      >
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: spacing.md,
            maxWidth: layout.pageMaxWidth,
            margin: '0 auto',
            padding: `${spacing.md} ${spacing.md} 0`,
          }}
        >
          <Link
            href="/"
            style={{
              fontSize: typeScale.title,
              fontWeight: fontWeight.black,
              color: v('ink'),
              textDecoration: 'none',
            }}
          >
            melee · 观战
          </Link>
          <span
            style={{
              padding: `1px ${spacing.sm}`,
              borderRadius: radius.sm,
              backgroundColor: v('surface-alt'),
              color: v('ink-faint'),
              fontFamily: 'var(--font-geist-mono, monospace)',
              fontSize: typeScale.micro,
            }}
          >
            {battleId}
          </span>
          <div
            style={{
              marginLeft: 'auto',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              gap: spacing.md,
            }}
          >
            {cancelNotice ? (
              <span style={{ fontSize: typeScale.micro, fontWeight: fontWeight.bold, color: v('ink-muted') }}>
                {cancelNotice}
              </span>
            ) : null}
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelling || settled}
              style={{
                padding: `${spacing.sm} ${spacing.md}`,
                border: `1px solid ${v('border-strong')}`,
                borderRadius: radius.md,
                backgroundColor: 'transparent',
                color: v('ink'),
                fontSize: typeScale.label,
                fontWeight: fontWeight.bold,
                cursor: cancelling || settled ? 'not-allowed' : 'pointer',
                opacity: cancelling || settled ? 0.4 : 1,
              }}
            >
              {cancelling ? '取消中…' : '取消对战'}
            </button>
          </div>
        </div>
        <div style={{ maxWidth: layout.pageMaxWidth, margin: '0 auto', padding: `${spacing.sm} ${spacing.md} ${spacing.md}` }}>
          <p
            style={{
              margin: 0,
              fontSize: typeScale.micro,
              fontWeight: fontWeight.black,
              letterSpacing: '0.14em',
              color: v('ink-faint'),
            }}
          >
            本场题目
          </p>
          <p
            style={{
              margin: `${spacing.xs} 0 0`,
              padding: `${spacing.sm} ${spacing.md}`,
              borderLeft: `4px solid ${arenaPalette[arena]}`,
              backgroundColor: v('surface-alt'),
              fontSize: typeScale.body,
              lineHeight: 1.7,
              color: v('ink-muted'),
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {topic}
          </p>
        </div>
      </header>

      <StatusBar
        phase={shownPhase}
        startedAt={shownStartedAt}
        contestants={views}
        failureReason={shownFailureReason}
      />

      {connectionLost ? (
        <p
          style={{
            maxWidth: layout.pageMaxWidth,
            margin: `${spacing.md} auto 0`,
            padding: `${spacing.sm} ${spacing.md}`,
            border: `1px solid ${v('failure')}`,
            borderRadius: radius.md,
            backgroundColor: v('failure-soft'),
            color: v('failure'),
            fontSize: typeScale.label,
            fontWeight: fontWeight.bold,
          }}
        >
          连接中断，请刷新
        </p>
      ) : null}

      <main
        style={{
          display: 'grid',
          gap: spacing.md,
          gridTemplateColumns: 'repeat(auto-fill, minmax(20rem, 1fr))',
          maxWidth: layout.pageMaxWidth,
          margin: '0 auto',
          padding: `${spacing.lg} ${spacing.md}`,
        }}
      >
        {views.map((view) => (
          <ContestantCard
            key={view.slot}
            view={view}
            providerLabel={providerLabels[view.providerId]}
            maxLatencyMs={maxLatencyMs}
          />
        ))}
      </main>

      {summary && activeSnapshot ? (
        <Settlement
          arena={arena}
          topic={topic}
          result={summary}
          snapshot={activeSnapshot}
          providerLabels={providerLabels}
        />
      ) : null}
    </div>
  )
}
