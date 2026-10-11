'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

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
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)

  useEffect(() => {
    voterKeyRef.current = getVoterKey()
  }, [])

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
        setNotice({ kind: 'ok', text: '投票成功！感谢参与 🎉' })
      } catch (error) {
        setNotice({ kind: 'error', text: error instanceof Error ? error.message : '投票失败' })
      } finally {
        setBusy(null)
      }
    },
    [battleId, busy, disabled, votedFor],
  )

  const total = totalVotes(tally)
  if (contestants.length === 0) return null

  return (
    <section className="border-t-2 border-zinc-200 pt-8 mt-8 dark:border-zinc-800">
      {/* Header */}
      <div className="flex items-baseline gap-3 mb-5">
        <span className="text-xs font-bold tracking-wider text-zinc-400 uppercase">
          观众投票
        </span>
        <span className="font-mono text-3xl font-black leading-none text-zinc-900 dark:text-zinc-50">
          {total}
        </span>
        <span className="text-sm text-zinc-500">票已投</span>
        {votedFor ? (
          <span className="ml-auto rounded-full bg-emerald-100 px-3 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
            ✓ 已投票
          </span>
        ) : null}
      </div>

      {/* Contestant vote rows */}
      <div className="flex flex-col gap-2">
        {contestants.map((contestant) => {
          const count = tally[contestant.contestantId ?? ''] ?? 0
          const percent = total > 0 ? Math.round((count / total) * 100) : 0
          const isVotingThis = busy === contestant.contestantId
          const isVotedForThis = votedFor === contestant.contestantId
          const canVote = !disabled && !votedFor && !busy

          return (
            <button
              key={contestant.slot}
              type="button"
              onClick={() => handleVote(contestant.contestantId)}
              disabled={!canVote || !contestant.contestantId}
              className={`flex items-center gap-4 rounded-xl border-2 px-5 py-4 text-left transition-all ${
                isVotedForThis
                  ? 'border-orange-500 bg-orange-50 dark:border-orange-400 dark:bg-orange-950/20'
                  : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700'
              } ${!canVote && !isVotedForThis ? 'opacity-60' : ''}`}
            >
              {/* Slot badge */}
              <span
                className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-black tabular-nums ${
                  isVotedForThis
                    ? 'bg-orange-500 text-white'
                    : 'bg-zinc-900 text-amber-400 dark:bg-zinc-800'
                }`}
              >
                {contestant.slot}
              </span>

              {/* Model info */}
              <span className="flex flex-col gap-0.5 min-w-0 flex-1">
                <span className="text-sm font-bold text-zinc-900 dark:text-zinc-50">
                  {contestant.providerId}
                </span>
                <span className="truncate font-mono text-[11px] text-zinc-400">
                  {contestant.modelId}
                </span>
              </span>

              {/* Progress bar */}
              <span className="relative block h-3 w-20 shrink-0 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800">
                <span
                  className={`absolute inset-0 h-full rounded-full transition-[width] duration-500 ease-out ${
                    isVotedForThis ? 'bg-orange-500' : 'bg-amber-400'
                  }`}
                  style={{ width: `${percent}%` }}
                />
              </span>

              {/* Vote count */}
              <span className="w-9 shrink-0 text-right font-mono text-xs font-bold tabular-nums text-zinc-900 dark:text-zinc-50">
                {count}
              </span>

              {/* Vote state */}
              <span className="w-16 shrink-0 text-center text-[11px] font-bold">
                {isVotingThis ? (
                  <span className="text-zinc-500">投票中…</span>
                ) : isVotedForThis ? (
                  <span className="text-orange-500 dark:text-orange-400">你的选择</span>
                ) : canVote ? (
                  <span className="text-zinc-400">点击投票</span>
                ) : null}
              </span>
            </button>
          )
        })}
      </div>

      {/* Notice */}
      {notice ? (
        <p
          className={`mt-3 rounded-lg px-4 py-2.5 text-xs font-medium ${
            notice.kind === 'ok'
              ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-400'
              : 'bg-red-50 text-red-700 dark:bg-red-950/30 dark:text-red-400'
          }`}
        >
          {notice.text}
        </p>
      ) : null}
    </section>
  )
}
