import { generateText, type LanguageModel } from 'ai'
import { z } from 'zod'
import type { JudgeContext, Submission, Verdict } from '../arena/types.ts'
import { createModel, getProvider } from '../providers/registry.ts'

const DEFAULT_RATIONALE = '未提供理由'
const DEFAULT_SCORE = 0
const SCORE_PRECISION = 10

const judgeVerdictSchema = z.object({
  label: z.string(),
  logic: z.number().catch(DEFAULT_SCORE),
  evidence: z.number().catch(DEFAULT_SCORE),
  relevance: z.number().catch(DEFAULT_SCORE),
  rationale: z.string().catch(DEFAULT_RATIONALE),
})

const judgePayloadSchema = z.object({
  verdicts: z.array(judgeVerdictSchema),
})

export interface JudgeVerdict {
  label: string
  logic: number
  evidence: number
  relevance: number
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
}

export interface JudgeOutcome {
  verdicts: Verdict[]
  roundsAgreed: boolean
  unavailable: boolean
  raw?: string
}

export const DEFAULT_JUDGE_PROVIDER_ID = 'deepseek'
export const DEFAULT_JUDGE_MODEL_ID = 'deepseek-chat'

const JUDGE_SYSTEM_PROMPT = [
  '你是「AI 大乱斗」的现场解说裁判。你的职责不是给选手定生死，而是把场上局势讲给观众听：' +
    '口吻要像一位嘴贫的解说员，有梗、犀利但不下作，绝不人身攻击，也绝不透露选手的真实身份（他们已被匿名化）。',
  '',
  '评分口径（每项都是 0-10 的整数）：',
  '- logic：逻辑自洽度。立论与结论是否自洽，有没有自相矛盾、循环论证。',
  '- evidence：论据与细节充分度。有没有具体例子、数据或细节支撑，还是空喊口号。',
  '- relevance：切题程度。有没有正面回应题目，还是绕圈、答非所问。',
  '',
  '输出铁律（违反任何一条都视为无效输出）：',
  '1. 只输出一个 JSON 对象，除此之外一个字都不要有：不用 Markdown 代码围栏，不加任何解释、寒暄或前后缀。',
  '2. JSON 结构固定为：{"verdicts":[{"label":"A","logic":8,"evidence":7,"relevance":9,"rationale":"一句吐槽式点评"}]}',
  '3. verdicts 必须为每一位参赛者各出一条，label 只能使用题目里给出的匿名标签（如 A/B/C），顺序自定。',
  '4. 三项分数必须是 0-10 的整数；rationale 是一句 50 字以内的中文大白话，要能被主持人直接念给观众听。',
  '5. 每位参赛者独立评判，严禁受出场顺序影响：顺序靠前不代表更强，靠后不代表更弱。',
].join('\n')

export function resolveJudgeModel(ctx: JudgeContext): LanguageModel {
  const ref = ctx.judgeModelId.trim() || DEFAULT_JUDGE_MODEL_ID
  const separator = ref.indexOf(':')
  if (separator > 0) {
    const def = getProvider(ref.slice(0, separator))
    if (def) return createModel(def, ref.slice(separator + 1), ctx.judgeApiKey)
  }
  const fallback = getProvider(DEFAULT_JUDGE_PROVIDER_ID)
  if (!fallback) {
    throw new Error(`裁判默认 provider「${DEFAULT_JUDGE_PROVIDER_ID}」未在选手池中注册`)
  }
  return createModel(fallback, ref, ctx.judgeApiKey)
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
    if (parsed.success) return { verdicts: parsed.data.verdicts }
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
  return verdict.logic + verdict.evidence + verdict.relevance
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
    mapped.push({ slot, ...verdict })
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
    const rationale = entries
      .map((verdict) => verdict.rationale)
      .filter((text) => text.length > 0)
      .join(' ｜ ')
    return {
      contestantSlot: slot,
      scores: {
        logic: average(
          entries.reduce((sum, verdict) => sum + verdict.logic, 0),
          entries.length,
        ),
        evidence: average(
          entries.reduce((sum, verdict) => sum + verdict.evidence, 0),
          entries.length,
        ),
        relevance: average(
          entries.reduce((sum, verdict) => sum + verdict.relevance, 0),
          entries.length,
        ),
      },
      passed: 0,
      total: 0,
      rationale: rationale || DEFAULT_RATIONALE,
    }
  })
}

function defaultJudgeCall(opts: JudgeOptions): JudgeCall {
  const model = opts.judgeModel
  if (!model) {
    throw new Error('裁判模型缺失：runJudge 需要 judgeModel，或可注入的 callJudge')
  }
  return async ({ system, user }) => {
    const result = await generateText({
      model,
      system,
      prompt: user,
      maxRetries: 0,
      abortSignal: opts.signal,
    })
    return result.text
  }
}

export async function runJudge(opts: JudgeOptions): Promise<JudgeOutcome> {
  const raws: string[] = []
  try {
    const active = opts.submissions.filter((item) => item.text.trim().length > 0)
    if (active.length === 0) return { verdicts: [], roundsAgreed: false, unavailable: true }

    const call = opts.callJudge ?? defaultJudgeCall(opts)
    const rounds: MappedVerdict[][] = []
    for (const order of [active, [...active].reverse()]) {
      const labels: RoundAssignment[] = order.map((item, index) => ({
        label: labelForIndex(index),
        slot: item.contestant.slot,
      }))
      const text = await call({ system: JUDGE_SYSTEM_PROMPT, user: buildJudgeUser(opts.topic, order, labels) })
      raws.push(text)
      const mapped = mapRound(parseJudgeOutput(text), labels)
      if (mapped) rounds.push(mapped)
    }

    if (rounds.length === 0) {
      return { verdicts: [], roundsAgreed: false, unavailable: true, raw: raws.join('\n') }
    }

    return {
      verdicts: toVerdicts(rounds),
      roundsAgreed: rounds.length === 2 ? roundsAgreed(rounds[0], rounds[1]) : false,
      unavailable: false,
      raw: raws.join('\n'),
    }
  } catch {
    return { verdicts: [], roundsAgreed: false, unavailable: true }
  }
}
