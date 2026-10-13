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
  dbContestantId?: string
}

export interface Verdict {
  contestantSlot: number
  scores: Record<string, number>
  passed: number
  total: number
  rationale: string
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

export interface Arena {
  id: ArenaId
  buildPrompt(topic: string): { system?: string; user: string }
  extractCode(text: string): string | null
  judge(subs: Submission[], ctx: JudgeContext): Promise<Verdict[]>
}
