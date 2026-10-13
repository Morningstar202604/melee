import type { BattleResult } from '../battle/events.ts'
import type { Snapshot } from '../db/repo.ts'

export interface PredictionStats {
  total: number
  hits: number
}

const STATS_KEY = 'melee.predict.stats'
const PICK_PREFIX = 'melee.predict.pick.'
const SCORED_PREFIX = 'melee.predict.scored.'

export const PREDICTION_CHANGE_EVENT = 'melee:predict-change'
export const EMPTY_PREDICTION_STATS: PredictionStats = Object.freeze({ total: 0, hits: 0 })

function readRaw(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 忽略隐私模式等写入失败
  }
}

let statsCache: { raw: string | null; value: PredictionStats } | null = null

/** getSnapshot 需要稳定引用：按原始字符串缓存。 */
export function loadPredictionStats(): PredictionStats {
  const raw = readRaw(STATS_KEY)
  if (statsCache && statsCache.raw === raw) return statsCache.value
  let value: PredictionStats = EMPTY_PREDICTION_STATS
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Partial<PredictionStats>
      if (typeof parsed.total === 'number' && typeof parsed.hits === 'number') {
        value = { total: parsed.total, hits: parsed.hits }
      }
    } catch {
      value = EMPTY_PREDICTION_STATS
    }
  }
  statsCache = { raw, value }
  return value
}

export function loadMyPrediction(battleId: string): string | null {
  const raw = readRaw(PICK_PREFIX + battleId)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as unknown
    return typeof value === 'string' && value ? value : null
  } catch {
    return null
  }
}

export function saveMyPrediction(battleId: string, contestantId: string): void {
  writeJSON(PICK_PREFIX + battleId, contestantId)
}

export function isScored(battleId: string): boolean {
  const raw = readRaw(SCORED_PREFIX + battleId)
  return raw === 'true'
}

export function markScored(battleId: string): void {
  writeJSON(SCORED_PREFIX + battleId, true)
}

export function recordOutcome(hit: boolean): void {
  const stats = loadPredictionStats()
  writeJSON(STATS_KEY, { total: stats.total + 1, hits: stats.hits + (hit ? 1 : 0) })
}

export function notifyPredictionChange(): void {
  try {
    window.dispatchEvent(new Event(PREDICTION_CHANGE_EVENT))
  } catch {
    // 忽略
  }
}

/** 判定赢家（按赛场的公开口径）：硬战场/残局看通过数，其余先看人类投票再看裁判总分。 */
export function winnerSlots(
  arena: string,
  summary: BattleResult | null,
  voteTally: Record<string, number>,
  snapshot: Snapshot | null,
): number[] {
  if (!summary || !snapshot) return []

  const slotByContestantId = new Map(snapshot.contestants.map((row) => [row.id, row.slot]))

  if (arena === 'hard-code' || arena === 'chess') {
    let best = 0
    for (const verdict of summary.verdicts) {
      if (verdict.passed > best) best = verdict.passed
    }
    if (best <= 0) return []
    return summary.verdicts.filter((v) => v.passed === best).map((v) => v.contestantSlot)
  }

  let bestVotes = 0
  const voteWinners: number[] = []
  for (const [contestantId, count] of Object.entries(voteTally)) {
    const slot = slotByContestantId.get(contestantId)
    if (slot === undefined) continue
    if (count > bestVotes) {
      bestVotes = count
      voteWinners.length = 0
      voteWinners.push(slot)
    } else if (count === bestVotes) {
      voteWinners.push(slot)
    }
  }
  if (bestVotes > 0) return voteWinners.sort((a, b) => a - b)

  let bestScore = 0
  const judgeWinners: number[] = []
  for (const verdict of summary.verdicts) {
    const total = Object.values(verdict.scores).reduce((sum, value) => sum + value, 0)
    if (total > bestScore) {
      bestScore = total
      judgeWinners.length = 0
      judgeWinners.push(verdict.contestantSlot)
    } else if (total === bestScore) {
      judgeWinners.push(verdict.contestantSlot)
    }
  }
  return bestScore > 0 ? judgeWinners.sort((a, b) => a - b) : []
}
