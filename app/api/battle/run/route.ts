import { arenaLimits, getArena } from '../../../../lib/arena/index.ts'
import type { ArenaId, ContestantInput } from '../../../../lib/arena/types.ts'
import { allow } from '../../../../lib/battle/ratelimit.ts'
import {
  startBattle,
  type BattleDeps,
  type StartBattleInput,
} from '../../../../lib/battle/runner.ts'
import { createModel, getProvider } from '../../../../lib/providers/registry.ts'
import { logger } from '../../../../lib/log/logger.ts'
import { secureResponse } from '../../../../lib/security.ts'

export const runtime = 'nodejs'

const MAX_TOPIC_LENGTH = 2000
const MAX_API_KEY_LENGTH = 500
const MAX_MODEL_ID_LENGTH = 200

function clientIp(request: Request): string {
  const forwardedFor = request.headers.get('x-forwarded-for')
  if (forwardedFor) {
    const first = forwardedFor.split(',')[0]?.trim()
    if (first) return first
  }
  const realIp = request.headers.get('x-real-ip')?.trim()
  return realIp || 'unknown'
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function parseContestants(raw: unknown): { contestants?: ContestantInput[]; error?: string } {
  if (!Array.isArray(raw)) return { error: 'contestants 必须是数组' }
  if (raw.length < 2) return { error: '至少需要 2 名选手' }
  if (raw.length > 8) return { error: '最多支持 8 名选手' }
  const contestants: ContestantInput[] = []
  for (const [index, entry] of raw.entries()) {
    const item = (entry ?? {}) as Record<string, unknown>
    const slot = typeof item.slot === 'number' && Number.isFinite(item.slot) ? item.slot : index + 1
    const providerId = asString(item.providerId)
    const modelId = asString(item.modelId)
    const apiKey = typeof item.apiKey === 'string' ? item.apiKey.trim() : ''
    if (!providerId || !modelId || !apiKey) {
      return { error: `第 ${index + 1} 名选手缺少 providerId / modelId / apiKey` }
    }
    if (modelId.length > MAX_MODEL_ID_LENGTH) {
      return { error: `第 ${index + 1} 名选手的模型 ID 超出长度上限` }
    }
    if (apiKey.length > MAX_API_KEY_LENGTH) {
      return { error: `第 ${index + 1} 名选手的 API Key 超出长度上限` }
    }
    if (!getProvider(providerId)) {
      return { error: `第 ${index + 1} 名选手的 provider 未注册：${providerId}` }
    }
    contestants.push({ slot, providerId, modelId, apiKey })
  }
  return { contestants }
}

function buildJudgeModelRef(): string | undefined {
  const providerId = asString(process.env.MELEE_JUDGE_PROVIDER_ID)
  const modelId = asString(process.env.MELEE_JUDGE_MODEL_ID)
  if (!modelId) return undefined
  if (providerId && !modelId.includes(':')) return `${providerId}:${modelId}`
  return modelId
}

export async function POST(request: Request) {
  const ip = clientIp(request)
  if (!allow(ip)) {
    logger.warn('限流拒绝', { ip })
    return secureResponse(
      JSON.stringify({ error: '请求过于频繁：每 IP 每分钟最多发起 5 场对战' }),
      { status: 429 },
    )
  }

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return secureResponse(JSON.stringify({ error: '请求体不是合法 JSON' }), { status: 400 })
  }
  const payload = (body ?? {}) as Record<string, unknown>

  const arena = asString(payload.arena)
  if (!arena) {
    return secureResponse(JSON.stringify({ error: 'arena 必填（soft / hard-code）' }), { status: 400 })
  }
  const arenaId = arena as ArenaId
  let arenaDef
  try {
    arenaDef = getArena(arenaId)
  } catch {
    return secureResponse(
      JSON.stringify({ error: 'arena 仅支持 soft / debate / banter / hard-code / chess' }),
      { status: 400 },
    )
  }

  const topic = asString(payload.topic)
  if (!topic) {
    return secureResponse(JSON.stringify({ error: 'topic 必填' }), { status: 400 })
  }
  if (topic.length > MAX_TOPIC_LENGTH) {
    return secureResponse(JSON.stringify({ error: '题面长度超出上限' }), { status: 400 })
  }

  const parsed = parseContestants(payload.contestants)
  if (!parsed.contestants) {
    return secureResponse(JSON.stringify({ error: parsed.error ?? 'contestants 不合法' }), { status: 400 })
  }

  const limits = arenaLimits(arenaDef)
  if (parsed.contestants.length < limits.min || parsed.contestants.length > limits.max) {
    return secureResponse(
      JSON.stringify({ error: `本赛场需要 ${limits.min}~${limits.max} 名选手` }),
      { status: 400 },
    )
  }

  const deps: BattleDeps = {
    arena: arenaDef,
    createModel: (contestant: ContestantInput) => {
      const provider = getProvider(contestant.providerId)
      if (!provider) {
        throw new Error(`未知 provider：${contestant.providerId}`)
      }
      return createModel(provider, contestant.modelId, contestant.apiKey)
    },
  }

  const judgeApiKey = asString(process.env.MELEE_JUDGE_API_KEY)
  const judgeModelRef = buildJudgeModelRef()
  if (judgeApiKey && judgeModelRef) {
    deps.judgeContext = { topic, judgeModelId: judgeModelRef, judgeApiKey }
  }

  try {
    const input: StartBattleInput = { arena: arenaId, topic, contestants: parsed.contestants }
    const battleId = await startBattle(input, deps)
    logger.info('对战已接受', { battleId, arena: arenaId, ip, contestants: parsed.contestants.length })
    return secureResponse(JSON.stringify({ battleId }), { status: 202 })
  } catch (error) {
    logger.error('对战创建失败', { error: error instanceof Error ? error.message : String(error) })
    return secureResponse(JSON.stringify({ error: '对战编排失败，请检查选手配置' }), { status: 500 })
  }
}
