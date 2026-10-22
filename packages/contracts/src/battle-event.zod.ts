// 由 schema/battle-event.schema.json 手工对齐的判别联合。
// 不做自动生成的原因：生成器遇到 allOf + $ref 会退化成 z.any()，失去校验意义。
// 改契约时两边一起改；services/match/tests 里有一致性测试。

import { z } from 'zod'

export const EVENT_TYPES = [
  'match.created',
  'match.started',
  'round.started',
  'turn.started',
  'thought.delta',
  'action.proposed',
  'action.rejected',
  'action.applied',
  'state.changed',
  'narration.line',
  'match.ended',
  'director.cue',
] as const

const visibilitySchema = z.union([
  z.literal('public'),
  z.object({ players: z.array(z.string()).min(1) }).strict(),
])

const baseShape = {
  seq: z.number().int().min(0),
  matchId: z.string().min(1).max(64),
  at: z.string().datetime({ offset: true }),
  visibility: visibilitySchema.optional(),
  player: z.string().min(1).max(64).optional(),
}

const actionSchema = z
  .object({
    kind: z.enum(['ask', 'guess']),
    value: z.string(),
  })
  .strict()

export const matchCreatedSchema = z
  .object({
    ...baseShape,
    type: z.literal('match.created'),
    payload: z
      .object({
        game: z.string(),
        players: z.array(z.string()).min(2),
        seed: z.number().int(),
        config: z.record(z.string(), z.unknown()).optional(),
      })
      .strict(),
  })
  .strict()

export const matchStartedSchema = z
  .object({
    ...baseShape,
    type: z.literal('match.started'),
    payload: z.object({ firstPlayer: z.string() }).strict(),
  })
  .strict()

export const roundStartedSchema = z
  .object({
    ...baseShape,
    type: z.literal('round.started'),
    payload: z
      .object({
        round: z.number().int().min(1),
        activePlayers: z.array(z.string()),
      })
      .strict(),
  })
  .strict()

export const turnStartedSchema = z
  .object({
    ...baseShape,
    type: z.literal('turn.started'),
    payload: z.object({ deadlineMs: z.number().int().min(1) }).strict(),
  })
  .strict()

export const thoughtDeltaSchema = z
  .object({
    ...baseShape,
    player: z.string().min(1).max(64),
    type: z.literal('thought.delta'),
    payload: z.object({ text: z.string().max(4000) }).strict(),
  })
  .strict()

export const actionProposedSchema = z
  .object({
    ...baseShape,
    player: z.string().min(1).max(64),
    type: z.literal('action.proposed'),
    payload: z.object({ action: actionSchema, raw: z.string().max(4000) }).strict(),
  })
  .strict()

export const actionRejectedSchema = z
  .object({
    ...baseShape,
    player: z.string().min(1).max(64),
    type: z.literal('action.rejected'),
    payload: z.object({ reason: z.string().max(500), retryable: z.boolean() }).strict(),
  })
  .strict()

export const actionAppliedSchema = z
  .object({
    ...baseShape,
    player: z.string().min(1).max(64),
    type: z.literal('action.applied'),
    payload: z
      .object({
        action: actionSchema,
        effects: z.array(z.record(z.string(), z.unknown())),
      })
      .strict(),
  })
  .strict()

export const stateChangedSchema = z
  .object({
    ...baseShape,
    type: z.literal('state.changed'),
    payload: z
      .object({
        publicState: z.record(z.string(), z.unknown()),
        deltas: z.array(z.record(z.string(), z.unknown())),
      })
      .strict(),
  })
  .strict()

export const narrationLineSchema = z
  .object({
    ...baseShape,
    type: z.literal('narration.line'),
    payload: z
      .object({
        text: z.string().max(140),
        tone: z.enum(['neutral', 'tension', 'reveal', 'turn']).optional(),
        refs: z.array(z.number().int().min(0)),
      })
      .strict(),
  })
  .strict()

export const matchEndedSchema = z
  .object({
    ...baseShape,
    type: z.literal('match.ended'),
    payload: z
      .object({
        winner: z.string().nullable(),
        reason: z.string().max(200),
        finalState: z.record(z.string(), z.unknown()),
      })
      .strict(),
  })
  .strict()

export const directorCueSchema = z
  .object({
    ...baseShape,
    type: z.literal('director.cue'),
    payload: z
      .object({
        shot: z.enum(['wide', 'close', 'thought', 'reveal', 'slowmo']),
        target: z.string().nullable().optional(),
        durationMs: z.number().int().min(0),
        trigger: z.string().max(200).optional(),
      })
      .strict(),
  })
  .strict()

export const battleEventSchema = z.discriminatedUnion('type', [
  matchCreatedSchema,
  matchStartedSchema,
  roundStartedSchema,
  turnStartedSchema,
  thoughtDeltaSchema,
  actionProposedSchema,
  actionRejectedSchema,
  actionAppliedSchema,
  stateChangedSchema,
  narrationLineSchema,
  matchEndedSchema,
  directorCueSchema,
])

export type BattleEvent = z.infer<typeof battleEventSchema>
export type EventType = BattleEvent['type']
