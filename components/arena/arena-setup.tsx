'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Check,
  ChevronLeft,
  Crown,
  Flame,
  MessagesSquare,
  Scale,
  Search,
  ShieldCheck,
  Swords,
  Terminal,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/cn'
import type { ArenaId } from '@/lib/arena/types'
import type {
  BanterTopic,
  CodeTopic,
  DebateTopic,
  SoftTopic,
} from '@/lib/arena/topics'
import { banterTopic } from '@/lib/arena/topics'
import { chessTopic, type ChessPuzzle } from '@/lib/arena/chess'
import type { ProviderDef } from '@/lib/providers/registry'
import { ARENA_IDS, ARENA_SKINS } from './theme'
import { ChessBoard } from './chess-board'
import { LineupEditor } from './lineup-editor'

export interface ArenaSetupProps {
  providers: ProviderDef[]
  softTopics: SoftTopic[]
  debateTopics: DebateTopic[]
  banterTopics: BanterTopic[]
  codeTopics: CodeTopic[]
  chessPuzzles: ChessPuzzle[]
}

const LINEUP_STORAGE_KEY = 'melee.lineup'
const KEY_STORAGE_KEY = 'melee.keys'

interface ContestantRow {
  rowId: string
  providerId: string
  modelId: string
  apiKey: string
}

interface PingOutcome {
  ok: boolean
  status: string
  message: string
}

interface ModelNotice {
  kind: 'info' | 'error'
  text: string
}

const ARENA_ICONS: Record<ArenaId, LucideIcon> = {
  soft: MessagesSquare,
  debate: Scale,
  banter: Flame,
  'hard-code': Terminal,
  chess: Crown,
}

const STEPS = [
  { id: 1, label: '选赛场', hint: '五种玩法，判定方式不同' },
  { id: 2, label: '定题目', hint: '同一道题，所有选手' },
  { id: 3, label: '点大将', hint: '模型 + API Key' },
  { id: 4, label: '开打', hint: '确认阵容，发起对战' },
]

const BTN_PRIMARY =
  'inline-flex items-center gap-2 rounded-lg bg-blaze px-5 py-2.5 text-sm font-bold text-void transition-colors hover:bg-blaze/90 disabled:cursor-not-allowed disabled:bg-hairline disabled:text-dust'
const BTN_GHOST =
  'inline-flex items-center gap-2 rounded-lg border border-hairline px-4 py-2.5 text-sm font-semibold text-ash transition-colors hover:border-dust hover:text-ink disabled:cursor-not-allowed disabled:opacity-40'

let ROW_SEQ = 0

function makeRowId() {
  ROW_SEQ += 1
  return `r${ROW_SEQ.toString(36)}`
}

function blankRow(providerId: string): ContestantRow {
  return {
    rowId: makeRowId(),
    providerId,
    modelId: '',
    apiKey: providerId === 'ollama' ? 'ollama-local' : '',
  }
}

interface RestoredLineup {
  arena: ArenaId
  topicByArena: Record<string, string>
  rows: ContestantRow[]
}

function readLineup(providers: ProviderDef[]): RestoredLineup {
  const fallback: RestoredLineup = {
    arena: 'soft',
    topicByArena: {},
    rows: [blankRow('ollama'), blankRow('deepseek')],
  }
  try {
    const raw = window.localStorage.getItem(LINEUP_STORAGE_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const known = new Set(providers.map((p) => p.id))
    const storedKeys = readKeys()
    const storedRows = Array.isArray(parsed.rows) ? parsed.rows : []
    const rows = storedRows
      .map((entry) => {
        const item = (entry ?? {}) as Record<string, unknown>
        const providerId = typeof item.providerId === 'string' ? item.providerId : ''
        if (!known.has(providerId)) return null
        const rowId = typeof item.rowId === 'string' && item.rowId ? item.rowId : makeRowId()
        const modelId = typeof item.modelId === 'string' ? item.modelId : ''
        return { rowId, providerId, modelId, apiKey: storedKeys[rowId] ?? '' }
      })
      .filter((row): row is ContestantRow => row !== null)
    if (rows.length < 2) return fallback

    const topicByArena: Record<string, string> = {}
    const storedTopics = parsed.topicByArena
    if (storedTopics && typeof storedTopics === 'object') {
      for (const [key, value] of Object.entries(storedTopics as Record<string, unknown>)) {
        if (typeof value === 'string') topicByArena[key] = value
      }
    }
    if (typeof parsed.softTopicId === 'string' && parsed.softTopicId) {
      topicByArena.soft = parsed.softTopicId
    }
    if (typeof parsed.codeTopicId === 'string' && parsed.codeTopicId) {
      topicByArena['hard-code'] = parsed.codeTopicId
    }

    const arena = ARENA_IDS.includes(parsed.arena as ArenaId)
      ? (parsed.arena as ArenaId)
      : fallback.arena
    return { arena, topicByArena, rows }
  } catch {
    return fallback
  }
}

function readKeys(): Record<string, string> {
  try {
    const raw = window.sessionStorage.getItem(KEY_STORAGE_KEY)
    const parsed = raw ? (JSON.parse(raw) as Record<string, unknown>) : {}
    const keys: Record<string, string> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string' && value) keys[key] = value
    }
    return keys
  } catch {
    return {}
  }
}

function errorText(error: unknown): string {
  if (error instanceof Error) {
    const redacted = error.message
      .replace(/https?:\/\/[^\s]+/gi, '[URL]')
      .replace(/[A-Za-z0-9_-]{20,}/gi, (m) => (m.length > 32 ? '[REDACTED]' : m))
    return redacted.length > 200 ? `${redacted.slice(0, 200)}…` : redacted
  }
  return '未知错误'
}

export function ArenaSetup({
  providers,
  softTopics,
  debateTopics,
  banterTopics,
  codeTopics,
  chessPuzzles,
}: ArenaSetupProps) {
  const router = useRouter()
  const providerById = useMemo(() => new Map(providers.map((p) => [p.id, p])), [providers])

  const [step, setStep] = useState(1)
  const [arena, setArena] = useState<ArenaId>('soft')
  const [topicByArena, setTopicByArena] = useState<Record<string, string>>({})
  const [rows, setRows] = useState<ContestantRow[]>([blankRow('ollama'), blankRow('deepseek')])
  const [ping, setPing] = useState<Record<string, PingOutcome>>({})
  const [modelLists, setModelLists] = useState<Record<string, string[]>>({})
  const [modelNotices, setModelNotices] = useState<Record<string, ModelNotice>>({})
  const [fetchingRow, setFetchingRow] = useState<string>('')
  const [busy, setBusy] = useState<'' | 'ping' | 'fight'>('')
  const [pageError, setPageError] = useState('')
  const [skippedNotice, setSkippedNotice] = useState('')
  const [hydrated, setHydrated] = useState(false)
  const hydratedRef = useRef(false)

  const skin = ARENA_SKINS[arena]

  const defaultTopicId = useCallback(
    (id: ArenaId): string => {
      if (id === 'soft') return softTopics[0]?.id ?? ''
      if (id === 'debate') return debateTopics[0]?.id ?? ''
      if (id === 'banter') return banterTopics[0]?.id ?? ''
      if (id === 'hard-code') return codeTopics[0]?.id ?? ''
      return chessPuzzles[0]?.id ?? ''
    },
    [banterTopics, chessPuzzles, codeTopics, debateTopics, softTopics],
  )

  useEffect(() => {
    if (hydratedRef.current) return
    hydratedRef.current = true
    const restored = readLineup(providers)
    setArena(restored.arena)
    setRows(restored.rows)
    setTopicByArena(restored.topicByArena)
    setHydrated(true)
  }, [providers])

  const topicId = topicByArena[arena] || defaultTopicId(arena)

  const topicOptions = useMemo((): Array<{ id: string; label: string }> => {
    if (arena === 'soft') {
      return softTopics.map((topic) => ({ id: topic.id, label: `${topic.title}（${topic.kind}）` }))
    }
    if (arena === 'debate') {
      return debateTopics.map((topic) => ({ id: topic.id, label: topic.title }))
    }
    if (arena === 'banter') {
      return banterTopics.map((topic) => ({ id: topic.id, label: topic.title }))
    }
    if (arena === 'hard-code') {
      return codeTopics.map((topic) => ({
        id: topic.id,
        label: `${topic.title}（${topic.tests.length} 个用例）`,
      }))
    }
    return chessPuzzles.map((puzzle) => ({
      id: puzzle.id,
      label: `${puzzle.title}（白先 ${puzzle.mateIn} 步杀）`,
    }))
  }, [arena, banterTopics, chessPuzzles, codeTopics, debateTopics, softTopics])

  const activePuzzle = useMemo(
    () => (arena === 'chess' ? chessPuzzles.find((p) => p.id === topicId) ?? chessPuzzles[0] : undefined),
    [arena, chessPuzzles, topicId],
  )

  const topicPrompt = useMemo((): string => {
    if (arena === 'soft') {
      return softTopics.find((t) => t.id === topicId)?.prompt ?? ''
    }
    if (arena === 'debate') {
      return debateTopics.find((t) => t.id === topicId)?.motion ?? ''
    }
    if (arena === 'banter') {
      const found = banterTopics.find((t) => t.id === topicId)
      return found ? banterTopic(found) : ''
    }
    if (arena === 'hard-code') {
      return codeTopics.find((t) => t.id === topicId)?.prompt ?? ''
    }
    return activePuzzle ? chessTopic(activePuzzle) : ''
  }, [activePuzzle, arena, banterTopics, codeTopics, debateTopics, softTopics, topicId])

  const step1Valid = !!arena
  const step2Valid = !!topicPrompt
  const greenCount = rows.filter((row) => ping[row.rowId]?.ok === true).length
  const step3Valid = greenCount >= skin.minContestants && rows.length <= skin.maxContestants

  const persist = useCallback(() => {
    try {
      window.localStorage.setItem(
        LINEUP_STORAGE_KEY,
        JSON.stringify({
          arena,
          topicByArena,
          rows: rows.map((row) => ({
            rowId: row.rowId,
            providerId: row.providerId,
            modelId: row.modelId,
          })),
        }),
      )
      const keys: Record<string, string> = {}
      for (const row of rows) {
        if (row.apiKey) keys[row.rowId] = row.apiKey
      }
      window.sessionStorage.setItem(KEY_STORAGE_KEY, JSON.stringify(keys))
    } catch {
      // ignore
    }
  }, [arena, rows, topicByArena])

  useEffect(() => {
    if (hydrated) persist()
  }, [hydrated, persist])

  const selectArena = useCallback(
    (next: ArenaId) => {
      setArena(next)
      setPageError('')
      const max = ARENA_SKINS[next].maxContestants
      setRows((current) => (current.length > max ? current.slice(0, max) : current))
    },
    [],
  )

  const updateRow = useCallback((rowId: string, patch: Partial<ContestantRow>) => {
    setRows((current) => current.map((row) => (row.rowId === rowId ? { ...row, ...patch } : row)))
  }, [])

  const addRow = useCallback(() => {
    setRows((current) => {
      if (current.length >= skin.maxContestants) return current
      return [...current, blankRow(current[current.length - 1]?.providerId ?? 'ollama')]
    })
  }, [skin.maxContestants])

  const removeRow = useCallback(
    (rowId: string) => {
      setRows((current) =>
        current.length > skin.minContestants ? current.filter((row) => row.rowId !== rowId) : current,
      )
      setPing((current) => {
        const next = { ...current }
        delete next[rowId]
        return next
      })
      setModelLists((current) => {
        const next = { ...current }
        delete next[rowId]
        return next
      })
      setModelNotices((current) => {
        const next = { ...current }
        delete next[rowId]
        return next
      })
    },
    [skin.minContestants],
  )

  const clearRowCache = useCallback((rowId: string) => {
    setPing((current) => {
      const next = { ...current }
      delete next[rowId]
      return next
    })
    setModelLists((current) => {
      const next = { ...current }
      delete next[rowId]
      return next
    })
    setModelNotices((current) => {
      const next = { ...current }
      delete next[rowId]
      return next
    })
  }, [])

  const pingContestants = useCallback(
    async (targets: ContestantRow[]): Promise<Record<string, PingOutcome>> => {
      const response = await fetch('/api/providers/ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contestants: targets.map((row, index) => ({
            slot: index + 1,
            providerId: row.providerId,
            modelId: row.modelId,
            apiKey: row.apiKey,
          })),
        }),
      })
      if (!response.ok) {
        throw new Error(`预检接口返回 HTTP ${response.status}`)
      }
      const payload = (await response.json()) as {
        results?: Array<{ slot?: number; ok?: boolean; status?: string; message?: string }>
      }
      const next: Record<string, PingOutcome> = {}
      for (const result of payload.results ?? []) {
        const slot = typeof result.slot === 'number' ? result.slot : 0
        const row = targets[slot - 1]
        if (!row) continue
        next[row.rowId] = {
          ok: result.ok === true,
          status: typeof result.status === 'string' ? result.status : 'unknown',
          message: typeof result.message === 'string' ? result.message : '无原因',
        }
      }
      return next
    },
    [],
  )

  const handlePing = useCallback(async () => {
    setBusy('ping')
    setPageError('')
    setSkippedNotice('')
    try {
      setPing(await pingContestants(rows))
    } catch (error) {
      setPageError(`预检失败：${errorText(error)}`)
    } finally {
      setBusy('')
    }
  }, [pingContestants, rows])

  const handleFetchModels = useCallback(
    async (rowId: string) => {
      const row = rows.find((item) => item.rowId === rowId)
      if (!row) return
      const provider = providerById.get(row.providerId)
      if (!provider) return
      setFetchingRow(rowId)
      setModelNotices((current) => ({ ...current, [rowId]: { kind: 'info', text: '拉取中…' } }))
      try {
        const response = await fetch('/api/providers/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ providerId: provider.id, apiKey: row.apiKey }),
        })
        const payload = (await response.json()) as {
          ok?: boolean
          models?: string[]
          reason?: string
        }
        if (payload.ok && Array.isArray(payload.models)) {
          setModelLists((current) => ({ ...current, [rowId]: payload.models as string[] }))
          setModelNotices((current) => ({
            ...current,
            [rowId]: { kind: 'info', text: `拉到 ${(payload.models as string[]).length} 个模型` },
          }))
        } else {
          setModelLists((current) => {
            const next = { ...current }
            delete next[rowId]
            return next
          })
          setModelNotices((current) => ({
            ...current,
            [rowId]: {
              kind: 'error',
              text: payload.reason ?? `${provider.label} 未返回模型列表`,
            },
          }))
        }
      } catch (error) {
        setModelNotices((current) => ({
          ...current,
          [rowId]: { kind: 'error', text: `${provider.label} 不可达：${errorText(error)}` },
        }))
      } finally {
        setFetchingRow('')
      }
    },
    [providerById, rows],
  )

  const handleFight = useCallback(async () => {
    setPageError('')
    setSkippedNotice('')
    persist()

    let currentPing: Record<string, PingOutcome> = {}
    try {
      setBusy('ping')
      currentPing = await pingContestants(rows)
      setPing(currentPing)
    } catch (error) {
      setBusy('')
      setPageError(`预检失败：${errorText(error)}`)
      return
    }

    const eligible = rows.filter((row) => currentPing[row.rowId]?.ok === true)
    const skipped = rows.filter((row) => currentPing[row.rowId]?.ok !== true)
    if (eligible.length < skin.minContestants) {
      setBusy('')
      setPageError(
        `预检通过的选手只有 ${eligible.length} 名，本赛场至少需要 ${skin.minContestants} 名才能开打`,
      )
      return
    }
    if (!topicPrompt) {
      setBusy('')
      setPageError('请先选择一个题目')
      return
    }

    setBusy('fight')
    try {
      const response = await fetch('/api/battle/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          arena,
          topic: topicPrompt,
          contestants: eligible.map((row, index) => ({
            slot: index + 1,
            providerId: row.providerId,
            modelId: row.modelId,
            apiKey: row.apiKey,
          })),
        }),
      })
      if (response.status === 429) {
        setPageError('请求过于频繁：每 IP 每分钟最多发起 5 场对战，稍后再打')
        return
      }
      const payload = (await response.json().catch(() => ({}))) as {
        battleId?: string
        error?: string
      }
      if (!response.ok) {
        setPageError(payload.error ?? `开打失败：接口返回 HTTP ${response.status}`)
        return
      }
      if (!payload.battleId) {
        setPageError('开打失败：接口没有返回 battleId')
        return
      }
      if (skipped.length > 0) {
        setSkippedNotice(
          `以下选手预检未通过，本场不登场：${skipped
            .map(
              (row) =>
                `${providerById.get(row.providerId)?.label ?? row.providerId}（${row.modelId || '未填模型'}）`,
            )
            .join('、')}`,
        )
      }
      router.push(`/battle/${payload.battleId}`)
    } catch (error) {
      setPageError(`开打失败：${errorText(error)}`)
    } finally {
      setBusy('')
    }
  }, [arena, persist, providerById, pingContestants, router, rows, skin.minContestants, topicPrompt])

  const goNext = useCallback(() => {
    if (step === 1 && !step1Valid) return
    if (step === 2 && !step2Valid) return
    if (step === 3 && !step3Valid) return
    setPageError('')
    setStep(step + 1)
  }, [step, step1Valid, step2Valid, step3Valid])

  return (
    <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
      {/* ── Step rail ─────────────────────────────────────────── */}
      <nav aria-label="对战准备进度" className="lg:sticky lg:top-6 lg:self-start">
        <ol className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:gap-1 lg:overflow-visible lg:pb-0">
          {STEPS.map((item) => {
            const active = step === item.id
            const done = step > item.id
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => {
                    if (item.id <= step) setStep(item.id)
                  }}
                  disabled={item.id > step}
                  aria-current={active ? 'step' : undefined}
                  className={cn(
                    'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors',
                    active
                      ? 'bg-raise'
                      : done
                        ? 'text-ash hover:bg-raise/50 hover:text-ink'
                        : 'cursor-not-allowed text-dust',
                  )}
                >
                  <span
                    className={cn(
                      'flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-[11px] font-bold tabular-nums',
                      active
                        ? 'border-blaze/50 bg-blaze/15 text-blaze'
                        : done
                          ? 'border-ok/30 bg-ok/10 text-ok'
                          : 'border-hairline text-dust',
                    )}
                  >
                    {done ? <Check className="h-3.5 w-3.5" /> : String(item.id).padStart(2, '0')}
                  </span>
                  <span className="flex min-w-0 flex-col">
                    <span className="whitespace-nowrap text-sm font-semibold">{item.label}</span>
                    <span className="hidden whitespace-nowrap text-[11px] text-dust lg:block">
                      {item.hint}
                    </span>
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      </nav>

      {/* ── Step content ──────────────────────────────────────── */}
      <div className="flex min-w-0 flex-col gap-4">
        {step === 1 && (
          <section className="animate-rise rounded-xl border border-hairline bg-panel p-5 sm:p-6">
            <header className="flex flex-col gap-1">
              <h2 className="text-lg font-bold">选赛场</h2>
              <p className="text-[13px] leading-relaxed text-ash">
                五种玩法：从同题抢答到多轮互怼、残局杀法——判定方式各不相同。
              </p>
            </header>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {ARENA_IDS.map((id) => {
                const item = ARENA_SKINS[id]
                const Icon = ARENA_ICONS[id]
                const active = arena === id
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => selectArena(id)}
                    aria-pressed={active}
                    className={cn(
                      'relative flex items-start gap-3.5 rounded-xl border p-4 text-left transition-colors',
                      active ? cn('bg-raise', item.accentEdge) : 'border-hairline bg-void hover:border-dust/60',
                    )}
                  >
                    <span
                      className={cn(
                        'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border',
                        active ? item.accentChip : 'border-hairline bg-raise text-ash',
                      )}
                    >
                      <Icon className="h-4.5 w-4.5" />
                    </span>
                    <span className="flex min-w-0 flex-col gap-1">
                      <span className="flex items-center gap-2 text-[15px] font-bold">
                        {item.label}
                        <span className="rounded-full border border-hairline px-1.5 py-px font-mono text-[10px] font-medium text-dust">
                          {item.minContestants === item.maxContestants
                            ? `${item.minContestants} 人`
                            : `${item.minContestants}~${item.maxContestants} 人`}
                        </span>
                      </span>
                      <span className="text-[12.5px] leading-relaxed text-ash">{item.tagline}</span>
                    </span>
                    <span
                      aria-hidden
                      className={cn(
                        'absolute right-3.5 top-3.5 h-2 w-2 rounded-full transition-colors',
                        active ? 'bg-blaze' : 'bg-hairline',
                      )}
                    />
                  </button>
                )
              })}
            </div>
          </section>
        )}

        {step === 2 && (
          <section className="animate-rise rounded-xl border border-hairline bg-panel p-5 sm:p-6">
            <header className="flex flex-col gap-1">
              <h2 className="text-lg font-bold">定题目</h2>
              <p className="text-[13px] leading-relaxed text-ash">
                {arena === 'soft' && '选一个辩题、创意任务或角色扮演场景，全场选手作答同一道题。'}
                {arena === 'debate' && '选一个辩题：1 号位自动执正方、2 号位执反方，三轮定胜负。'}
                {arena === 'banter' && '选一个吵架场景：角色按座位号自动分配，风格天生产生分化。'}
                {arena === 'hard-code' && '选一道 Python 编程题，选手作答后由沙箱跑测试用例。'}
                {arena === 'chess' && '选一个残局：白先、限步将杀，着法由棋规真实校验。'}
              </p>
            </header>
            <div className="mt-5 flex flex-col gap-4">
              <label className="flex flex-col gap-2">
                <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
                  {arena === 'chess' ? '残局' : '题目'}
                </span>
                <select
                  value={topicId}
                  onChange={(event) => setTopicByArena((current) => ({ ...current, [arena]: event.target.value }))}
                  className="w-full rounded-lg border border-hairline bg-void px-3.5 py-2.5 text-sm text-ink outline-none transition-colors focus:border-blaze/60 sm:max-w-md"
                >
                  {topicOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>

              {arena === 'chess' && activePuzzle ? (
                <div className="flex flex-col gap-4 rounded-lg border-l-2 border-arena-chess/60 bg-void px-4 py-3.5 sm:flex-row sm:items-start">
                  <div className="w-full max-w-64 shrink-0">
                    <ChessBoard fen={activePuzzle.fen} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-1.5">
                    <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
                      题面
                    </span>
                    <p className="text-sm font-bold">
                      {activePuzzle.title} · 白先 {activePuzzle.mateIn} 步杀
                    </p>
                    <p className="text-[13px] leading-relaxed text-ash">{activePuzzle.brief}</p>
                    <p className="mt-auto font-mono text-[11px] text-dust">
                      大写=白方 小写=黑方 · 提交着法按 SAN 记法
                    </p>
                  </div>
                </div>
              ) : (
                <div
                  className={cn(
                    'rounded-lg border-l-2 bg-void px-4 py-3.5',
                    arena === 'soft' && 'border-soft-arena/60',
                    arena === 'debate' && 'border-arena-debate/60',
                    arena === 'banter' && 'border-arena-banter/60',
                    arena === 'hard-code' && 'border-hard-arena/60',
                  )}
                >
                  <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
                    {arena === 'debate' ? '辩题' : '题面'}
                  </span>
                  <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-[1.75] text-ink/90">
                    {topicPrompt || '（请先选择一道题）'}
                  </pre>
                  {arena === 'hard-code' && topicPrompt ? (
                    <p className="mt-3 flex items-center gap-2 font-mono text-[11px] text-dust">
                      <Terminal className="h-3.5 w-3.5 text-hard-arena" />
                      代码在 Pyodide WASM 沙箱中真实执行，无需 Docker
                    </p>
                  ) : null}
                  {arena === 'debate' && topicPrompt ? (
                    <p className="mt-3 flex items-center gap-2 font-mono text-[11px] text-dust">
                      <Scale className="h-3.5 w-3.5 text-arena-debate" />
                      立论 → 驳斥 → 结辩 三轮，裁判按逻辑 / 论据 / 交锋打分
                    </p>
                  ) : null}
                  {arena === 'banter' && topicPrompt ? (
                    <p className="mt-3 flex items-center gap-2 font-mono text-[11px] text-dust">
                      <Flame className="h-3.5 w-3.5 text-arena-banter" />
                      三轮互怼，每轮 ≤80 字；毒舌但越线会被扣分
                    </p>
                  ) : null}
                </div>
              )}
            </div>
          </section>
        )}

        {step === 3 && (
          <section className="animate-rise rounded-xl border border-hairline bg-panel p-5 sm:p-6">
            <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h2 className="text-lg font-bold">点大将</h2>
              <span
                className={cn(
                  'rounded-full border px-2.5 py-0.5 font-mono text-[11px] font-semibold tabular-nums',
                  step3Valid ? 'border-ok/25 bg-ok/10 text-ok' : 'border-warn/25 bg-warn/10 text-warn',
                )}
              >
                {greenCount}/{rows.length} 就绪
                {step3Valid ? '' : ` · 本赛场需要 ${skin.minContestants}~${skin.maxContestants} 人`}
              </span>
              <span className="w-full text-[13px] leading-relaxed text-ash sm:w-auto">
                {skin.label}赛场：{skin.multiRound ? '每名选手都会在多轮里出场。' : '每位选手 = 一个 provider + 一个模型 + 一把 Key。'}
              </span>
            </header>
            <div className="mt-5">
              <LineupEditor
                rows={rows}
                providers={providers}
                ping={ping}
                modelLists={modelLists}
                modelNotices={modelNotices}
                fetchingRow={fetchingRow}
                busy={busy}
                greenCount={greenCount}
                minContestants={skin.minContestants}
                maxContestants={skin.maxContestants}
                onUpdateRow={updateRow}
                onRemoveRow={removeRow}
                onAddRow={addRow}
                onFetchModels={handleFetchModels}
                onClearRowCache={clearRowCache}
              />
            </div>
          </section>
        )}

        {step === 4 && (
          <section className="animate-rise overflow-hidden rounded-xl border border-hairline bg-panel">
            <div className="border-b border-hairline bg-raise/60 px-5 py-4 sm:px-6">
              <div className="flex items-center gap-3">
                <Swords className="h-5 w-5 text-blaze" />
                <h2 className="text-lg font-bold">确认阵容，准备开打</h2>
              </div>
            </div>
            <div className="flex flex-col gap-4 p-5 sm:p-6">
              <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
                <div className="flex items-center justify-between gap-4 border-b border-hairline pb-2 sm:border-0 sm:pb-0">
                  <dt className="text-dust">赛场</dt>
                  <dd className={cn('rounded-full border px-2.5 py-0.5 text-xs font-semibold', skin.accentChip)}>
                    {skin.label}
                  </dd>
                </div>
                <div className="flex items-center justify-between gap-4 border-b border-hairline pb-2 sm:border-0 sm:pb-0">
                  <dt className="text-dust">选手</dt>
                  <dd className="font-mono text-sm font-bold tabular-nums">
                    {greenCount} 名就绪
                    {rows.length > greenCount ? (
                      <span className="ml-2 text-xs font-medium text-warn">
                        {rows.length - greenCount} 人未通过预检
                      </span>
                    ) : null}
                  </dd>
                </div>
              </dl>

              <div className="rounded-lg bg-void px-4 py-3">
                <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-dust">
                  {arena === 'debate' ? '辩题' : '题面'}
                </span>
                <p className="mt-1.5 whitespace-pre-wrap text-sm leading-relaxed text-ink/90">
                  {topicPrompt.split('\n').slice(0, 2).join(' · ').slice(0, 140)}
                  {topicPrompt.length > 140 ? '…' : ''}
                </p>
              </div>

              {pageError ? (
                <p
                  role="alert"
                  className="rounded-lg border border-bad/30 bg-bad/10 px-4 py-2.5 text-sm text-bad"
                >
                  {pageError}
                </p>
              ) : null}
              {skippedNotice ? (
                <p className="rounded-lg border border-warn/30 bg-warn/10 px-4 py-2.5 text-[13px] leading-relaxed text-warn">
                  {skippedNotice}
                </p>
              ) : null}

              <div className="flex flex-wrap items-center gap-3">
                <button type="button" onClick={handleFight} disabled={busy !== ''} className={BTN_PRIMARY}>
                  <Swords className="h-4 w-4" />
                  {busy === 'fight' ? '开打中…' : busy === 'ping' ? '预检中…' : '开战'}
                </button>
                <span className="flex items-center gap-1.5 text-[11px] text-dust">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  Key 只存在于本次对战的服务端内存，不写库、不进日志
                </span>
              </div>
            </div>
          </section>
        )}

        {/* ── Footer nav ──────────────────────────────────────── */}
        <div className="flex items-center justify-between gap-3">
          {step > 1 ? (
            <button type="button" onClick={() => setStep(step - 1)} className={BTN_GHOST}>
              <ChevronLeft className="h-4 w-4" />
              上一步
            </button>
          ) : (
            <span />
          )}
          {step === 3 ? (
            <button type="button" onClick={handlePing} disabled={busy !== ''} className={BTN_GHOST}>
              <Search className="h-4 w-4" />
              {busy === 'ping' ? '预检中…' : '预检全员'}
            </button>
          ) : null}
          {step < 4 ? (
            <button
              type="button"
              onClick={goNext}
              disabled={step === 1 ? !step1Valid : step === 2 ? !step2Valid : !step3Valid}
              className={BTN_PRIMARY}
            >
              下一步
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
