'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import type { ArenaId } from '@/lib/arena/types'
import type { CodeTopic, SoftTopic } from '@/lib/arena/topics'
import type { ProviderDef } from '@/lib/providers/registry'
import { LineupEditor } from './lineup-editor'

export interface ArenaSetupProps {
  providers: ProviderDef[]
  softTopics: SoftTopic[]
  codeTopics: CodeTopic[]
}

const LINEUP_STORAGE_KEY = 'melee.lineup'
const KEY_STORAGE_KEY = 'melee.keys'
const MIN_CONTESTANTS = 2

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

interface ArenaMeta {
  emoji: string
  label: string
  tagline: string
  accent: string
  activeClass: string
  iconBg: string
}

const ARENA_META: Record<ArenaId, ArenaMeta> = {
  soft: {
    emoji: '🎭',
    label: '软战场',
    tagline: '辩论文 / 创意 / 角色扮演，观众投票 + AI 裁判双轨判定',
    accent: '#6366f1',
    activeClass:
      'border-indigo-500 bg-indigo-50 shadow-indigo-500/10 shadow-lg dark:border-indigo-400 dark:bg-indigo-950/30',
    iconBg: 'bg-indigo-100 dark:bg-indigo-900/40',
  },
  'hard-code': {
    emoji: '🐍',
    label: '硬战场',
    tagline: 'Python 代码实战，沙箱真跑测试用例，按通过率判定',
    accent: '#f59e0b',
    activeClass:
      'border-amber-500 bg-amber-50 shadow-amber-500/10 shadow-lg dark:border-amber-400 dark:bg-amber-950/30',
    iconBg: 'bg-amber-100 dark:bg-amber-900/40',
  },
}

const ARENA_IDS: ArenaId[] = ['soft', 'hard-code']

const STEP_LABELS = ['选赛道', '定题目', '点大将', '开打']

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

function readLineup(providers: ProviderDef[]): {
  arena: ArenaId
  softTopicId: string
  codeTopicId: string
  rows: ContestantRow[]
} {
  const fallback = {
    arena: 'soft' as ArenaId,
    softTopicId: '',
    codeTopicId: '',
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
    if (rows.length < MIN_CONTESTANTS) return fallback
    const arena = ARENA_IDS.includes(parsed.arena as ArenaId)
      ? (parsed.arena as ArenaId)
      : fallback.arena
    return {
      arena,
      softTopicId: typeof parsed.softTopicId === 'string' ? parsed.softTopicId : '',
      codeTopicId: typeof parsed.codeTopicId === 'string' ? parsed.codeTopicId : '',
      rows,
    }
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

export function ArenaSetup({ providers, softTopics, codeTopics }: ArenaSetupProps) {
  const router = useRouter()
  const providerById = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  )

  const [step, setStep] = useState(1)
  const [arena, setArena] = useState<ArenaId>('soft')
  const [softTopicId, setSoftTopicId] = useState(softTopics[0]?.id ?? '')
  const [codeTopicId, setCodeTopicId] = useState(codeTopics[0]?.id ?? '')
  const [rows, setRows] = useState<ContestantRow[]>([
    blankRow('ollama'),
    blankRow('deepseek'),
  ])
  const [ping, setPing] = useState<Record<string, PingOutcome>>({})
  const [modelLists, setModelLists] = useState<Record<string, string[]>>({})
  const [modelNotices, setModelNotices] = useState<Record<string, ModelNotice>>({})
  const [fetchingRow, setFetchingRow] = useState<string>('')
  const [busy, setBusy] = useState<'' | 'ping' | 'fight'>('')
  const [pageError, setPageError] = useState('')
  const [skippedNotice, setSkippedNotice] = useState('')
  const [hydrated, setHydrated] = useState(false)
  const hydratedRef = useRef(false)

  const topicId = arena === 'soft' ? softTopicId : codeTopicId
  const activeSoftTopic = softTopics.find((topic) => topic.id === softTopicId)
  const activeCodeTopic = codeTopics.find((topic) => topic.id === codeTopicId)
  const topicPrompt =
    arena === 'soft' ? activeSoftTopic?.prompt ?? '' : activeCodeTopic?.prompt ?? ''

  const topicOptions: Array<{ id: string; label: string }> =
    arena === 'soft'
      ? softTopics.map((topic) => ({ id: topic.id, label: `${topic.title}（${topic.kind}）` }))
      : codeTopics.map((topic) => ({
          id: topic.id,
          label: `${topic.title}（${topic.tests.length} 个用例）`,
        }))

  // Step validation
  const step1Valid = !!arena
  const step2Valid = !!topicPrompt
  const greenCount = rows.filter((row) => ping[row.rowId]?.ok === true).length
  const step3Valid = greenCount >= MIN_CONTESTANTS

  useEffect(() => {
    if (hydratedRef.current) return
    hydratedRef.current = true
    const restored = readLineup(providers)
    setArena(restored.arena)
    setRows(restored.rows)
    if (softTopics.some((topic) => topic.id === restored.softTopicId)) {
      setSoftTopicId(restored.softTopicId)
    }
    if (codeTopics.some((topic) => topic.id === restored.codeTopicId)) {
      setCodeTopicId(restored.codeTopicId)
    }
    setHydrated(true)
  }, [providers, softTopics, codeTopics])

  const persist = useCallback(() => {
    try {
      window.localStorage.setItem(
        LINEUP_STORAGE_KEY,
        JSON.stringify({
          arena,
          softTopicId,
          codeTopicId,
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
  }, [arena, softTopicId, codeTopicId, rows])

  useEffect(() => {
    if (hydrated) persist()
  }, [hydrated, persist])

  const updateRow = useCallback((rowId: string, patch: Partial<ContestantRow>) => {
    setRows((current) =>
      current.map((row) => (row.rowId === rowId ? { ...row, ...patch } : row)),
    )
  }, [])

  const addRow = useCallback(() => {
    setRows((current) => [
      ...current,
      blankRow(current[current.length - 1]?.providerId ?? 'ollama'),
    ])
  }, [])

  const removeRow = useCallback((rowId: string) => {
    setRows((current) =>
      current.length > MIN_CONTESTANTS
        ? current.filter((row) => row.rowId !== rowId)
        : current,
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
  }, [])

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
            [rowId]: {
              kind: 'info',
              text: `拉到 ${(payload.models as string[]).length} 个模型`,
            },
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
    if (eligible.length < MIN_CONTESTANTS) {
      setBusy('')
      setPageError(
        `预检通过的选手只有 ${eligible.length} 名，至少需要 ${MIN_CONTESTANTS} 名才能开打`,
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
  }, [arena, persist, providerById, pingContestants, router, rows, topicPrompt])

  const meta = ARENA_META[arena]

  // ── Helper: go to next step with validation ─────────────────
  const goNext = useCallback(() => {
    if (step === 1 && !step1Valid) return
    if (step === 2 && !step2Valid) return
    if (step === 3 && !step3Valid) return
    setPageError('')
    setStep(step + 1)
  }, [step, step1Valid, step2Valid, step3Valid])

  // ── Render ────────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-6">
      {/* ── Progress Indicator ──────────────────────────────── */}
      <nav aria-label="对战准备进度" className="rounded-2xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex items-center gap-0">
          {STEP_LABELS.map((label, i) => {
            const s = i + 1
            const isActive = step === s
            const isDone = step > s
            const isLast = s === STEP_LABELS.length
            return (
              <div key={label} className="flex flex-1 items-center">
                <button
                  type="button"
                  onClick={() => {
                    if (s <= step) setStep(s)
                  }}
                  disabled={s > step}
                  className="flex flex-col items-center gap-1.5 group disabled:cursor-default"
                >
                  <span
                    className={`flex h-9 w-9 items-center justify-center rounded-full text-sm font-black transition-all ${
                      isActive
                        ? 'bg-orange-500 text-white shadow-md shadow-orange-500/30 scale-110'
                        : isDone
                          ? 'bg-emerald-500 text-white'
                          : 'bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-400'
                    }`}
                  >
                    {isDone ? '✓' : s}
                  </span>
                  <span
                    className={`text-xs font-semibold ${
                      isActive
                        ? 'text-orange-600 dark:text-orange-400'
                        : isDone
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-zinc-400'
                    }`}
                  >
                    {label}
                  </span>
                </button>
                {!isLast ? (
                  <span
                    className={`mx-2 h-0.5 flex-1 rounded-full transition-colors ${
                      step > s ? 'bg-emerald-400 dark:bg-emerald-600' : 'bg-zinc-200 dark:bg-zinc-700'
                    }`}
                  />
                ) : null}
              </div>
            )
          })}
        </div>
        {/* Progress bar fill */}
        <div className="mt-3 h-1.5 rounded-full bg-zinc-100 dark:bg-zinc-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-orange-500 to-amber-500 transition-all duration-500"
            style={{ width: `${((step - 1) / (STEP_LABELS.length - 1)) * 100}%` }}
          />
        </div>
      </nav>

      {/* ── Step 1: Arena ─────────────────────────────────────── */}
      {step === 1 && (
        <section className="animate-fade-in-up">
          <div className="rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">
              选赛道
            </h2>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              决定比赛方式：是靠嘴皮子（软战场）还是拼真功夫（硬战场代码）。
            </p>
            <div className="mt-5 grid gap-4 sm:grid-cols-2">
              {ARENA_IDS.map((id) => {
                const item = ARENA_META[id]
                const active = arena === id
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setArena(id)}
                    aria-pressed={active}
                    className={`group relative flex items-start gap-4 rounded-2xl border-2 p-5 text-left transition-all ${
                      active
                        ? `${item.activeClass}`
                        : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700'
                    }`}
                  >
                    <span
                      className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl text-2xl ${item.iconBg}`}
                    >
                      {item.emoji}
                    </span>
                    <span className="flex flex-col gap-1">
                      <span className="text-base font-bold text-zinc-900 dark:text-zinc-50">
                        {item.label}
                      </span>
                      <span className="text-sm leading-relaxed text-zinc-500 dark:text-zinc-400">
                        {item.tagline}
                      </span>
                    </span>
                    {active ? (
                      <span className="absolute right-3 top-3 flex h-7 w-7 items-center justify-center rounded-full bg-white text-sm font-bold text-orange-500 shadow-sm dark:bg-zinc-800">
                        ✓
                      </span>
                    ) : null}
                  </button>
                )
              })}
            </div>
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={goNext}
              disabled={!step1Valid}
              className="mt-4 rounded-xl bg-gradient-to-r from-orange-500 to-amber-500 px-6 py-3 text-sm font-black text-white shadow-md shadow-orange-500/20 transition-all hover:shadow-orange-500/30 hover:brightness-110 disabled:cursor-not-allowed disabled:from-zinc-400 disabled:to-zinc-500 disabled:shadow-none"
            >
              下一步：定题目 →
            </button>
          </div>
        </section>
      )}

      {/* ── Step 2: Topic ─────────────────────────────────────── */}
      {step === 2 && (
        <section className="animate-fade-in-up">
          <div className="rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
            <h2 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">
              定题目
            </h2>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              {arena === 'soft' ? '选一个辩题、创意任务或角色扮演场景。' : '选一道 Python 编程题，选手作答后由沙箱跑测试。'}
            </p>
            <div className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
              <select
                id="topic-select"
                aria-label="选择本场题目"
                value={topicId}
                onChange={(event) =>
                  arena === 'soft'
                    ? setSoftTopicId(event.target.value)
                    : setCodeTopicId(event.target.value)
                }
                className="w-full rounded-xl border-2 border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-900 outline-none transition-colors focus:border-orange-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100 sm:w-80"
              >
                {topicOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
              <div className="flex-1 rounded-xl border-2 border-zinc-100 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-800/50">
                <div className="flex items-center gap-2">
                  <span className="text-lg">{meta.emoji}</span>
                  <span className="text-xs font-bold tracking-wider text-zinc-400">
                    {arena === 'soft' ? '软战场题面' : '硬战场题面'}
                  </span>
                </div>
                <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
                  {topicPrompt || '（请先在左侧选择一道题）'}
                </pre>
                {arena === 'hard-code' && topicPrompt ? (
                  <p className="mt-3 flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                    <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
                    代码在 Pyodide WASM 沙箱中执行，无需 Docker
                  </p>
                ) : null}
              </div>
            </div>
          </div>
          <div className="flex justify-between">
            <button
              type="button"
              onClick={() => setStep(1)}
              className="mt-4 rounded-xl border-2 border-zinc-200 px-5 py-3 text-sm font-bold text-zinc-600 transition-all hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              ← 上一步
            </button>
            <button
              type="button"
              onClick={goNext}
              disabled={!step2Valid}
              className="mt-4 rounded-xl bg-gradient-to-r from-orange-500 to-amber-500 px-6 py-3 text-sm font-black text-white shadow-md shadow-orange-500/20 transition-all hover:shadow-orange-500/30 hover:brightness-110 disabled:cursor-not-allowed disabled:from-zinc-400 disabled:to-zinc-500 disabled:shadow-none"
            >
              下一步：点大将 →
            </button>
          </div>
        </section>
      )}

      {/* ── Step 3: Lineup ────────────────────────────────────── */}
      {step === 3 && (
        <div className="animate-fade-in-up">
          <div className="rounded-2xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-bold text-zinc-900 dark:text-zinc-50">点大将</h2>
              <span
                className={`rounded-full px-3 py-1 text-xs font-bold ${
                  step3Valid
                    ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400'
                    : 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400'
                }`}
              >
                {step3Valid ? `${greenCount} 人就绪 ✓` : `需至少 ${MIN_CONTESTANTS} 人通过预检`}
              </span>
            </div>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              选择模型提供方和具体型号，填写 API Key。点「预检全员」确认都能连通后再开打。
            </p>
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
                onUpdateRow={updateRow}
                onRemoveRow={removeRow}
                onAddRow={addRow}
                onFetchModels={handleFetchModels}
                onClearRowCache={clearRowCache}
              />
            </div>
          </div>
          <div className="flex justify-between">
            <button
              type="button"
              onClick={() => setStep(2)}
              className="mt-4 rounded-xl border-2 border-zinc-200 px-5 py-3 text-sm font-bold text-zinc-600 transition-all hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              ← 上一步
            </button>
            <button
              type="button"
              onClick={handlePing}
              disabled={busy !== ''}
              className="mt-4 rounded-xl border-2 border-zinc-300 px-5 py-3 text-sm font-bold text-zinc-700 transition-all hover:border-orange-400 hover:text-orange-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-200 dark:hover:text-orange-400"
            >
              {busy === 'ping' ? '预检中…' : '🔍 预检全员'}
            </button>
          </div>
        </div>
      )}

      {/* ── Step 4: Launch ────────────────────────────────────── */}
      {step === 4 && (
        <section className="animate-fade-in-up">
          <div className="overflow-hidden rounded-2xl border-2 border-orange-300 bg-gradient-to-br from-orange-50 to-amber-50 dark:border-orange-800 dark:from-zinc-900 dark:to-zinc-900">
            <div className="p-6 sm:p-8">
              <div className="flex items-center gap-3">
                <span className="text-3xl">⚔️</span>
                <h2 className="text-2xl font-black text-zinc-900 dark:text-zinc-50">准备开打！</h2>
              </div>
              <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
                确认以下配置后将发起对战。预检未通过的选手将不参与本场。
              </p>

              {/* Summary card */}
              <div className="mt-5 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-800/50">
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                  <span className="flex items-center gap-1.5">
                    <span className="text-lg">{meta.emoji}</span>
                    <strong>{meta.label}</strong>
                  </span>
                  <span className="flex items-center gap-1.5 text-zinc-500">
                    👥 {greenCount} 名选手就绪
                  </span>
                  {rows.length > greenCount ? (
                    <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
                      ⚠ {rows.length - greenCount} 人未预检
                    </span>
                  ) : null}
                </div>
                <p className="mt-3 border-t border-zinc-100 pt-3 text-xs leading-relaxed text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
                  题目：{topicPrompt.slice(0, 120)}{topicPrompt.length > 120 ? '…' : ''}
                </p>
              </div>

              {/* Error / Skipped */}
              {pageError ? (
                <p className="mt-4 rounded-xl border-2 border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300">
                  {pageError}
                </p>
              ) : null}
              {skippedNotice ? (
                <p className="mt-4 rounded-xl border-2 border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
                  {skippedNotice}
                </p>
              ) : null}

              {/* Launch button */}
              <div className="mt-6 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={handleFight}
                  disabled={busy !== ''}
                  className="rounded-xl bg-gradient-to-r from-orange-500 via-red-500 to-amber-500 px-8 py-3.5 text-base font-black tracking-wide text-white shadow-lg shadow-orange-500/30 transition-all hover:shadow-orange-500/50 hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy === 'fight' ? '开打中…' : busy === 'ping' ? '预检中…' : '⚔️  开战！'}
                </button>
                {skippedNotice ? (
                  <button
                    type="button"
                    onClick={handleFight}
                    disabled={busy !== ''}
                    className="rounded-xl border-2 border-orange-300 px-5 py-3 text-sm font-bold text-orange-600 transition-all hover:bg-orange-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-orange-700 dark:text-orange-400 dark:hover:bg-orange-950/30"
                  >
                    依然开战
                  </button>
                ) : null}
              </div>
            </div>
          </div>

          <div className="mt-4">
            <button
              type="button"
              onClick={() => setStep(3)}
              className="rounded-xl border-2 border-zinc-200 px-5 py-3 text-sm font-bold text-zinc-600 transition-all hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              ← 返回修改选手
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
