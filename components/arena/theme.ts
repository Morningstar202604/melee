import type { ArenaId } from '@/lib/arena/types'
import type { ContestantView } from '@/lib/client/battle-store'

export const arenaPalette: Record<ArenaId, string> = {
  soft: '#6366f1',
  'hard-code': '#f59e0b',
}

const lightColors = {
  'page-bg': '#f4f4f5',
  surface: '#ffffff',
  'surface-alt': '#fafafa',
  'surface-sunken': '#e4e4e7',
  border: '#d4d4d8',
  'border-strong': '#a1a1aa',
  ink: '#18181b',
  'ink-muted': '#52525b',
  'ink-faint': '#71717a',
  live: '#0f766e',
  'live-soft': '#ccfbf1',
  success: '#047857',
  'success-soft': '#d1fae5',
  failure: '#b91c1c',
  'failure-soft': '#fee2e2',
  timeout: '#b45309',
  'timeout-soft': '#fef3c7',
  degraded: '#9a3412',
  'degraded-soft': '#ffedd5',
  first: '#854d0e',
  'first-soft': '#fef9c3',
  scoreboard: '#0c0c10',
  'scoreboard-line': '#27272a',
  'scoreboard-ink': '#fafafa',
  'scoreboard-muted': '#a1a1aa',
  'scoreboard-accent': '#fbbf24',
  'on-tone': '#ffffff',
}

const darkColors: Record<ColorToken, string> = {
  'page-bg': '#09090b',
  surface: '#18181b',
  'surface-alt': '#1f1f23',
  'surface-sunken': '#27272a',
  border: '#3f3f46',
  'border-strong': '#52525b',
  ink: '#fafafa',
  'ink-muted': '#a1a1aa',
  'ink-faint': '#71717a',
  live: '#2dd4bf',
  'live-soft': '#134e4a',
  success: '#34d399',
  'success-soft': '#064e3b',
  failure: '#f87171',
  'failure-soft': '#7f1d1d',
  timeout: '#fbbf24',
  'timeout-soft': '#78350f',
  degraded: '#fb923c',
  'degraded-soft': '#7c2d12',
  first: '#fde047',
  'first-soft': '#3f2d06',
  scoreboard: '#000000',
  'scoreboard-line': '#27272a',
  'scoreboard-ink': '#fafafa',
  'scoreboard-muted': '#a1a1aa',
  'scoreboard-accent': '#fbbf24',
  'on-tone': '#ffffff',
}

export type ColorToken = keyof typeof lightColors

export function v(token: ColorToken): string {
  return `var(--ar-${token})`
}

function cssVars(colors: Record<ColorToken, string>): string {
  return Object.entries(colors)
    .map(([token, value]) => `  --ar-${token}: ${value};`)
    .join('\n')
}

export const themeCss = `:root {
${cssVars(lightColors)}
}

@media (prefers-color-scheme: dark) {
:root {
${cssVars(darkColors)}
}
}
`

export const typeScale = {
  scoreboard: '2rem',
  phase: '1.375rem',
  title: '1.125rem',
  body: '0.875rem',
  label: '0.8125rem',
  micro: '0.6875rem',
} as const

export const fontWeight = {
  black: 900,
  bold: 700,
  medium: 500,
} as const

export const spacing = {
  xs: '0.25rem',
  sm: '0.5rem',
  md: '0.75rem',
  lg: '1rem',
  xl: '1.5rem',
  xxl: '2rem',
} as const

export const radius = {
  sm: '0.25rem',
  md: '0.375rem',
  lg: '0.625rem',
  pill: '999px',
} as const

export const metricBar = {
  height: 6,
  width: 72,
} as const

export const layout = {
  pageMaxWidth: '80rem',
  cardMinHeight: '19rem',
  badge: {
    size: 28,
  },
} as const

export const lineHeight = {
  body: 1.75,
} as const

export type StatusTone = 'live' | 'success' | 'failure' | 'timeout'

export const tonePair: Record<StatusTone, { base: ColorToken; soft: ColorToken }> = {
  live: { base: 'live', soft: 'live-soft' },
  success: { base: 'success', soft: 'success-soft' },
  failure: { base: 'failure', soft: 'failure-soft' },
  timeout: { base: 'timeout', soft: 'timeout-soft' },
}

export function statusTone(status: ContestantView['status']): StatusTone {
  if (status === 'streaming') return 'live'
  if (status === 'ok') return 'success'
  if (status === 'timeout') return 'timeout'
  return 'failure'
}
