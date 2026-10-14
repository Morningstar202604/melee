import { APICallError, NoObjectGeneratedError, Output, generateText, type LanguageModel } from 'ai'
import { z } from 'zod'
import type { JudgeAxis, JudgeContext, Submission, Verdict } from '../arena/types.ts'
import { SOFT_AXES } from '../arena/axes.ts'
import { resolveModelRef } from '../providers/registry.ts'
import { redact } from '../log/redact.ts'
import { logger } from '../log/logger.ts'

const DEFAULT_RATIONALE = '未提供理由'
const DEFAULT_SCORE = 0
const SCORE_PRECISION = 10

export const DEFAULT_JUDGE_AXES: JudgeAxis[] = SOFT_AXES

const judgeVerdictSchema = z
  .object({
    label: z.string(),
    rationale: z.string().catch(DEFAULT_RATIONALE),
  })
  .catchall(z.number().catch(DEFAULT_SCORE))

const judgePayloadSchema = z.object({
  verdicts: z.array(judgeVerdictSchema),
})

export interface JudgeVerdict {
  label: string
  scores: Record<string, number>
  rationale: string
}

export interface ParsedJudgement {
  verdicts: JudgeVerdict[]
}

export interface MappedVerdict extends JudgeVerdict {
  slot: number
}

export interface JudgeCallInput {
  system: string
  user: string
}

export type JudgeCall = (input: JudgeCallInput) => Promise<string>

export interface JudgeOptions {
  topic: string
  submissions: Submission[]
  judgeModel?: LanguageModel
  signal?: AbortSignal
  callJudge?: JudgeCall
  axes?: JudgeAxis[]
}

export interface JudgeOutcome {
  verdicts: Verdict[]
  roundsAgreed: boolean
  unavailable: boolean
  raw?: string
}

export const DEFAULT_JUDGE_PROVIDER_ID = 'deepseek'
export const DEFAULT_JUDGE_MODEL_ID = 'deepseek-chat'

export function buildJudgeSystemPrompt(axes: JudgeAxis[]): string {
  const axisLines = axes.map(
    (axis) => `- ${axis.key}（${axis.label}）：${axis.description}`,
  )
  const scoreShape = axes.map((axis) => `"${axis.key}":8`).join(',')
  return [
    '你是「AI 大乱斗」的现场解说裁判。你的职责不是给选手定生死，而是把场上局势讲给观众听：' +
      '口吻要像一位嘴贫的解说员，有梗、犀利但不下作，绝不人身攻击，也绝不透露选手的真实身份（他们已被匿名化）。',
    '',
    '评分口径（每项都是 0-10 的整数）：',
    ...axisLines,
    '',
    '输出铁律（违反任何一条都视为无效输出）：',
    '1. 只输出一个 JSON 对象，除此之外一个字都不要有：不用 Markdown 代码围栏，不加任何解释、寒暄或前后缀。',
    `2. JSON 结构固定为：{"verdicts":[{"label":"A",${scoreShape},"rationale":"一句吐槽式点评"}]}`,
    '3. verdicts 必须为每一位参赛者各出一条，label 只能使用题目里给出的匿名标签（如 A/B/C），顺序自定。',
    `4. 每项分数必须是 0-10 的整数（键只能是 ${axes.map((axis) => axis.key).join(' / ')}）；rationale 是一句 50 字以内的中文大白话，要能被主持人直接念给观众听。`,
    '5. 每位参赛者独立评判，严禁受出场顺序影响：顺序靠前不代表更强，靠后不代表更弱。',
  ].join('\n')
}

export function resolveJudgeModel(ctx: JudgeContext): LanguageModel {
  const ref = ctx.judgeModelId.trim() || DEFAULT_JUDGE_MODEL_ID
  return resolveModelRef(ref, ctx.judgeApiKey)
}

function extractScores(entry: Record<string, unknown>): Record<string, number> {
  const scores: Record<string, number> = {}
  for (const [key, value] of Object.entries(entry)) {
    if (key === 'label' || key === 'rationale') continue
    if (typeof value === 'number' && Number.isFinite(value)) scores[key] = value
  }
  return scores
}

function jsonCandidates(raw: string): unknown[] {
  const attempts: unknown[] = []
  const push = (text: string) => {
    try {
      attempts.push(JSON.parse(text))
    } catch {
      return
    }
  }

  push(raw.trim())

  const unfenced = raw
    .split(/\r?\n/)
    .filter((line) => !/^\s*```/.test(line))
    .join('\n')
    .trim()
  push(unfenced)

  const first = raw.indexOf('{')
  const last = raw.lastIndexOf('}')
  if (first >= 0 && last > first) {
    push(raw.slice(first, last + 1))
  }

  return attempts
}

export function parseJudgeOutput(raw: string): ParsedJudgement | null {
  for (const candidate of jsonCandidates(raw)) {
    const parsed = judgePayloadSchema.safeParse(candidate)
    if (!parsed.success) continue
    const verdicts: JudgeVerdict[] = []
    for (const entry of parsed.data.verdicts) {
      const record = entry as Record<string, unknown>
      const label = typeof record.label === 'string' ? record.label : ''
      if (!label) continue
      verdicts.push({
        label,
        scores: extractScores(record),
        rationale: typeof record.rationale === 'string' ? record.rationale : DEFAULT_RATIONALE,
      })
    }
    if (verdicts.length > 0) return { verdicts }
  }
  return null
}

function labelForIndex(index: number): string {
  let rest = index
  let label = ''
  do {
    label = String.fromCharCode(65 + (rest % 26)) + label
    rest = Math.floor(rest / 26) - 1
  } while (rest >= 0)
  return label
}

function totalScore(verdict: MappedVerdict): number {
  return Object.values(verdict.scores).reduce((sum, value) => sum + value, 0)
}

function rankSlots(round: MappedVerdict[]): number[] {
  return [...round]
    .sort((a, b) => totalScore(b) - totalScore(a) || a.slot - b.slot)
    .map((verdict) => verdict.slot)
}

export function roundsAgreed(a: MappedVerdict[], b: MappedVerdict[]): boolean {
  if (a.length !== b.length) return false
  const rankingA = rankSlots(a)
  const rankingB = rankSlots(b)
  return rankingA.every((slot, index) => slot === rankingB[index])
}

interface RoundAssignment {
  label: string
  slot: number
}

function buildJudgeUser(topic: string, order: Submission[], labels: RoundAssignment[]): string {
  const blocks = order.map((item, index) => {
    const label = labels[index]?.label ?? ''
    return [
      `[${label}] 参赛者 ${label} 的作答如下：`,
      item.text,
      `（参赛者 ${label} 作答结束）`,
    ].join('\n')
  })
  return [
    `本场题目：${topic}`,
    '',
    '下面是被匿名化后的参赛作答。出场顺序只是呈现顺序，不代表水平高低；' +
      '请只依据作答内容评判，不要推测、也不要透露参赛者的真实身份。',
    '',
    ...blocks,
    '',
    '请严格按 system 中的输出铁律给出 JSON。',
  ].join('\n')
}

function mapRound(parsed: ParsedJudgement | null, labels: RoundAssignment[]): MappedVerdict[] | null {
  if (!parsed || parsed.verdicts.length === 0) return null
  const slotByLabel = new Map(labels.map((item) => [item.label, item.slot]))
  const seen = new Set<number>()
  const mapped: MappedVerdict[] = []
  for (const verdict of parsed.verdicts) {
    const slot = slotByLabel.get(verdict.label)
    if (slot === undefined || seen.has(slot)) continue
    seen.add(slot)
    mapped.push({ slot, label: verdict.label, scores: verdict.scores, rationale: verdict.rationale })
  }
  return mapped.length > 0 ? mapped : null
}

function average(total: number, count: number): number {
  return Math.round((total / count) * SCORE_PRECISION) / SCORE_PRECISION
}

function toVerdicts(rounds: MappedVerdict[][]): Verdict[] {
  const slots = [...new Set(rounds.flatMap((round) => round.map((verdict) => verdict.slot)))].sort(
    (a, b) => a - b,
  )
  return slots.map((slot) => {
    const entries = rounds.flat().filter((verdict) => verdict.slot === slot)
    const keys = [...new Set(entries.flatMap((entry) => Object.keys(entry.scores)))]
    const scores: Record<string, number> = {}
    for (const key of keys) {
      const values = entries
        .map((entry) => entry.scores[key])
        .filter((value): value is number => typeof value === 'number')
      if (values.length === 0) continue
      scores[key] = average(
        values.reduce((sum, value) => sum + value, 0),
        values.length,
      )
    }
    const rationale = entries
      .map((verdict) => verdict.rationale)
      .filter((text) => text.length > 0)
      .join(' ｜ ')
    return {
      contestantSlot: slot,
      scores,
      passed: 0,
      total: 0,
      rationale: rationale || DEFAULT_RATIONALE,
    }
  })
}

/** provider 不支持结构化输出（或模型没按 schema 输出）时，退回纯文本 + 宽松解析。 */
function isStructuredOutputUnsupported(error: unknown): boolean {
  if (NoObjectGeneratedError.isInstance(error)) return true
  if (APICallError.isInstance(error)) {
    const code = error.statusCode
    return code === 400 || code === 404 || code === 422
  }
  return false
}

function defaultJudgeCall(opts: JudgeOptions, axes: JudgeAxis[]): JudgeCall {
  const model = opts.judgeModel
  if (!model) {
    throw new Error('裁判模型缺失：runJudge 需要 judgeModel，或可注入的 callJudge')
  }
  const system = buildJudgeSystemPrompt(axes)
  return async ({ user }) => {
    try {
      const result = await generateText({
        model,
        system,
        prompt: user,
        maxRetries: 0,
        abortSignal: opts.signal,
        output: Output.object({ schema: judgePayloadSchema }),
      })
      return JSON.stringify(result.output)
    } catch (error) {
      if (!isStructuredOutputUnsupported(error)) throw error
      logger.debug('裁判模型不支持结构化输出，退回纯文本解析', {
        detail: redact(error instanceof Error ? error.message : String(error)),
      })
      const fallback = await generateText({
        model,
        system,
        prompt: user,
        maxRetries: 0,
        abortSignal: opts.signal,
      })
      return fallback.text
    }
  }
}

function deterministicRotate<T>(input: T[]): T[] {
  if (input.length <= 1) return [...input]
  return [...input.slice(1), input[0]]
}

export async function runJudge(opts: JudgeOptions): Promise<JudgeOutcome> {
  const axes = opts.axes && opts.axes.length > 0 ? opts.axes : DEFAULT_JUDGE_AXES
  const system = buildJudgeSystemPrompt(axes)
  const raws: string[] = []
  try {
    const active = opts.submissions.filter((item) => item.text.trim().length > 0)
    if (active.length === 0) return { verdicts: [], roundsAgreed: false, unavailable: true }

    const call = opts.callJudge ?? defaultJudgeCall(opts, axes)

    if (active.length === 1) {
      const labels: RoundAssignment[] = [{ label: labelForIndex(0), slot: active[0].contestant.slot }]
      const text = await call({ system, user: buildJudgeUser(opts.topic, active, labels) })
      raws.push(text)
      const mapped = mapRound(parseJudgeOutput(text), labels)
      const rounds = mapped ? [mapped] : []
      if (rounds.length === 0) {
        return { verdicts: [], roundsAgreed: false, unavailable: true, raw: raws.join('\n') }
      }
      return { verdicts: toVerdicts(rounds), roundsAgreed: false, unavailable: false, raw: raws.join('\n') }
    }

    const rounds: MappedVerdict[][] = []
    const orders = [active, [...active].reverse(), deterministicRotate(active)]
    for (const order of orders) {
      const labels: RoundAssignment[] = order.map((item, index) => ({
        label: labelForIndex(index),
        slot: item.contestant.slot,
      }))
      const text = await call({ system, user: buildJudgeUser(opts.topic, order, labels) })
      raws.push(text)
      const mapped = mapRound(parseJudgeOutput(text), labels)
      if (mapped) rounds.push(mapped)
    }

    if (rounds.length === 0) {
      return { verdicts: [], roundsAgreed: false, unavailable: true, raw: raws.join('\n') }
    }

    const deterministicAgreed = rounds.length >= 2 ? roundsAgreed(rounds[0], rounds[1]) : false

    return {
      verdicts: toVerdicts(rounds),
      roundsAgreed: deterministicAgreed,
      unavailable: false,
      raw: raws.join('\n'),
    }
  } catch (error) {
    logger.error('裁判阶段失败', { error: error instanceof Error ? error.message : String(error) })
    return { verdicts: [], roundsAgreed: false, unavailable: true }
  }
}
