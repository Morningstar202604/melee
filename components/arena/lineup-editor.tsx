'use client'

import { useMemo } from 'react'
import { KeyRound, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { cn } from '@/lib/cn'
import type { ProviderDef } from '@/lib/providers/registry'
import { providerAccent } from './theme'

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

const INPUT_CLASS =
  'min-w-0 flex-1 rounded-lg border border-hairline bg-void px-3.5 py-2.5 text-sm text-ink outline-none transition-colors placeholder:text-dust focus:border-blaze/60'

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
      <div className="flex flex-col gap-3">
        {rows.map((row, index) => {
          const provider = providerById.get(row.providerId)
          const accent = providerAccent(row.providerId)
          const outcome = ping[row.rowId]
          const models = modelLists[row.rowId]
          const notice = modelNotices[row.rowId]
          const pinged = outcome !== undefined
          const listId = `melee-models-${row.rowId}`

          return (
            <article
              key={row.rowId}
              className="rounded-xl border border-hairline bg-void p-4"
              style={{ borderLeftWidth: 2, borderLeftColor: accent }}
            >
              <div className="flex flex-wrap items-center gap-3">
                <span
                  title={`${index + 1} 号选手`}
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md font-mono text-xs font-bold tabular-nums"
                  style={{ backgroundColor: `${accent}1f`, color: accent }}
                >
                  {index + 1}
                </span>

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
                  className="min-w-0 flex-1 rounded-lg border border-hairline bg-void px-3.5 py-2.5 text-sm font-medium text-ink outline-none transition-colors focus:border-blaze/60 sm:max-w-56"
                >
                  {providers.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                      {item.needsProxy ? '（需代理）' : ''}
                    </option>
                  ))}
                </select>

                <span className="hidden items-center gap-1.5 font-mono text-[11px] text-dust sm:flex">
                  {provider?.needsProxy ? '需代理' : '国内可直连'}
                </span>

                <button
                  type="button"
                  onClick={() => onRemoveRow(row.rowId)}
                  disabled={rows.length <= MIN_CONTESTANTS}
                  title="移除这名选手"
                  aria-label={`移除 ${index + 1} 号选手`}
                  className="ml-auto flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-dust transition-colors hover:bg-bad/10 hover:text-bad disabled:cursor-not-allowed disabled:opacity-30"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-3">
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <input
                    value={row.modelId}
                    list={models ? listId : undefined}
                    aria-label={`选手 ${index + 1} 模型 ID`}
                    onChange={(event) => {
                      onUpdateRow(row.rowId, { modelId: event.target.value })
                      onClearRowCache(row.rowId)
                    }}
                    placeholder="模型 id，如 deepseek-chat / qwen3:4b"
                    className={cn(INPUT_CLASS, 'font-mono text-[13px]')}
                  />
                  {models ? (
                    <datalist id={listId}>
                      {models.map((modelId) => (
                        <option key={modelId} value={modelId} />
                      ))}
                    </datalist>
                  ) : null}
                </div>

                <button
                  type="button"
                  onClick={() => onFetchModels(row.rowId)}
                  disabled={fetchingRow === row.rowId || busy !== ''}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-hairline px-3.5 py-2.5 text-[13px] font-medium text-ash transition-colors hover:border-dust hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  <RefreshCw
                    className={cn('h-3.5 w-3.5', fetchingRow === row.rowId && 'animate-spin')}
                  />
                  {fetchingRow === row.rowId ? '拉取中…' : models ? `已拉到 ${models.length} 个` : '获取列表'}
                </button>
              </div>

              <div className="mt-3 flex items-center gap-2">
                <span
                  aria-hidden
                  className={cn(
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    pinged ? (outcome.ok ? 'bg-ok' : 'bg-bad') : 'bg-hairline',
                  )}
                />
                <span
                  className={cn(
                    'truncate text-[12px]',
                    pinged ? (outcome.ok ? 'text-ok' : 'text-bad') : 'text-dust',
                  )}
                >
                  {pinged ? outcome.message : '未预检 — 开打时会自动预检'}
                </span>
                {notice ? (
                  <span
                    className={cn(
                      'truncate text-[12px]',
                      notice.kind === 'error' ? 'text-bad' : 'text-dust',
                    )}
                  >
                    · {notice.text}
                  </span>
                ) : null}
              </div>

              <div className="mt-3 flex items-center gap-3 rounded-lg bg-raise/60 px-3 py-2">
                <label className="flex min-w-0 flex-1 items-center gap-2.5">
                  <KeyRound className="h-3.5 w-3.5 shrink-0 text-dust" />
                  <span className="sr-only">选手 {index + 1} API Key</span>
                  <input
                    type="password"
                    value={row.apiKey}
                    onChange={(event) => onUpdateRow(row.rowId, { apiKey: event.target.value })}
                    placeholder={
                      provider?.id === 'ollama'
                        ? '本地 Ollama 无需 Key'
                        : `${provider?.label ?? 'Provider'} API Key`
                    }
                    autoComplete="off"
                    className="min-w-0 flex-1 bg-transparent font-mono text-[13px] text-ink outline-none placeholder:text-dust"
                  />
                </label>
              </div>
            </article>
          )
        })}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={onAddRow}
          className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-hairline px-4 py-2.5 text-[13px] font-semibold text-ash transition-colors hover:border-blaze/50 hover:text-blaze"
        >
          <Plus className="h-4 w-4" />
          加一名选手
        </button>
        <span className="font-mono text-[11px] tabular-nums text-dust">
          {greenCount}/{rows.length} 就绪
        </span>
        <span className="hidden items-center gap-1.5 text-[11px] leading-relaxed text-dust sm:flex">
          <ShieldCheck className="h-3.5 w-3.5" />
          Key 随请求发往本机服务端，不落库、不进日志
        </span>
      </div>
    </section>
  )
}
