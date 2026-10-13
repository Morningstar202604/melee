import { z } from 'zod'
import type { ArenaId, ContestantStatus } from '../arena/types.ts'

export type { ContestantStatus }

const contestantStatusValues = [
  'ok',
  'timeout',
  'rate_limited',
  'auth_failed',
  'provider_error',
  'empty',
] as const satisfies readonly ContestantStatus[]

export const contestantStatusSchema = z.enum(contestantStatusValues)

const arenaIds = ['soft', 'hard-code'] as const satisfies readonly ArenaId[]

export const arenaIdSchema = z.enum(arenaIds)

export const battlePhaseSchema = z.enum([
  'pending',
  'running',
  'judging',
  'scoring',
  'completed',
  'cancelled',
  'failed',
])

export type BattlePhase = z.infer<typeof battlePhaseSchema>

export const contestantStatusLabels: Record<ContestantStatus, string> = {
  ok: '完成',
  timeout: '超时',
  rate_limited: '被限流',
  auth_failed: '鉴权失败',
  provider_error: '调用失败',
  empty: '未返回内容',
}

export const battlePhaseLabels: Record<BattlePhase, string> = {
  pending: '准备中',
  running: '开打中',
  judging: '裁判中',
  scoring: '计分中',
  completed: '已结束',
  cancelled: '已取消',
  failed: '已失败',
}

const usageSchema = z.object({
  prompt: z.number(),
  completion: z.number(),
})

export const verdictSchema = z.object({
  contestantSlot: z.number(),
  scores: z.record(z.string(), z.number()),
  passed: z.number(),
  total: z.number(),
  rationale: z.string(),
})

export const contestantOutcomeSchema = z.object({
  slot: z.number(),
  providerId: z.string(),
  modelId: z.string(),
  status: contestantStatusSchema,
  usage: usageSchema.nullable(),
  latencyMs: z.number(),
  outputLength: z.number(),
  code: z.string().nullable(),
  errorMessage: z.string().nullable(),
})

export const battleResultSchema = z.object({
  battleId: z.string(),
  arena: arenaIdSchema,
  topic: z.string(),
  contestants: z.array(contestantOutcomeSchema),
  verdicts: z.array(verdictSchema),
  judgeAvailable: z.boolean(),
})

export const phaseChangedSchema = z.object({
  type: z.literal('phase.changed'),
  phase: battlePhaseSchema,
})

export const contestantStartedSchema = z.object({
  type: z.literal('contestant.started'),
  slot: z.number(),
  providerId: z.string(),
  modelId: z.string(),
})

export const contestantDeltaSchema = z.object({
  type: z.literal('contestant.delta'),
  slot: z.number(),
  text: z.string(),
})

export const contestantDoneSchema = z.object({
  type: z.literal('contestant.done'),
  slot: z.number(),
  usage: usageSchema.nullable(),
  latencyMs: z.number(),
})

export const contestantFailedSchema = z.object({
  type: z.literal('contestant.failed'),
  slot: z.number(),
  status: contestantStatusSchema,
  message: z.string(),
})

export const judgeDeltaSchema = z.object({
  type: z.literal('judge.delta'),
  text: z.string(),
})

export const judgeDoneSchema = z.object({
  type: z.literal('judge.done'),
  verdicts: z.array(verdictSchema),
  available: z.boolean(),
})

export const sandboxProgressStatusSchema = z.enum(['ok', 'timeout', 'error', 'unavailable'])

export const sandboxResultSchema = z.object({
  type: z.literal('sandbox.result'),
  slot: z.number(),
  status: sandboxProgressStatusSchema,
  passed: z.number(),
  total: z.number(),
  timeMs: z.number().nullable(),
  memoryKb: z.number().nullable(),
})

export const voteTallySchema = z.object({
  type: z.literal('vote.tally'),
  tally: z.record(z.string(), z.number()),
})

export const battleCompletedSchema = z.object({
  type: z.literal('battle.completed'),
  result: battleResultSchema,
})

export const battleFailedSchema = z.object({
  type: z.literal('battle.failed'),
  reason: z.string(),
})

export const battleEventSchema = z.discriminatedUnion('type', [
  phaseChangedSchema,
  contestantStartedSchema,
  contestantDeltaSchema,
  contestantDoneSchema,
  contestantFailedSchema,
  judgeDeltaSchema,
  judgeDoneSchema,
  sandboxResultSchema,
  voteTallySchema,
  battleCompletedSchema,
  battleFailedSchema,
])

export type BattleEvent = z.infer<typeof battleEventSchema>
export type BattleEventName = BattleEvent['type']
export type BattleResult = z.infer<typeof battleResultSchema>
export type ContestantOutcome = z.infer<typeof contestantOutcomeSchema>
export type VerdictValue = z.infer<typeof verdictSchema>

export function parseBattleEvent(input: unknown): BattleEvent {
  return battleEventSchema.parse(input)
}
