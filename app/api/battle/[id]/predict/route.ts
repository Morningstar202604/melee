import { spectatorLimiter } from '../../../../../lib/battle/ratelimit.ts'
import { clientIp, rateLimitedResponse, readJsonCapped } from '../../../../../lib/http/request.ts'
import { getBattleHandle } from '../../../../../lib/battle/registry.ts'
import { getSnapshot, recordPrediction } from '../../../../../lib/db/repo.ts'
import { redact } from '../../../../../lib/log/redact.ts'
import { logger } from '../../../../../lib/log/logger.ts'
import { secureResponse } from '../../../../../lib/security.ts'

export const runtime = 'nodejs'

const MAX_BODY_BYTES = 4 * 1024
const MAX_VOTER_KEY_LENGTH = 128

interface PredictBody {
  contestantId?: unknown
  voterKey?: unknown
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function tallyFrom(rows: Array<{ contestant_id: string }>): Record<string, number> {
  const tally: Record<string, number> = {}
  for (const row of rows) {
    tally[row.contestant_id] = (tally[row.contestant_id] ?? 0) + 1
  }
  return tally
}

export async function POST(request: Request, ctx: { params: Promise<{ id: string }> }) {
  const ip = clientIp(request)
  if (!spectatorLimiter.allow(ip)) {
    return rateLimitedResponse(spectatorLimiter, '次押注')
  }

  const { id } = await ctx.params

  const read = await readJsonCapped<PredictBody>(request, MAX_BODY_BYTES)
  if (!read.ok) return read.response
  const body = read.body ?? {}

  const contestantId = asString(body.contestantId)
  const voterKey = asString(body.voterKey)
  if (!contestantId || !voterKey) {
    return secureResponse(JSON.stringify({ error: 'contestantId 与 voterKey 必填' }), {
      status: 400,
    })
  }
  if (voterKey.length > MAX_VOTER_KEY_LENGTH) {
    return secureResponse(
      JSON.stringify({ error: `voterKey 超出长度上限（${MAX_VOTER_KEY_LENGTH}）` }),
      { status: 400 },
    )
  }

  const snapshot = getSnapshot(id)
  if (!snapshot) {
    return secureResponse(JSON.stringify({ error: '对战不存在' }), { status: 404 })
  }
  if (['completed', 'cancelled', 'failed'].includes(snapshot.battle.phase)) {
    return secureResponse(JSON.stringify({ error: '对战已结束，收盘不再接受押注' }), { status: 409 })
  }
  const contestant = snapshot.contestants.find((c) => c.id === contestantId)
  if (!contestant) {
    return secureResponse(JSON.stringify({ error: '选手不存在于该对战' }), { status: 404 })
  }

  try {
    await recordPrediction({
      battleId: id,
      contestantId,
      voterKey,
      positionIndex: contestant.position_index,
    })
  } catch (error) {
    const raw = error instanceof Error ? error.message : String(error)
    const message = redact(raw)
    if (/UNIQUE constraint failed/i.test(raw)) {
      return secureResponse(JSON.stringify({ error: '同一场对战每人只能押一注' }), { status: 409 })
    }
    logger.error('押注落库失败', { battleId: id, error: message })
    return secureResponse(JSON.stringify({ error: '押注落库失败，请稍后重试' }), { status: 500 })
  }

  const handle = getBattleHandle(id)
  if (handle) {
    const latest = getSnapshot(id)
    handle.emit({ type: 'prediction.tally', tally: tallyFrom(latest?.predictions ?? []) })
  }
  return secureResponse(JSON.stringify({ ok: true }))
}
