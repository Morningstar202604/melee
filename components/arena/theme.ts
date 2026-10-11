import type { ArenaId } from '@/lib/arena/types'
import type { ContestantView } from '@/lib/client/battle-store'

/**
 * 设计系统 — 仅保留 Tailwind 无法静态表达的动态色值。
 * 组件全部使用 Tailwind class，不再使用内联 CSS variable。
 */

export const arenaPalette: Record<ArenaId, string> = {
  soft: '#6366f1',
  'hard-code': '#f59e0b',
}

export type StatusTone = 'live' | 'success' | 'failure' | 'timeout'

export const toneClasses: Record<StatusTone, { text: string; bg: string; border: string; dot: string }> = {
  live: { text: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-950/30', border: 'border-emerald-200 dark:border-emerald-800', dot: 'bg-emerald-500' },
  success: { text: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-50 dark:bg-emerald-950/30', border: 'border-emerald-200 dark:border-emerald-800', dot: 'bg-emerald-500' },
  failure: { text: 'text-red-600 dark:text-red-400', bg: 'bg-red-50 dark:bg-red-950/30', border: 'border-red-200 dark:border-red-800', dot: 'bg-red-500' },
  timeout: { text: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-50 dark:bg-amber-950/30', border: 'border-amber-200 dark:border-amber-800', dot: 'bg-amber-500' },
}

export function statusTone(status: ContestantView['status']): StatusTone {
  if (status === 'streaming') return 'live'
  if (status === 'ok') return 'success'
  if (status === 'timeout') return 'timeout'
  return 'failure'
}
