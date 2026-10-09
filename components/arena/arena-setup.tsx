'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import type { ArenaId } from '@/lib/arena/types'
import type { CodeTopic, SoftTopic } from '@/lib/arena/topics'
import type { ProviderDef } from '@/lib/providers/registry'

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
}

const ARENA_META: Record<ArenaId, ArenaMeta> = {
  soft: {
    emoji: '🎭',
    label: '软战场',
    tagline: '辩论文 / 创意 / 角色扮演，观众投票 + AI 裁判双轨判定',
    accent: '#6366f1',
    activeClass:
      'border-indigo-500 bg-indigo-50 dark:border-indigo-400 dark:bg-indigo-400/10',
  },
  'hard-code': {
    emoji: '🐍',
    label: '硬战场 · 代码',
    tagline: 'Python 题真跑测试用例，按通过率判定',
    accent: '#f59e0b',
    activeClass:
      'border-amber-500 bg-amber-50 dark:border-amber-400 dark:bg-amber-400/10',
  },
}

const ARENA_IDS: ArenaId[] = ['soft', 'hard-code']

const BRAND_BY_PROVIDER: Record<string, { emoji: string; color: string }> = {
  ollama: { emoji: '🦙', color: '#0d9488' },
  deepseek: { emoji: '🐋', color: '#4d6bfe' },
  siliconflow: { emoji: '🌊', color: '#7c3aed' },
  zhipu: { emoji: '📘', color: '#0284c7' },
  moonshot: { emoji: '🌙', color: '#e11d48' },
  dashscope: { emoji: '☁️', color: '#ea580c' },
  openai: { emoji: '🧠', color: '#059669' },
  anthropic: { emoji: '🔶', color: '#c2410c' },
  google: { emoji: '🔷', color: '#2563eb' },
}

const FALLBACK_BRAND = { emoji: '🎯', color: '#71717a' }

const CONTROL_CLASS =
  'rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none transition-colors focus:border-amber-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100'

const PRIMARY_BUTTON_CLASS =
  'rounded-lg bg-amber-500 px-6 py-3 text-base font-black tracking-wide text-zinc-950 transition-colors hover:bg-amber-400 disabled:cursor-not-allowed disabled:bg-zinc-400 disabled:text-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-600'

const GHOST_BUTTON_CLASS =
  'rounded-lg border border-zinc-400 px-4 py-3 text-sm font-bold text-zinc-700 transition-colors hover:border-amber-500 hover:text-amber-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-600 dark:text-zinc-200 dark:hover:text-amber-400'

function brandOf(providerId: string) {
  return BRAND_BY_PROVIDER[providerId] ?? FALLBACK_BRAND
}

function makeRowId() {
  return `r${Math.random().toString(36).slice(2, 10)}`
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
  return error instanceof Error ? error.message : String(error)
}

export function ArenaSetup({ providers, softTopics, codeTopics }: ArenaSetupProps) {
  const router = useRouter()
  const providerById = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  )

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
      // 隐私模式下 localStorage / sessionStorage 可能不可写，忽略即可
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
              text: `已从 ${provider.label} 拉到 ${(payload.models as string[]).length} 个模型`,
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
            .map((row) => `${providerById.get(row.providerId)?.label ?? row.providerId}（${row.modelId || '未填模型'}）`)
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
  const greenCount = rows.filter((row) => ping[row.rowId]?.ok === true).length

  return (
    <div className="flex flex-col gap-6">
      <section
        className="overflow-hidden rounded-2xl border border-zinc-300 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="flex items-center gap-2 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <span className="rounded bg-zinc-900 px-2 py-0.5 text-xs font-black text-amber-400 dark:bg-zinc-950">
            01
          </span>
          <h2 className="text-sm font-black tracking-widest text-zinc-500 dark:text-zinc-400">
            选赛道
          </h2>
        </div>
        <div className="grid gap-3 p-5 sm:grid-cols-2">
          {ARENA_IDS.map((id) => {
            const item = ARENA_META[id]
            const active = arena === id
            return (
              <button
                key={id}
                type="button"
                onClick={() => setArena(id)}
                aria-pressed={active}
                className={`flex items-start gap-3 rounded-xl border-2 p-4 text-left transition-colors ${
                  active
                    ? item.activeClass
                    : 'border-zinc-300 bg-white hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:hover:border-zinc-500'
                }`}
              >
                <span className="text-2xl leading-none">{item.emoji}</span>
                <span className="flex flex-col gap-1">
                  <span className="text-base font-black text-zinc-900 dark:text-zinc-50">
                    {item.label}
                  </span>
                  <span className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
                    {item.tagline}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </section>

      <section
        className="overflow-hidden rounded-2xl border border-zinc-300 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900"
        style={{ borderTop: `4px solid ${meta.accent}` }}
      >
        <div className="flex flex-wrap items-center gap-2 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <span className="rounded bg-zinc-900 px-2 py-0.5 text-xs font-black text-amber-400 dark:bg-zinc-950">
            02
          </span>
          <h2 className="text-sm font-black tracking-widest text-zinc-500 dark:text-zinc-400">
            定题目
          </h2>
        </div>
        <div className="flex flex-col gap-3 p-5">
          <select
            value={topicId}
            onChange={(event) =>
              arena === 'soft'
                ? setSoftTopicId(event.target.value)
                : setCodeTopicId(event.target.value)
            }
            className={`${CONTROL_CLASS} max-w-xl`}
          >
            {topicOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <div
            className="rounded-lg border border-zinc-200 bg-zinc-50 p-4 dark:border-zinc-800 dark:bg-zinc-950"
            style={{ borderLeft: `4px solid ${meta.accent}` }}
          >
            <p className="text-xs font-bold text-zinc-400">
              {meta.emoji} {arena === 'soft' ? '软战场题面' : '硬战场题面（读标准输入，写标准输出）'}
            </p>
            <pre className="mt-2 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
              {topicPrompt}
            </pre>
            {arena === 'hard-code' ? (
              <p className="mt-3 text-xs text-amber-600 dark:text-amber-400">
                硬战场的沙箱判定后端尚未接入，当前开打会被服务端明确拒绝。
              </p>
            ) : null}
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border border-zinc-300 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
          <div className="flex items-center gap-2">
            <span className="rounded bg-zinc-900 px-2 py-0.5 text-xs font-black text-amber-400 dark:bg-zinc-950">
              03
            </span>
            <h2 className="text-sm font-black tracking-widest text-zinc-500 dark:text-zinc-400">
              点名将
            </h2>
            <span className="text-xs text-zinc-400">
              {rows.length} 人在场 · 预检通过 {greenCount} 人
            </span>
          </div>
          <button
            type="button"
            onClick={addRow}
            className="rounded-lg border border-zinc-400 px-3 py-1.5 text-xs font-bold text-zinc-700 transition-colors hover:border-amber-500 hover:text-amber-600 dark:border-zinc-600 dark:text-zinc-200 dark:hover:text-amber-400"
          >
            + 加一名选手
          </button>
        </div>
        <ul className="flex flex-col gap-3 p-5">
          {rows.map((row, index) => {
            const provider = providerById.get(row.providerId)
            const brand = brandOf(row.providerId)
            const outcome = ping[row.rowId]
            const models = modelLists[row.rowId]
            const notice = modelNotices[row.rowId]
            const pinged = outcome !== undefined
            return (
              <li
                key={row.rowId}
                className="rounded-xl border border-zinc-300 bg-zinc-50 p-4 dark:border-zinc-700 dark:bg-zinc-950"
                style={{ borderLeft: `4px solid ${brand.color}` }}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="w-6 text-center text-lg leading-none">{brand.emoji}</span>
                  <span className="w-8 text-center text-xs font-black text-zinc-400">
                    {index + 1}
                  </span>
                  <select
                    value={row.providerId}
                    onChange={(event) => {
                      const providerId = event.target.value
                      updateRow(row.rowId, {
                        providerId,
                        apiKey: providerId === 'ollama' && !row.apiKey ? 'ollama-local' : row.apiKey,
                      })
                      setPing((current) => {
                        const next = { ...current }
                        delete next[row.rowId]
                        return next
                      })
                      setModelLists((current) => {
                        const next = { ...current }
                        delete next[row.rowId]
                        return next
                      })
                      setModelNotices((current) => {
                        const next = { ...current }
                        delete next[row.rowId]
                        return next
                      })
                    }}
                    className={`${CONTROL_CLASS} w-48`}
                  >
                    {providers.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                        {item.needsProxy ? '（需代理）' : ''}
                      </option>
                    ))}
                  </select>
                  {models ? (
                    <select
                      value={row.modelId}
                      onChange={(event) => {
                        updateRow(row.rowId, { modelId: event.target.value })
                        setPing((current) => {
                          const next = { ...current }
                          delete next[row.rowId]
                          return next
                        })
                      }}
                      className={`${CONTROL_CLASS} min-w-56 flex-1`}
                    >
                      <option value="">从列表选择模型…</option>
                      {row.modelId && !models.includes(row.modelId) ? (
                        <option value={row.modelId}>{row.modelId}（手填）</option>
                      ) : null}
                      {models.map((modelId) => (
                        <option key={modelId} value={modelId}>
                          {modelId}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={row.modelId}
                      onChange={(event) => {
                        updateRow(row.rowId, { modelId: event.target.value })
                        setPing((current) => {
                          const next = { ...current }
                          delete next[row.rowId]
                          return next
                        })
                      }}
                      placeholder="模型 id，如 deepseek-chat / qwen3:4b"
                      className={`${CONTROL_CLASS} min-w-56 flex-1`}
                    />
                  )}
                  <button
                    type="button"
                    onClick={() => handleFetchModels(row.rowId)}
                    disabled={fetchingRow === row.rowId || busy !== ''}
                    className="rounded-lg border border-zinc-400 px-3 py-2 text-xs font-bold text-zinc-700 transition-colors hover:border-amber-500 hover:text-amber-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-600 dark:text-zinc-200 dark:hover:text-amber-400"
                  >
                    {fetchingRow === row.rowId ? '拉取中…' : '获取模型列表'}
                  </button>
                  <span className="flex min-w-40 flex-1 items-start gap-1.5 text-xs leading-relaxed">
                    {pinged ? (
                      <>
                        <span
                          className={`mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full ${
                            outcome.ok ? 'bg-emerald-500' : 'bg-red-500'
                          }`}
                        />
                        <span
                          className={
                            outcome.ok
                              ? 'text-emerald-700 dark:text-emerald-400'
                              : 'text-red-700 dark:text-red-400'
                          }
                        >
                          {outcome.message}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className="mt-1 inline-block h-2.5 w-2.5 shrink-0 rounded-full bg-zinc-400" />
                        <span className="text-zinc-400">未预检，开打时会自动预检</span>
                      </>
                    )}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeRow(row.rowId)}
                    disabled={rows.length <= MIN_CONTESTANTS}
                    className="rounded-lg border border-transparent px-2 py-2 text-xs font-bold text-zinc-400 transition-colors hover:border-red-400 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-30"
                  >
                    删
                  </button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-bold text-zinc-400">API Key</span>
                  <input
                    type="password"
                    value={row.apiKey}
                    onChange={(event) => updateRow(row.rowId, { apiKey: event.target.value })}
                    placeholder={provider?.id === 'ollama' ? '本地 Ollama 无需 Key，已预填占位值' : 'sk-…'}
                    autoComplete="off"
                    className={`${CONTROL_CLASS} w-72`}
                  />
                  {provider ? (
                    <span className="text-xs text-zinc-400">
                      {provider.needsProxy ? '需代理才能直连' : '国内可直连'} ·{' '}
                      {provider.kind === 'openai-compatible'
                        ? provider.baseUrl
                        : provider.kind}
                    </span>
                  ) : null}
                  {notice ? (
                    <span
                      className={`text-xs ${
                        notice.kind === 'error'
                          ? 'text-red-600 dark:text-red-400'
                          : 'text-zinc-500 dark:text-zinc-400'
                      }`}
                    >
                      {notice.text}
                    </span>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ul>
        <p className="border-t border-zinc-200 px-5 py-3 text-xs leading-relaxed text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          Key 只随请求发往本机服务端接口，不写库、不进日志；服务端仅在当次对战的内存里持有。
          阵容（选手与模型）存在 localStorage，Key 存在 sessionStorage，关掉标签页即失效。
        </p>
      </section>

      <section className="flex flex-col gap-3 rounded-2xl border border-zinc-300 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={handlePing}
            disabled={busy !== ''}
            className={GHOST_BUTTON_CLASS}
          >
            {busy === 'ping' ? '预检中…' : '预检全员'}
          </button>
          <button
            type="button"
            onClick={handleFight}
            disabled={busy !== '' || rows.length < MIN_CONTESTANTS}
            className={PRIMARY_BUTTON_CLASS}
          >
            {busy === 'fight' ? '开打中…' : '⚔️ 开打'}
          </button>
          <span className="text-xs text-zinc-400">
            {meta.emoji} {meta.label} · {topicPrompt ? '题面就绪' : '未选题'}
          </span>
        </div>
        {pageError ? (
          <p className="rounded-lg border border-red-400 bg-red-50 px-4 py-3 text-sm font-bold text-red-700 dark:bg-red-950/40 dark:text-red-300">
            {pageError}
          </p>
        ) : null}
        {skippedNotice ? (
          <p className="rounded-lg border border-amber-400 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            {skippedNotice}
          </p>
        ) : null}
      </section>
    </div>
  )
}
