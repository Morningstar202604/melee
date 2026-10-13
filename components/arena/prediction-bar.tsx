'use client'

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { CircleCheck, CircleX, HandCoins, Trophy } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { BattlePhase, BattleResult } from '@/lib/battle/events'
import type { Snapshot } from '@/lib/db/repo'
import type { ContestantView } from '@/lib/client/battle-store'
import {
  EMPTY_PREDICTION_STATS,
  PREDICTION_CHANGE_EVENT,
  isScored,
  loadMyPrediction,
  loadPredictionStats,
  markScored,
  notifyPredictionChange,
  recordOutcome,
  saveMyPrediction,
  winnerSlots,
} from '@/lib/client/prediction'
import { TONE_CLASSES, providerAccent } from './theme'

const VOTER_KEY = 'melee.voterKey'

export interface PredictionBarProps {
  battleId: string
  arena: string
  phase: BattlePhase
  views: ContestantView[]
  tally: Record<string, number>
  voteTally: Record<string, number>
  summary: BattleResult | null
  snapshot: Snapshot | null
  providerLabels: Record<string, string>
}

function getVoterKey(): string {
  try {
    const existing = window.localStorage.getItem(VOTER_KEY)
    if (existing) return existing
    const fresh = globalThis.crypto?.randomUUID?.() ?? `vk-${Date.now()}-${Math.random().toString(36).slice(2)}`
    window.localStorage.setItem(VOTER_KEY, fresh)
    return fresh
  } catch {
    return `vk-${Date.now()}-${Math.random().toString(36).slice(2)}`
  }
}

const TERMINAL: BattlePhase[] = ['completed', 'cancelled', 'failed']

export function PredictionBar({
  battleId,
  arena,
  phase,
  views,
  tally,
  voteTally,
  summary,
  snapshot,
  providerLabels,
}: PredictionBarProps) {
  const voterKeyRef = useRef<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string>('')
  const [overlay, setOverlay] = useState<{
    base: Record<string, number>
    delta: Record<string, number>
  }>(() => ({ base: {}, delta: {} }))

  const subscribe = useCallback((notify: () => void) => {
    window.addEventListener(PREDICTION_CHANGE_EVENT, notify)
    return () => window.removeEventListener(PREDICTION_CHANGE_EVENT, notify)
  }, [])

  const picked = useSyncExternalStore(
    subscribe,
    () => loadMyPrediction(battleId),
    () => null,
  )
  const stats = useSyncExternalStore(subscribe, loadPredictionStats, () => EMPTY_PREDICTION_STATS)

  const terminal = TERMINAL.includes(phase)
  const localDelta = overlay.base === tally ? overlay.delta : {}
  const effectiveTally: Record<string, number> = { ...tally }
  for (const [contestantId, delta] of Object.entries(localDelta)) {
    effectiveTally[contestantId] = (effectiveTally[contestantId] ?? 0) + delta
  }
  const total = Object.values(effectiveTally).reduce((sum, count) => sum + count, 0)

  const winners = useMemo(
    () =>
      terminal && phase === 'completed'
        ? winnerSlots(arena, summary, voteTally, snapshot)
        : [],
    [arena, phase, summary, voteTally, snapshot, terminal],
  )

  const pickedSlot = useMemo(() => {
    if (!picked || !snapshot) return null
    const row = snapshot.contestants.find((c) => c.id === picked)
    return row?.slot ?? null
  }, [picked, snapshot])

  const hit = pickedSlot !== null && winners.includes(pickedSlot)

  useEffect(() => {
    if (phase !== 'completed' || !picked || pickedSlot === null) return
    if (isScored(battleId)) return
    markScored(battleId)
    recordOutcome(hit)
    notifyPredictionChange()
  }, [phase, picked, pickedSlot, hit, battleId])

  const handlePick = useCallback(
    async (contestantId: string | null) => {
      if (!contestantId || busy || picked || terminal) return
      if (!voterKeyRef.current) voterKeyRef.current = getVoterKey()
      setBusy(contestantId)
      setNotice('')
      try {
        const response = await fetch(`/api/battle/${battleId}/predict`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ contestantId, voterKey: voterKeyRef.current }),
        })
        if (response.status === 409) {
          const payload = (await response.json().catch(() => ({}))) as { error?: string }
          setNotice(payload.error ?? '本场已经押过注了')
          return
        }
        if (!response.ok) {
          const payload = (await response.json().catch(() => ({}))) as { error?: string }
          setNotice(payload.error ?? `押注失败：HTTP ${response.status}`)
          return
        }
        saveMyPrediction(battleId, contestantId)
        notifyPredictionChange()
        setOverlay((current) => {
          const base = current.base === tally ? current.delta : {}
          return {
            base: tally,
            delta: { ...base, [contestantId]: (base[contestantId] ?? 0) + 1 },
          }
        })
      } catch (error) {
        setNotice(error instanceof Error ? error.message : '押注失败')
      } finally {
        setBusy(null)
      }
    },
    [battleId, busy, picked, tally, terminal],
  )

  const winnerLabel =
    winners.length === 0
      ? terminal && phase === 'completed'
        ? '本场无赢家'
        : ''
      : winners
          .map((slot) => {
            const view = views.find((item) => item.slot === slot)
            return `${slot} 号 ${view ? (providerLabels[view.providerId] ?? view.providerId) : ''}`.trim()
          })
          .join('、')

  return (
    <section className="rounded-xl border border-hairline bg-panel p-4 sm:p-5">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <HandCoins className="h-4 w-4 text-dust" />
        <h3 className="text-sm font-bold">赛前押注</h3>
        <span className="font-mono text-[11px] text-dust">{total > 0 ? `${total} 注在场` : '开盘'}</span>
        {pickedSlot !== null ? (
          <span className="rounded-full border border-blaze/30 bg-blaze/10 px-2.5 py-0.5 font-mono text-[11px] font-semibold text-blaze">
            你押了 {pickedSlot} 号
          </span>
        ) : null}
        {stats.total > 0 ? (
          <span className="ml-auto inline-flex items-center gap-1.5 font-mono text-[11px] text-dust">
            <Trophy className="h-3.5 w-3.5" />
            押注战绩 {stats.hits}/{stats.total}
          </span>
        ) : null}
      </header>

      {terminal ? (
        <div className="mt-3 flex flex-col gap-2">
          <p className="flex items-center gap-2 text-[13px] text-ink">
            {winnerLabel ? (
              <>
                <CircleCheck className={cn('h-4 w-4', winners.length > 0 ? 'text-ok' : 'text-dust')} />
                收盘结果：{winnerLabel}
              </>
            ) : (
              <span className="text-dust">本场没有产生判定依据，押注不计入战绩</span>
            )}
          </p>
          {picked && pickedSlot !== null && winners.length > 0 ? (
            <p
              className={cn(
                'inline-flex w-fit items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[12px] font-semibold',
                hit ? TONE_CLASSES.success.chip : TONE_CLASSES.failure.chip,
              )}
            >
              {hit ? <CircleCheck className="h-3.5 w-3.5" /> : <CircleX className="h-3.5 w-3.5" />}
              {hit ? '押中了' : '没押中'}
            </p>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 flex flex-col gap-1.5">
          {views.map((view) => {
            const contestantId = view.contestantId
            const count = effectiveTally[contestantId ?? ''] ?? 0
            const percent = total > 0 ? Math.round((count / total) * 100) : 0
            const accent = providerAccent(view.providerId)
            const isMine = picked === contestantId
            const disabled = !contestantId || Boolean(picked) || busy !== null
            return (
              <button
                key={view.slot}
                type="button"
                onClick={() => handlePick(contestantId)}
                disabled={disabled && !isMine}
                className={cn(
                  'group flex items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors',
                  isMine
                    ? 'border-blaze/50 bg-blaze/5'
                    : disabled
                      ? 'border-hairline bg-void opacity-60'
                      : 'border-hairline bg-void hover:border-blaze/40 hover:bg-raise/60',
                )}
              >
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded font-mono text-[10px] font-bold tabular-nums"
                  style={{ backgroundColor: `${accent}1f`, color: accent }}
                >
                  {view.slot}
                </span>
                <span className="truncate text-[13px] font-semibold">
                  {providerLabels[view.providerId] ?? view.providerId}
                </span>
                <span className="truncate font-mono text-[10px] text-dust">{view.modelId}</span>
                <span className="relative ml-auto block h-1.5 w-20 shrink-0 overflow-hidden rounded-full bg-raise">
                  <span
                    className="absolute inset-0 h-full rounded-full bg-dust/80 transition-[width] duration-500"
                    style={{ width: `${percent}%` }}
                  />
                </span>
                <span className="w-12 shrink-0 text-right font-mono text-[11px] tabular-nums text-dust">
                  {count > 0 ? `${count} 注` : busy === contestantId ? '押注中…' : '押它'}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {notice ? (
        <p className="mt-2.5 rounded-lg border border-warn/30 bg-warn/10 px-3 py-1.5 text-[12px] text-warn">
          {notice}
        </p>
      ) : null}
    </section>
  )
}
