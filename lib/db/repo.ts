import { randomUUID } from 'node:crypto'
import { getDb } from './client.ts'

export type BattlePhase =
  | 'pending'
  | 'running'
  | 'judging'
  | 'scoring'
  | 'completed'
  | 'cancelled'
  | 'failed'

export interface NewBattle {
  id?: string
  arena: string
  topic: string
  status?: string
  phase?: BattlePhase
  createdAt?: number
}

export interface BattleRow {
  id: string
  arena: string
  topic: string
  status: string
  phase: BattlePhase
  created_at: number
  completed_at: number | null
  error: string | null
}

export interface NewContestant {
  id?: string
  battleId: string
  slot: number
  providerId: string
  modelId: string
  positionIndex: number
  status: string
  promptTokens?: number | null
  completionTokens?: number | null
  latencyMs?: number | null
  outputText?: string | null
  code?: string | null
  errorMessage?: string | null
}

export interface ContestantRow {
  id: string
  battle_id: string
  slot: number
  provider_id: string
  model_id: string
  position_index: number
  prompt_tokens: number | null
  completion_tokens: number | null
  latency_ms: number | null
  output_text: string | null
  code: string | null
  status: string
  error_message: string | null
}

export interface ContestantPatch {
  promptTokens?: number | null
  completionTokens?: number | null
  latencyMs?: number | null
  outputText?: string | null
  code?: string | null
  status?: string
  errorMessage?: string | null
}

export interface NewVote {
  id?: string
  battleId: string
  contestantId: string
  voterKey: string
  positionIndex: number
  createdAt?: number
}

export interface VoteRow {
  id: string
  battle_id: string
  contestant_id: string
  voter_key: string
  position_index: number
  created_at: number
}

export interface NewJudgement {
  id?: string
  battleId: string
  contestantId: string
  round: number
  judgeModel: string
  positionIndex: number
  scoresJson: string
  rationale: string
  createdAt?: number
}

export interface JudgementRow {
  id: string
  battle_id: string
  contestant_id: string
  round: number
  judge_model: string
  position_index: number
  scores_json: string
  rationale: string
  created_at: number
}

export interface NewSandboxResult {
  id?: string
  contestantId: string
  backend: string
  isolationLevel: string
  language: string
  status: string
  passed: number
  total: number
  timeMs?: number | null
  memoryKb?: number | null
  compileOutput?: string | null
  stdoutTail?: string | null
  degraded: number
}

export interface SandboxResultRow {
  id: string
  contestant_id: string
  backend: string
  isolation_level: string
  language: string
  status: string
  passed: number
  total: number
  time_ms: number | null
  memory_kb: number | null
  compile_output: string | null
  stdout_tail: string | null
  degraded: number
}

export interface Snapshot {
  battle: BattleRow
  contestants: ContestantRow[]
  votes: VoteRow[]
  judgements: JudgementRow[]
  sandboxResults: SandboxResultRow[]
}

let writeQueue: Promise<unknown> = Promise.resolve()

function enqueueWrite<T>(op: () => T): Promise<T> {
  const next = writeQueue.then(op)
  writeQueue = next.catch(() => {})
  return next
}

const now = () => Date.now()

function battleFromRow(row: Record<string, unknown> | undefined): BattleRow | undefined {
  if (!row) return undefined
  return {
    id: String(row.id),
    arena: String(row.arena),
    topic: String(row.topic),
    status: String(row.status),
    phase: String(row.phase) as BattlePhase,
    created_at: Number(row.created_at),
    completed_at: row.completed_at == null ? null : Number(row.completed_at),
    error: row.error == null ? null : String(row.error),
  }
}

function contestantFromRow(row: Record<string, unknown>): ContestantRow {
  return {
    id: String(row.id),
    battle_id: String(row.battle_id),
    slot: Number(row.slot),
    provider_id: String(row.provider_id),
    model_id: String(row.model_id),
    position_index: Number(row.position_index),
    prompt_tokens: row.prompt_tokens == null ? null : Number(row.prompt_tokens),
    completion_tokens: row.completion_tokens == null ? null : Number(row.completion_tokens),
    latency_ms: row.latency_ms == null ? null : Number(row.latency_ms),
    output_text: row.output_text == null ? null : String(row.output_text),
    code: row.code == null ? null : String(row.code),
    status: String(row.status),
    error_message: row.error_message == null ? null : String(row.error_message),
  }
}

function voteFromRow(row: Record<string, unknown>): VoteRow {
  return {
    id: String(row.id),
    battle_id: String(row.battle_id),
    contestant_id: String(row.contestant_id),
    voter_key: String(row.voter_key),
    position_index: Number(row.position_index),
    created_at: Number(row.created_at),
  }
}

function judgementFromRow(row: Record<string, unknown>): JudgementRow {
  return {
    id: String(row.id),
    battle_id: String(row.battle_id),
    contestant_id: String(row.contestant_id),
    round: Number(row.round),
    judge_model: String(row.judge_model),
    position_index: Number(row.position_index),
    scores_json: String(row.scores_json),
    rationale: String(row.rationale),
    created_at: Number(row.created_at),
  }
}

function sandboxResultFromRow(row: Record<string, unknown>): SandboxResultRow {
  return {
    id: String(row.id),
    contestant_id: String(row.contestant_id),
    backend: String(row.backend),
    isolation_level: String(row.isolation_level),
    language: String(row.language),
    status: String(row.status),
    passed: Number(row.passed),
    total: Number(row.total),
    time_ms: row.time_ms == null ? null : Number(row.time_ms),
    memory_kb: row.memory_kb == null ? null : Number(row.memory_kb),
    compile_output: row.compile_output == null ? null : String(row.compile_output),
    stdout_tail: row.stdout_tail == null ? null : String(row.stdout_tail),
    degraded: Number(row.degraded),
  }
}

export async function createBattle(b: NewBattle): Promise<string> {
  const id = b.id ?? randomUUID()
  const createdAt = b.createdAt ?? now()
  return enqueueWrite(() => {
    getDb()
      .prepare(
        'INSERT INTO battles (id, arena, topic, status, phase, created_at, completed_at, error) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)',
      )
      .run(id, b.arena, b.topic, b.status ?? 'pending', b.phase ?? 'pending', createdAt)
    return id
  })
}

export function getBattle(id: string): BattleRow | undefined {
  return battleFromRow(
    getDb().prepare('SELECT * FROM battles WHERE id = ?').get(id) as
      | Record<string, unknown>
      | undefined,
  )
}

export async function insertContestant(c: NewContestant): Promise<string> {
  const id = c.id ?? randomUUID()
  await enqueueWrite(() => {
    getDb()
      .prepare(
        'INSERT INTO contestants (id, battle_id, slot, provider_id, model_id, position_index, prompt_tokens, completion_tokens, latency_ms, output_text, code, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      )
      .run(
        id,
        c.battleId,
        c.slot,
        c.providerId,
        c.modelId,
        c.positionIndex,
        c.promptTokens ?? null,
        c.completionTokens ?? null,
        c.latencyMs ?? null,
        c.outputText ?? null,
        c.code ?? null,
        c.status,
        c.errorMessage ?? null,
      )
  })
  return id
}

export async function updateContestantResult(id: string, patch: ContestantPatch): Promise<void> {
  const sets: string[] = []
  const values: unknown[] = []
  const push = (column: string, value: unknown) => {
    sets.push(`${column} = ?`)
    values.push(value ?? null)
  }
  if ('promptTokens' in patch) push('prompt_tokens', patch.promptTokens)
  if ('completionTokens' in patch) push('completion_tokens', patch.completionTokens)
  if ('latencyMs' in patch) push('latency_ms', patch.latencyMs)
  if ('outputText' in patch) push('output_text', patch.outputText)
  if ('code' in patch) push('code', patch.code)
  if ('status' in patch) push('status', patch.status)
  if ('errorMessage' in patch) push('error_message', patch.errorMessage)
  if (sets.length === 0) return
  await enqueueWrite(() => {
    getDb()
      .prepare(`UPDATE contestants SET ${sets.join(', ')} WHERE id = ?`)
      .run(...(values as (string | number | null)[]), id)
  })
}

export async function updateBattlePhase(
  id: string,
  phase: BattlePhase,
  patch: { error?: string; completedAt?: number } = {},
): Promise<void> {
  const sets: string[] = ['phase = ?', 'status = ?']
  const values: unknown[] = [phase, phase]
  if (patch.completedAt !== undefined) {
    sets.push('completed_at = ?')
    values.push(patch.completedAt)
  }
  if (patch.error !== undefined) {
    sets.push('error = ?')
    values.push(patch.error)
  }
  await enqueueWrite(() => {
    getDb()
      .prepare(`UPDATE battles SET ${sets.join(', ')} WHERE id = ?`)
      .run(...(values as (string | number)[]), id)
  })
}

export async function recordVote(v: NewVote): Promise<void> {
  const id = v.id ?? randomUUID()
  const createdAt = v.createdAt ?? now()
  await enqueueWrite(() => {
    getDb()
      .prepare(
        'INSERT INTO votes (id, battle_id, contestant_id, voter_key, position_index, created_at) VALUES (?, ?, ?, ?, ?, ?)',
      )
      .run(id, v.battleId, v.contestantId, v.voterKey, v.positionIndex, createdAt)
  })
}

export async function recordJudgement(j: NewJudgement): Promise<void> {
  const id = j.id ?? randomUUID()
  const createdAt = j.createdAt ?? now()
  await enqueueWrite(() => {
    getDb()
      .prepare(
        'INSERT INTO judgements (id, battle_id, contestant_id, round, judge_model, position_index, scores_json, rationale, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
      )
      .run(
        id,
        j.battleId,
        j.contestantId,
        j.round,
        j.judgeModel,
        j.positionIndex,
        j.scoresJson,
        j.rationale,
        createdAt,
      )
  })
}

export async function recordSandboxResult(r: NewSandboxResult): Promise<void> {
  const id = r.id ?? randomUUID()
  await enqueueWrite(() => {
    getDb()
      .prepare(
        'INSERT INTO sandbox_results (id, contestant_id, backend, isolation_level, language, status, passed, total, time_ms, memory_kb, compile_output, stdout_tail, degraded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      )
      .run(
        id,
        r.contestantId,
        r.backend,
        r.isolationLevel,
        r.language,
        r.status,
        r.passed,
        r.total,
        r.timeMs ?? null,
        r.memoryKb ?? null,
        r.compileOutput ?? null,
        r.stdoutTail ?? null,
        r.degraded,
      )
  })
}

export function getSnapshot(id: string): Snapshot | undefined {
  const db = getDb()
  const battle = battleFromRow(
    db.prepare('SELECT * FROM battles WHERE id = ?').get(id) as
      | Record<string, unknown>
      | undefined,
  )
  if (!battle) return undefined
  const contestants = (
    db.prepare('SELECT * FROM contestants WHERE battle_id = ? ORDER BY slot').all(id) as Record<
      string,
      unknown
    >[]
  ).map(contestantFromRow)
  const votes = (
    db.prepare('SELECT * FROM votes WHERE battle_id = ? ORDER BY created_at').all(id) as Record<
      string,
      unknown
    >[]
  ).map(voteFromRow)
  const judgements = (
    db
      .prepare('SELECT * FROM judgements WHERE battle_id = ? ORDER BY round, position_index')
      .all(id) as Record<string, unknown>[]
  ).map(judgementFromRow)
  const sandboxResults = (
    db
      .prepare(
        'SELECT * FROM sandbox_results WHERE contestant_id IN (SELECT id FROM contestants WHERE battle_id = ?)',
      )
      .all(id) as Record<string, unknown>[]
  ).map(sandboxResultFromRow)
  return { battle, contestants, votes, judgements, sandboxResults }
}
