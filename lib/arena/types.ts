import type { ContestantStatus } from '../providers/stream.ts'

export type { ContestantStatus }

export type ArenaId = 'soft' | 'hard-code'

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
}

export interface Verdict {
  contestantSlot: number
  scores: Record<string, number>
  passed: number
  total: number
  rationale: string
}

export interface JudgeContext {
  topic: string
  judgeModelId: string
  judgeApiKey: string
}

export interface Arena {
  id: ArenaId
  buildPrompt(topic: string): { system?: string; user: string }
  extractCode(text: string): string | null
  judge(subs: Submission[], ctx: JudgeContext): Promise<Verdict[]>
}
