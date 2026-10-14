import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { ARENA_IDS, type ArenaId } from '../arena/types.ts'

export interface ContestantRow {
  rowId: string
  providerId: string
  modelId: string
}

interface LineupState {
  arena: ArenaId
  topicByArena: Record<string, string>
  rows: ContestantRow[]
  setArena: (arena: ArenaId) => void
  setTopic: (arena: ArenaId, topicId: string) => void
  setRows: (updater: (rows: ContestantRow[]) => ContestantRow[]) => void
  reset: () => void
}

let rowSeq = 0
export function makeRowId(): string {
  rowSeq += 1
  return `r${rowSeq.toString(36)}`
}

export function blankRow(providerId: string): ContestantRow {
  return { rowId: makeRowId(), providerId, modelId: '' }
}

const DEFAULT_ROWS = (): ContestantRow[] => [blankRow('ollama'), blankRow('deepseek')]

const LEGACY_KEYS = {
  lineup: 'melee.lineup',
  keys: 'melee.keys',
} as const

/** 单次迁移：旧的手写 localStorage 格式（含 sessionStorage 里的 Key）读进新 store。 */
function readLegacy(): { arena?: ArenaId; topicByArena?: Record<string, string>; rows?: ContestantRow[]; sessionKeys: Record<string, string> } {
  try {
    const sessionKeysRaw = window.sessionStorage.getItem(LEGACY_KEYS.keys)
    const sessionKeys = sessionKeysRaw
      ? Object.fromEntries(
          Object.entries(JSON.parse(sessionKeysRaw) as Record<string, unknown>).filter(
            (entry): entry is [string, string] => typeof entry[1] === 'string' && entry[1].length > 0,
          ),
        )
      : {}

    const raw = window.localStorage.getItem(LEGACY_KEYS.lineup)
    if (!raw) return { sessionKeys }
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const rows = Array.isArray(parsed.rows)
      ? parsed.rows
          .map((entry) => {
            const item = (entry ?? {}) as Record<string, unknown>
            const providerId = typeof item.providerId === 'string' ? item.providerId : ''
            if (!providerId) return null
            return {
              rowId: typeof item.rowId === 'string' && item.rowId ? item.rowId : makeRowId(),
              providerId,
              modelId: typeof item.modelId === 'string' ? item.modelId : '',
            }
          })
          .filter((row): row is ContestantRow => row !== null)
      : []
    return {
      arena: ARENA_IDS.includes(parsed.arena as ArenaId) ? (parsed.arena as ArenaId) : undefined,
      topicByArena:
        parsed.topicByArena && typeof parsed.topicByArena === 'object'
          ? Object.fromEntries(
              Object.entries(parsed.topicByArena as Record<string, unknown>).filter(
                (entry): entry is [string, string] => typeof entry[1] === 'string',
              ),
            )
          : {},
      rows: rows.length >= 2 ? rows : undefined,
      sessionKeys,
    }
  } catch {
    return { sessionKeys: {} }
  }
}

function persistSessionKeys(rowIds: string[], keys: Record<string, string>): void {
  try {
    const payload: Record<string, string> = {}
    for (const rowId of rowIds) {
      const value = keys[rowId]
      if (value) payload[rowId] = value
    }
    if (Object.keys(payload).length === 0) window.sessionStorage.removeItem(LEGACY_KEYS.keys)
    else window.sessionStorage.setItem(LEGACY_KEYS.keys, JSON.stringify(payload))
  } catch {
    // 忽略隐私模式等写入失败
  }
}

export const useLineupStore = create<LineupState>()(
  persist(
    (set) => ({
      arena: 'soft',
      topicByArena: {},
      rows: DEFAULT_ROWS(),
      setArena: (arena) => set({ arena }),
      setTopic: (arena, topicId) => set((state) => ({ topicByArena: { ...state.topicByArena, [arena]: topicId } })),
      setRows: (updater) => set((state) => ({ rows: updater(state.rows) })),
      reset: () => set({ arena: 'soft', topicByArena: {}, rows: DEFAULT_ROWS() }),
    }),
    {
      name: 'melee.lineup.v2',
      version: 1,
      storage: createJSONStorage(() => window.localStorage),
      partialize: (state) => ({ arena: state.arena, topicByArena: state.topicByArena, rows: state.rows }),
    },
  ),
)

/**
 * 把旧格式并入新 store（幂等）。
 * 返回：rowId → API Key（Key 仍只存 sessionStorage，不进 localStorage）。
 */
export function migrateLegacyLineup(): Record<string, string> {
  const legacy = readLegacy()
  const hasLegacyData = Boolean(legacy.arena || legacy.rows || Object.keys(legacy.topicByArena ?? {}).length > 0)
  if (!hasLegacyData) {
    const state = useLineupStore.getState()
    persistSessionKeys(state.rows.map((row) => row.rowId), legacy.sessionKeys)
    return legacy.sessionKeys
  }
  useLineupStore.setState((state) => ({
    arena: legacy.arena ?? state.arena,
    topicByArena: { ...state.topicByArena, ...legacy.topicByArena },
    rows: legacy.rows ?? state.rows,
  }))
  persistSessionKeys((legacy.rows ?? useLineupStore.getState().rows).map((row) => row.rowId), legacy.sessionKeys)
  try {
    window.localStorage.removeItem(LEGACY_KEYS.lineup)
  } catch {
    // 忽略
  }
  return legacy.sessionKeys
}
