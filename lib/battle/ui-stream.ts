import type { UIMessage } from 'ai'
import { parseBattleEvent, type BattleEvent } from './events.ts'

/** 官方 UIMessage 数据部件协议：战况流就是一条带 battle 数据部件的消息。 */
export type BattleUIMessage = UIMessage<never, { battle: BattleEvent }>

export const BATTLE_DATA_PART = 'data-battle' as const

/** 客户端解包：只认官方数据部件，内部数据仍由 zod 契约校验。 */
export function unwrapBattleChunk(raw: unknown): BattleEvent | null {
  if (!raw || typeof raw !== 'object') return null
  const chunk = raw as { type?: unknown; data?: unknown }
  if (chunk.type !== BATTLE_DATA_PART) return null
  try {
    return parseBattleEvent(chunk.data)
  } catch {
    return null
  }
}
