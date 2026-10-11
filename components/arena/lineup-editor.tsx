'use client'

import { useMemo } from 'react'
import type { ProviderDef } from '@/lib/providers/registry'

interface LineupContestantRow {
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

interface LineupEditorProps {
  rows: LineupContestantRow[]
  providers: ProviderDef[]
  ping: Record<string, PingOutcome>
  modelLists: Record<string, string[]>
  modelNotices: Record<string, ModelNotice>
  fetchingRow: string
  busy: string
  greenCount: number
  onUpdateRow: (rowId: string, patch: Partial<LineupContestantRow>) => void
  onRemoveRow: (rowId: string) => void
  onAddRow: () => void
  onFetchModels: (rowId: string) => void
  onClearRowCache: (rowId: string) => void
}

const MIN_CONTESTANTS = 2

const BRAND_BY_PROVIDER: Record<string, { emoji: string; color: string; bg: string; bgDark: string }> = {
  ollama: { emoji: '🦙', color: '#0d9488', bg: 'bg-teal-100', bgDark: 'dark:bg-teal-900/30' },
  deepseek: { emoji: '🐋', color: '#4d6bfe', bg: 'bg-blue-100', bgDark: 'dark:bg-blue-900/30' },
  siliconflow: { emoji: '🌊', color: '#7c3aed', bg: 'bg-violet-100', bgDark: 'dark:bg-violet-900/30' },
  zhipu: { emoji: '📘', color: '#0284c7', bg: 'bg-sky-100', bgDark: 'dark:bg-sky-900/30' },
  moonshot: { emoji: '🌙', color: '#e11d48', bg: 'bg-rose-100', bgDark: 'dark:bg-rose-900/30' },
  dashscope: { emoji: '☁️', color: '#ea580c', bg: 'bg-orange-100', bgDark: 'dark:bg-orange-900/30' },
  openai: { emoji: '🧠', color: '#059669', bg: 'bg-emerald-100', bgDark: 'dark:bg-emerald-900/30' },
  anthropic: { emoji: '🔶', color: '#c2410c', bg: 'bg-orange-100', bgDark: 'dark:bg-orange-900/30' },
  google: { emoji: '🔷', color: '#2563eb', bg: 'bg-blue-100', bgDark: 'dark:bg-blue-900/30' },
}

const FALLBACK_BRAND = { emoji: '🎯', color: '#71717a', bg: 'bg-zinc-100', bgDark: 'dark:bg-zinc-800' }

function brandOf(providerId: string) {
  return BRAND_BY_PROVIDER[providerId] ?? FALLBACK_BRAND
}

export function LineupEditor({
  rows,
  providers,
  ping,
  modelLists,
  modelNotices,
  fetchingRow,
  busy,
  greenCount,
  onUpdateRow,
  onRemoveRow,
  onAddRow,
  onFetchModels,
  onClearRowCache,
}: LineupEditorProps) {
  const providerById = useMemo(
    () => new Map(providers.map((p) => [p.id, p])),
    [providers],
  )

  return (
    <section>
      <div className="mb-4 flex items-center gap-3">
        <span className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-orange-500 text-sm font-black text-white shadow-sm">
          3
        </span>
        <div className="flex-1">
          <h2 className="text-lg font-bold text-zinc-900 dark:text-zinc-50">点大将</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            选择模型提供方和具体型号，填写 API Key
          </p>
        </div>
        <span className="rounded-full bg-emerald-100 px-3 py-1 text-xs font-bold text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
          {greenCount}/{rows.length} 就绪
        </span>
      </div>

      <div className="flex flex-col gap-4">
        {rows.map((row, index) => {
          const provider = providerById.get(row.providerId)
          const brand = brandOf(row.providerId)
          const outcome = ping[row.rowId]
          const models = modelLists[row.rowId]
          const notice = modelNotices[row.rowId]
          const pinged = outcome !== undefined

          return (
            <div
              key={row.rowId}
              className="group rounded-2xl border-2 border-zinc-200 bg-white p-5 transition-all hover:border-zinc-300 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700"
              style={{ borderLeftWidth: 4, borderLeftColor: brand.color }}
            >
              {/* Header */}
              <div className="flex flex-wrap items-center gap-3">
                <span
                  className={`flex h-10 w-10 items-center justify-center rounded-xl text-xl ${brand.bg} ${brand.bgDark}`}
                >
                  {brand.emoji}
                </span>
                <span className="text-sm font-black text-zinc-500 dark:text-zinc-400">
                  #{index + 1}
                </span>

                {/* Provider Select */}
                <select
                  value={row.providerId}
                  aria-label={`选手 ${index + 1} 模型提供方`}
                  onChange={(event) => {
                    const providerId = event.target.value
                    onUpdateRow(row.rowId, {
                      providerId,
                      apiKey:
                        providerId === 'ollama' && !row.apiKey ? 'ollama-local' : row.apiKey,
                    })
                    onClearRowCache(row.rowId)
                  }}
                  className="flex-1 rounded-xl border-2 border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm font-medium text-zinc-900 outline-none transition-colors focus:border-orange-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                >
                  {providers.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label} {item.needsProxy ? '（需代理）' : ''}
                    </option>
                  ))}
                </select>

                {/* Remove button */}
                <button
                  type="button"
                  onClick={() => onRemoveRow(row.rowId)}
                  disabled={rows.length <= MIN_CONTESTANTS}
                  title="移除这名选手"
                  className="flex h-10 w-10 items-center justify-center rounded-xl border-2 border-transparent text-zinc-400 transition-all hover:border-red-300 hover:bg-red-50 hover:text-red-500 disabled:cursor-not-allowed disabled:opacity-20 dark:hover:border-red-700 dark:hover:bg-red-950/30"
                >
                  ✕
                </button>
              </div>

              {/* Model Row */}
              <div className="mt-4 flex flex-wrap items-center gap-3">
                {models ? (
                  <select
                    value={row.modelId}
                    onChange={(event) => {
                      onUpdateRow(row.rowId, { modelId: event.target.value })
                      onClearRowCache(row.rowId)
                    }}
                    className="min-w-0 flex-1 rounded-xl border-2 border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm text-zinc-900 outline-none transition-colors focus:border-orange-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
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
                    aria-label={`选手 ${index + 1} 模型 ID`}
                    onChange={(event) => {
                      onUpdateRow(row.rowId, { modelId: event.target.value })
                      onClearRowCache(row.rowId)
                    }}
                    placeholder="模型 id，如 deepseek-chat / qwen3:4b"
                    className="min-w-0 flex-1 rounded-xl border-2 border-zinc-200 bg-zinc-50 px-4 py-2.5 text-sm text-zinc-900 outline-none transition-colors focus:border-orange-500 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-100"
                  />
                )}

                <button
                  type="button"
                  onClick={() => onFetchModels(row.rowId)}
                  disabled={fetchingRow === row.rowId || busy !== ''}
                  className="shrink-0 rounded-xl border-2 border-zinc-200 px-4 py-2.5 text-sm font-medium text-zinc-600 transition-all hover:border-orange-400 hover:text-orange-600 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-300 dark:hover:text-orange-400"
                >
                  {fetchingRow === row.rowId ? '拉取中…' : '↻ 获取列表'}
                </button>
              </div>

              {/* Status indicator */}
              <div className="mt-3 flex items-center gap-2">
                {pinged ? (
                  <>
                    <span
                      className={`inline-block h-2.5 w-2.5 rounded-full ${
                        outcome.ok ? 'bg-emerald-500' : 'bg-red-500'
                      }`}
                    />
                    <span
                      className={`text-sm font-medium ${
                        outcome.ok
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : 'text-red-600 dark:text-red-400'
                      }`}
                    >
                      {outcome.message}
                    </span>
                  </>
                ) : (
                  <>
                    <span className="inline-block h-2.5 w-2.5 rounded-full bg-zinc-300 dark:bg-zinc-600" />
                    <span className="text-sm text-zinc-400 dark:text-zinc-500">
                      未预检 — 点击「预检全员」或「开打」时自动检测
                    </span>
                  </>
                )}
              </div>

              {/* API Key area */}
              <div className="mt-4 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800/50">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-xs font-bold text-zinc-400">API Key</span>
                  <input
                    type="password"
                    value={row.apiKey}
                    aria-label={`选手 ${index + 1} API Key`}
                    onChange={(event) => onUpdateRow(row.rowId, { apiKey: event.target.value })}
                    placeholder={
                      provider?.id === 'ollama'
                        ? '本地 Ollama 无需 Key，已预填占位值'
                        : 'sk-…'
                    }
                    autoComplete="off"
                    className="min-w-0 flex-1 rounded-lg border-2 border-transparent bg-white px-3 py-2 text-sm text-zinc-900 outline-none transition-colors focus:border-orange-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
                  />
                </div>
                {provider ? (
                  <p className="mt-2 text-xs text-zinc-400 dark:text-zinc-500">
                    {provider.needsProxy ? '⚠ 需代理才能直连' : '✓ 国内可直连'} · Key 仅当次请求持有，不落库
                  </p>
                ) : null}
              </div>

              {/* Model fetch notice */}
              {notice ? (
                <p
                  className={`mt-2 text-xs font-medium ${
                    notice.kind === 'error'
                      ? 'text-red-500 dark:text-red-400'
                      : 'text-zinc-500 dark:text-zinc-400'
                  }`}
                >
                  {notice.text}
                </p>
              ) : null}
            </div>
          )
        })}
      </div>

      {/* Add Button */}
      <button
        type="button"
        onClick={onAddRow}
        className="mt-4 flex w-full items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-zinc-300 bg-zinc-50 py-4 text-sm font-bold text-zinc-500 transition-all hover:border-orange-400 hover:bg-orange-50 hover:text-orange-600 dark:border-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-400 dark:hover:text-orange-400"
      >
        <span className="text-lg">+</span>
        加一名选手
      </button>

      {/* Security notice */}
      <p className="mt-4 rounded-xl bg-zinc-100 px-4 py-3 text-xs leading-relaxed text-zinc-500 dark:bg-zinc-800/50 dark:text-zinc-500">
        🔒 API Key 仅随请求发往本机服务端，不写库、不进日志。阵容存于 localStorage，
        Key 存于 sessionStorage，关闭标签页即失效。
      </p>
    </section>
  )
}
