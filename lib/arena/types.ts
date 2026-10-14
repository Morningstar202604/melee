import type { ModelMessage } from 'ai'
import type { ContestantStatus } from '../providers/stream.ts'

export type { ContestantStatus }

export type ArenaId = 'soft' | 'debate' | 'banter' | 'hard-code' | 'chess'

export const ARENA_IDS: readonly ArenaId[] = ['soft', 'debate', 'banter', 'hard-code', 'chess']

export interface ContestantInput {
  slot: number
  providerId: string
  modelId: string
  apiKey: string
}

export interface Submission {
  contestant: ContestantInput
  text: string
  code: string | null
  usage: { prompt: number; completion: number } | null
  latencyMs: number
  status: ContestantStatus
  errorMessage?: string
  dbContestantId?: string
}

export interface Verdict {
  contestantSlot: number
  scores: Record<string, number>
  passed: number
  total: number
  rationale: string
}

export interface JudgeAxis {
  key: string
  label: string
  description: string
}

export type SandboxProgressStatus = 'ok' | 'timeout' | 'error' | 'unavailable'

export interface SandboxProgress {
  slot: number
  status: SandboxProgressStatus
  passed: number
  total: number
  timeMs: number | null
  memoryKb: number | null
}

export interface JudgeContext {
  topic: string
  judgeModelId: string
  judgeApiKey: string
  onSandboxResult?: (progress: SandboxProgress) => void
}

export interface TurnRecord {
  round: number
  text: string
  reasoning?: string
}

export interface TurnPlan {
  totalRounds: number
  roundLabel: (round: number) => string
  /**
   * 官方对话模式：把"角色 + 历史发言 + 本轮要求"编成标准 ModelMessage 数组
   * （user/assistant 交替），让 provider 的会话处理与提示缓存能生效。
   */
  buildTurnMessages: (
    topic: string,
    round: number,
    slot: number,
    own: TurnRecord[],
    others: Array<{ slot: number; turns: TurnRecord[] }>,
  ) => { system?: string; messages: ModelMessage[] }
}

export interface Arena {
  id: ArenaId
  buildPrompt(topic: string): { system?: string; user: string }
  extractCode(text: string): string | null
  judge(subs: Submission[], ctx: JudgeContext): Promise<Verdict[]>
  plan?: TurnPlan
  axes?: JudgeAxis[]
  /** false = 判定不依赖 LLM 裁判（沙箱 / 残局校验），没有裁判配置也要执行 */
  judgeNeedsLlm?: boolean
  minContestants?: number
  maxContestants?: number
  turnTimeoutMs?: number
  battleTimeoutMs?: number
  maxOutputTokens?: number
}

export const DEFAULT_MIN_CONTESTANTS = 2
export const DEFAULT_MAX_CONTESTANTS = 8
export const DEFAULT_TURN_TIMEOUT_MS = 120000
export const DEFAULT_BATTLE_TIMEOUT_MS = 300000

/** 多轮模式把整份发言序列化进 output_text；单轮模式保持纯文本。 */
export function serializeTurns(turns: TurnRecord[]): string {
  return JSON.stringify(turns)
}

export function parseTurns(text: string | null, multiRound: boolean): TurnRecord[] {
  if (!text) return []
  if (!multiRound) return [{ round: 1, text }]
  try {
    const parsed = JSON.parse(text) as unknown
    if (Array.isArray(parsed)) {
      const turns = parsed
        .map((entry) => {
          const item = (entry ?? {}) as Record<string, unknown>
          const round = typeof item.round === 'number' ? item.round : 1
          const value = typeof item.text === 'string' ? item.text : ''
          const reasoning = typeof item.reasoning === 'string' ? item.reasoning : undefined
          return { round, text: value, ...(reasoning ? { reasoning } : {}) }
        })
        .filter((turn) => turn.text.length > 0 || (turn.reasoning?.length ?? 0) > 0)
      if (turns.length > 0) return turns
    }
  } catch {
    // 不是 JSON：视为纯文本（旧数据或降级）
  }
  return [{ round: 1, text }]
}
