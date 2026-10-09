import { softArena } from './soft.ts'
import type { Arena, ArenaId } from './types.ts'

export function getArena(id: ArenaId): Arena {
  if (id === 'soft') return softArena
  throw new Error('hard-code 战场尚未实现（Task 10）')
}
