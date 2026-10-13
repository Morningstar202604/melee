'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Vote } from 'lucide-react'
import { cn } from '@/lib/cn'
import { providerAccent } from './theme'

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
  const [votedFor, setVotedFor] = useState<string | null>(null)
  const [voteOverlay, setVoteOverlay] = useState<{ base: Record<string, number>; delta: Record<string, number> }>(
    () => ({ base: {}, delta: {} }),
  )
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    voterKeyRef.current = getVoterKey()
  }, [])

  const localDelta = voteOverlay.base === tally ? voteOverlay.delta : {}
  const effectiveTally: Record<string, number> = { ...tally }
  for (const [contestantId, delta] of Object.entries(localDelta)) {
    effectiveTally[contestantId] = (effectiveTally[contestantId] ?? 0) + delta
  }

  const handleVote = useCallback(
    async (contestantId: string | null) => {
      if (!contestantId || busy || disabled || votedFor) return
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
          setNotice({ kind: 'error', text: '你已经投过票了' })
          return
        }
        if (!response.ok) {
          const payload = (await response.json().catch(() => ({}))) as { error?: string }
          setNotice({ kind: 'error', text: payload.error ?? `投票失败：HTTP ${response.status}` })
          return
        }
        setVotedFor(contestantId)
        setVoteOverlay((current) => {
          const base = current.base === tally ? current.delta : {}
          return {
            base: tally,
            delta: { ...base, [contestantId]: (base[contestantId] ?? 0) + 1 },
          }
        })
        setNotice({ kind: 'ok', text: '投票成功' })
      } catch (error) {
        setNotice({ kind: 'error', text: error instanceof Error ? error.message : '投票失败' })
      } finally {
        setBusy(null)
      }
    },
    [battleId, busy, disabled, votedFor, tally],
  )

  const total = totalVotes(effectiveTally)
  if (contestants.length === 0) return null

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Vote className="h-4 w-4 self-center text-dust" />
        <h3 className="text-sm font-bold">观众投票</h3>
        <span className="font-mono text-2xl font-bold tabular-nums text-ink">{total}</span>
        <span className="text-xs text-dust">票已投</span>
        {disabled ? (
          <span className="text-xs text-dust">· 本场不开放投票</span>
        ) : votedFor ? (
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-ok/25 bg-ok/10 px-2.5 py-0.5 text-[11px] font-semibold text-ok">
            <Check className="h-3 w-3" />
            已投票
          </span>
        ) : null}
      </header>

      <div className="flex flex-col gap-2">
        {contestants.map((contestant) => {
          const count = effectiveTally[contestant.contestantId ?? ''] ?? 0
          const percent = total > 0 ? Math.round((count / total) * 100) : 0
          const isVotingThis = busy === contestant.contestantId
          const isVotedForThis = votedFor === contestant.contestantId
          const canVote = !disabled && !votedFor && !busy
          const accent = providerAccent(contestant.providerId)

          return (
            <button
              key={contestant.slot}
              type="button"
              onClick={() => handleVote(contestant.contestantId)}
              disabled={!canVote || !contestant.contestantId}
              className={cn(
                'group flex items-center gap-4 rounded-xl border px-4 py-3 text-left transition-colors',
                isVotedForThis
                  ? 'border-blaze/50 bg-blaze/5'
                  : canVote
                    ? 'border-hairline bg-panel hover:border-dust/60 hover:bg-raise/60'
                    : 'border-hairline bg-panel opacity-60',
              )}
            >
              <span
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md font-mono text-[11px] font-bold tabular-nums"
                style={{ backgroundColor: `${accent}1f`, color: accent }}
              >
                {contestant.slot}
              </span>

              <span className="flex min-w-0 flex-1 items-baseline gap-2">
                <span className="truncate text-sm font-semibold">{contestant.providerId}</span>
                <span className="truncate font-mono text-[11px] text-dust">{contestant.modelId}</span>
              </span>

              <span className="relative block h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-raise">
                <span
                  className={cn(
                    'absolute inset-0 h-full rounded-full transition-[width] duration-500 ease-out',
                    isVotedForThis ? 'bg-blaze' : 'bg-dust/80 group-hover:bg-dust',
                  )}
                  style={{ width: `${percent}%` }}
                />
              </span>

              <span className="w-10 shrink-0 text-right font-mono text-xs font-bold tabular-nums text-ink">
                {count}
              </span>

              <span className="w-14 shrink-0 text-right font-mono text-[11px] text-dust">
                {isVotingThis ? '投票中…' : isVotedForThis ? '你的选择' : total > 0 ? `${percent}%` : ''}
              </span>
            </button>
          )
        })}
      </div>

      {notice ? (
        <p
          role="status"
          className={cn(
            'rounded-lg border px-3.5 py-2 text-xs font-medium',
            notice.kind === 'ok'
              ? 'border-ok/25 bg-ok/10 text-ok'
              : 'border-bad/25 bg-bad/10 text-bad',
          )}
        >
          {notice.text}
        </p>
      ) : null}
    </section>
  )
}
