import type { ArenaId } from '@/lib/arena/types'
import type { SandboxProgress } from '@/lib/arena/types'
import type { ContestantView } from '@/lib/client/battle-store'

/**
 * 转播台设计令牌：仅保留 Tailwind 无法静态表达的动态色值（hex 内联用），
 * 静态部分一律走 globals.css 里的 @theme 语义色。
 */

export interface ArenaSkin {
  id: ArenaId
  label: string
  tagline: string
  accent: string
  accentText: string
  accentEdge: string
  accentChip: string
}

export const ARENA_SKINS: Record<ArenaId, ArenaSkin> = {
  soft: {
    id: 'soft',
    label: '软战场',
    tagline: '辩论 / 创意 / 角色扮演 · 观众投票 + AI 裁判双轨判定',
    accent: '#8b8ff5',
    accentText: 'text-soft-arena',
    accentEdge: 'border-soft-arena/45',
    accentChip: 'border-soft-arena/30 bg-soft-arena/10 text-soft-arena',
  },
  'hard-code': {
    id: 'hard-code',
    label: '硬战场',
    tagline: 'Python 实战 · 沙箱真跑测试用例 · 按通过率判定',
    accent: '#38bdf8',
    accentText: 'text-hard-arena',
    accentEdge: 'border-hard-arena/45',
    accentChip: 'border-hard-arena/30 bg-hard-arena/10 text-hard-arena',
  },
}

export const ARENA_IDS: ArenaId[] = ['soft', 'hard-code']

/* ── 队色：provider 品牌色（暗底降噪版），承担选手身份识别 ── */

const PROVIDER_ACCENT: Record<string, string> = {
  ollama: '#2dd4bf',
  deepseek: '#5b7cfa',
  siliconflow: '#a78bfa',
  zhipu: '#38bdf8',
  moonshot: '#fb7185',
  dashscope: '#fb923c',
  openai: '#34d399',
  anthropic: '#f97316',
  google: '#60a5fa',
}

const FALLBACK_ACCENT = '#8b8b94'

export function providerAccent(providerId: string): string {
  return PROVIDER_ACCENT[providerId] ?? FALLBACK_ACCENT
}

/* ── 状态色调：live / success / timeout / failure 四档 ── */

export type StatusTone = 'live' | 'success' | 'timeout' | 'failure'

export const TONE_CLASSES: Record<
  StatusTone,
  { dot: string; text: string; chip: string; edge: string; bar: string; banner: string }
> = {
  live: {
    dot: 'bg-ok animate-live',
    text: 'text-ok',
    chip: 'border-ok/25 bg-ok/10 text-ok',
    edge: 'border-ok/35',
    bar: 'bg-ok',
    banner: 'border-ok/25 bg-ok/10 text-ok',
  },
  success: {
    dot: 'bg-ok',
    text: 'text-ok',
    chip: 'border-ok/25 bg-ok/10 text-ok',
    edge: 'border-ok/25',
    bar: 'bg-ok',
    banner: 'border-ok/25 bg-ok/10 text-ok',
  },
  timeout: {
    dot: 'bg-warn',
    text: 'text-warn',
    chip: 'border-warn/25 bg-warn/10 text-warn',
    edge: 'border-warn/35',
    bar: 'bg-warn',
    banner: 'border-warn/25 bg-warn/10 text-warn',
  },
  failure: {
    dot: 'bg-bad',
    text: 'text-bad',
    chip: 'border-bad/25 bg-bad/10 text-bad',
    edge: 'border-bad/35',
    bar: 'bg-bad',
    banner: 'border-bad/25 bg-bad/10 text-bad',
  },
}

export function statusTone(status: ContestantView['status']): StatusTone {
  if (status === 'streaming') return 'live'
  if (status === 'ok') return 'success'
  if (status === 'timeout') return 'timeout'
  return 'failure'
}

export const SANDBOX_TONE: Record<SandboxProgress['status'], StatusTone> = {
  ok: 'success',
  timeout: 'timeout',
  error: 'failure',
  unavailable: 'failure',
}

export const SANDBOX_LABELS: Record<SandboxProgress['status'], string> = {
  ok: '沙箱完成',
  timeout: '沙箱超时',
  error: '沙箱报错',
  unavailable: '未执行',
}

export function isFailedStatus(status: ContestantView['status']): boolean {
  return status !== 'streaming' && status !== 'ok'
}
