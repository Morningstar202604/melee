import { softArena } from './soft.ts'
import { debateArena } from './debate.ts'
import { banterArena } from './banter.ts'
import { hardCodeArena } from './hard-code.ts'
import { chessArena } from './chess.ts'
import { DEFAULT_MAX_CONTESTANTS, DEFAULT_MIN_CONTESTANTS, type Arena, type ArenaId } from './types.ts'

const ARENAS: Record<ArenaId, Arena> = {
  soft: softArena,
  debate: debateArena,
  banter: banterArena,
  'hard-code': hardCodeArena,
  chess: chessArena,
}

export function getArena(id: ArenaId): Arena {
  const arena = ARENAS[id]
  if (!arena) throw new Error(`未知赛场：${id}`)
  return arena
}

export function arenaLimits(arena: Arena): { min: number; max: number } {
  return {
    min: arena.minContestants ?? DEFAULT_MIN_CONTESTANTS,
    max: arena.maxContestants ?? DEFAULT_MAX_CONTESTANTS,
  }
}
