import { ARENA_IDS as ARENA_ID_LIST, type ArenaId } from '@/lib/arena/types'
import { BANTER_AXES, DEBATE_AXES, SOFT_AXES } from '@/lib/arena/axes'
import type { JudgeAxis, SandboxProgress } from '@/lib/arena/types'
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
  minContestants: number
  maxContestants: number
  multiRound: boolean
}

export const ARENA_SKINS: Record<ArenaId, ArenaSkin> = {
  soft: {
    id: 'soft',
    label: '软战场',
    tagline: '同题抢答 · 辩论 / 创意 / 角色扮演 · 观众投票 + AI 裁判双轨判定',
    accent: '#8b8ff5',
    accentText: 'text-soft-arena',
    accentEdge: 'border-soft-arena/45',
    accentChip: 'border-soft-arena/30 bg-soft-arena/10 text-soft-arena',
    minContestants: 2,
    maxContestants: 8,
    multiRound: false,
  },
  debate: {
    id: 'debate',
    label: '互辩场',
    tagline: '两名选手三轮制辩论：立论 → 驳斥 → 结辩 · 裁判判交锋',
    accent: '#67e8f9',
    accentText: 'text-arena-debate',
    accentEdge: 'border-arena-debate/45',
    accentChip: 'border-arena-debate/30 bg-arena-debate/10 text-arena-debate',
    minContestants: 2,
    maxContestants: 2,
    multiRound: true,
  },
  banter: {
    id: 'banter',
    label: '互怼大会',
    tagline: '2~4 人三轮互怼 · 接梗反击 · 毒舌不许越线',
    accent: '#fb7185',
    accentText: 'text-arena-banter',
    accentEdge: 'border-arena-banter/45',
    accentChip: 'border-arena-banter/30 bg-arena-banter/10 text-arena-banter',
    minContestants: 2,
    maxContestants: 4,
    multiRound: true,
  },
  'hard-code': {
    id: 'hard-code',
    label: '硬战场',
    tagline: 'Python 实战 · 沙箱真跑测试用例 · 按通过率判定',
    accent: '#38bdf8',
    accentText: 'text-hard-arena',
    accentEdge: 'border-hard-arena/45',
    accentChip: 'border-hard-arena/30 bg-hard-arena/10 text-hard-arena',
    minContestants: 2,
    maxContestants: 8,
    multiRound: false,
  },
  chess: {
    id: 'chess',
    label: '残局场',
    tagline: '国际象棋残局 · 白先 N 步杀 · 着法真校验',
    accent: '#e0a458',
    accentText: 'text-arena-chess',
    accentEdge: 'border-arena-chess/45',
    accentChip: 'border-arena-chess/30 bg-arena-chess/10 text-arena-chess',
    minContestants: 2,
    maxContestants: 8,
    multiRound: false,
  },
}

export const ARENA_IDS: ArenaId[] = [...ARENA_ID_LIST]

/** 战报记分板的评分轴（键与种子/裁判输出一致） */
export const ARENA_AXES: Partial<Record<ArenaId, JudgeAxis[]>> = {
  soft: SOFT_AXES,
  debate: DEBATE_AXES,
  banter: BANTER_AXES,
}

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
