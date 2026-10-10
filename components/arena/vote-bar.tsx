'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { fontWeight, radius, spacing, typeScale, v } from './theme'

const VOTE_STORAGE_KEY = 'melee.voterKey'

interface VoteBarProps {
  battleId: string
  contestants: Array<{ slot: number; contestantId: string | null; providerId: string; modelId: string }>
  tally: Record<string, number>
  disabled: boolean
}

function getVoterKey(): string {
  try {
    const existing = window.localStorage.getItem(VOTE_STORAGE_KEY)
    if (existing) return existing
    const fresh = globalThis.crypto?.randomUUID?.() ?? `vk-${Date.now()}-${Math.random().toString(36).slice(2)}`
    window.localStorage.setItem(VOTE_STORAGE_KEY, fresh)
    return fresh
  } catch {
    return `vk-${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
}

function totalVotes(tally: Record<string, number>): number {
  return Object.values(tally).reduce((sum, count) => sum + Math.max(0, count), 0)
}

export function VoteBar({ battleId, contestants, tally, disabled }: VoteBarProps) {
  const voterKeyRef = useRef<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    voterKeyRef.current = getVoterKey()
  }, [])

  const handleVote = useCallback(
    async (contestantId: string | null) => {
      if (!contestantId || busy || disabled) return
      if (!voterKeyRef.current) voterKeyRef.current = getVoterKey()
      setBusy(contestantId)
      setNotice(null)
      try {
        const response = await fetch(`/api/battle/${battleId}/vote`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contestantId, voterKey: voterKeyRef.current }),
        })
        if (response.status === 409) {
          setNotice({ kind: 'error', text: '你已投过票了' })
          return
        }
        if (!response.ok) {
          const payload = (await response.json().catch(() => ({}))) as { error?: string }
          setNotice({ kind: 'error', text: payload.error ?? `投票失败：HTTP ${response.status}` })
          return
        }
        setNotice({ kind: 'ok', text: '投票成功' })
      } catch (error) {
        setNotice({ kind: 'error', text: error instanceof Error ? error.message : '投票失败' })
      } finally {
        setBusy(null)
      }
    },
    [battleId, busy, disabled],
  )

  const total = totalVotes(tally)

  if (contestants.length === 0) return null

  return (
    <section
      style={{
        padding: `${spacing.lg} ${spacing.md}`,
        borderTop: `1px solid ${v('border')}`,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          gap: spacing.sm,
          marginBottom: spacing.md,
        }}
      >
        <span style={{ fontSize: typeScale.label, fontWeight: fontWeight.black, letterSpacing: '0.14em', color: v('ink-faint') }}>
          观众票
        </span>
        <span style={{ fontFamily: 'var(--font-geist-mono, monospace)', fontSize: typeScale.title, fontWeight: fontWeight.black, color: v('ink') }}>
          {total}
        </span>
        <span style={{ fontSize: typeScale.micro, color: v('ink-muted') }}>票已投</span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: spacing.sm }}>
        {contestants.map((contestant) => {
          const count = tally[contestant.contestantId ?? ''] ?? 0
          const percent = total > 0 ? Math.round((count / total) * 100) : 0
          return (
            <div
              key={contestant.slot}
              style={{ display: 'flex', alignItems: 'center', gap: spacing.sm }}
            >
              <span
                style={{
                  flex: 1,
                  minWidth: 0,
                  display: 'flex',
                  alignItems: 'center',
                  gap: spacing.sm,
                }}
              >
                <span
                  title={`${contestant.slot} 号选手`}
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
                  {contestant.slot}
                </span>
                <span style={{ fontSize: typeScale.body, fontWeight: fontWeight.bold, color: v('ink') }}>
                  {contestant.providerId}
                </span>
                <span
                  style={{
                    fontFamily: 'var(--font-geist-mono, monospace)',
                    fontSize: typeScale.micro,
                    color: v('ink-faint'),
                  }}
                >
                  {contestant.modelId}
                </span>
              </span>
              <span
                role="img"
                aria-label={`${count} 票，占总票数 ${percent}%`}
                style={{
                  position: 'relative',
                  display: 'block',
                  width: 96,
                  height: 14,
                  borderRadius: radius.pill,
                  backgroundColor: v('surface-sunken'),
                  overflow: 'hidden',
                }}
              >
                <span
                  style={{
                    position: 'absolute',
                    inset: 0,
                    width: `${percent}%`,
                    backgroundColor: v('scoreboard-accent'),
                    borderRadius: radius.pill,
                    transition: 'width 320ms ease-out',
                  }}
                />
              </span>
              <span
                style={{
                  minWidth: 28,
                  textAlign: 'right',
                  fontFamily: 'var(--font-geist-mono, monospace)',
                  fontSize: typeScale.label,
                  fontWeight: fontWeight.bold,
                  color: v('ink'),
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {count}
              </span>
              <button
                type="button"
                onClick={() => handleVote(contestant.contestantId)}
                disabled={disabled || busy !== null || !contestant.contestantId}
                style={{
                  padding: `${spacing.xs} ${spacing.sm}`,
                  border: `1px solid ${v('border-strong')}`,
                  borderRadius: radius.md,
                  backgroundColor: 'transparent',
                  color: v('ink-muted'),
                  fontSize: typeScale.micro,
                  fontWeight: fontWeight.bold,
                  cursor: disabled || busy !== null || !contestant.contestantId ? 'not-allowed' : 'pointer',
                  opacity: disabled || busy !== null || !contestant.contestantId ? 0.5 : 1,
                }}
              >
                {busy === contestant.contestantId ? '投票中…' : '投票'}
              </button>
            </div>
          )
        })}
      </div>

      {notice ? (
        <p
          style={{
            marginTop: spacing.sm,
            fontSize: typeScale.micro,
            fontWeight: fontWeight.bold,
            color: notice.kind === 'ok' ? v('success') : v('failure'),
          }}
        >
          {notice.text}
        </p>
      ) : null}
    </section>
  )
}
