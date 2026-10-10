import { softArena } from './soft.ts'
import { hardCodeArena } from './hard-code.ts'
import type { Arena, ArenaId } from './types.ts'

export function getArena(id: ArenaId): Arena {
  if (id === 'soft') return softArena
  if (id === 'hard-code') return hardCodeArena
  throw new Error(`未知赛场：${id}`)
}
