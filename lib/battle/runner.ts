import { randomUUID } from 'node:crypto'
import type { LanguageModel, ModelMessage } from 'ai'
import type {
  Arena,
  ArenaId,
  ContestantInput,
  ContestantStatus,
  JudgeContext,
  Submission,
  Verdict,
} from '../arena/types.ts'
import {
  createBattle,
  insertContestant,
  updateBattlePhase,
  updateContestantResult,
} from '../db/repo.ts'
import { redact } from '../log/redact.ts'
import { streamModel } from '../providers/stream.ts'
import {
  battlePhaseLabels,
  contestantStatusLabels,
  type BattlePhase,
  type BattleResult,
} from './events.ts'
import {
  createBattleHandle,
  getBattleHandle,
  registerBattle,
  unregisterBattle,
  type BattleHandle,
} from './registry.ts'

const DEFAULT_TIMEOUT_MS = 120000
const RECYCLE_GRACE_MS = 60000

export interface StartBattleInput {
  arena: ArenaId
  topic: string
  contestants: ContestantInput[]
}

export interface BattleDeps {
  arena: Arena
  createModel: (c: ContestantInput) => LanguageModel
  judgeContext?: JudgeContext
  timeoutMs?: number
  now?: () => number
}

interface ContestantRun {
  contestant: ContestantInput
  contestantId: string
  submission: Submission | null
  status: ContestantStatus
  errorMessage: string | null
}

interface RunContext {
  deps: BattleDeps
  handle: BattleHandle
  battleId: string
  prompt: { system?: string; user: string }
  contestantIds: Map<number, string>
  timeoutMs: number
}

export function startBattle(input: StartBattleInput, deps: BattleDeps): string {
  const battleId = randomUUID()
  const handle = createBattleHandle(battleId)
  registerBattle(battleId, handle)
  void runBattle(battleId, input, deps, handle).finally(() => scheduleRecycle(battleId))
  return battleId
}

function scheduleRecycle(battleId: string): void {
  const timer = setTimeout(() => {
    unregisterBattle(battleId)
  }, RECYCLE_GRACE_MS)
  ;(timer as unknown as { unref?: () => void }).unref?.()
}

export function cancelBattle(id: string): boolean {
  const handle = getBattleHandle(id)
  if (!handle || handle.cancelled || handle.settled) return false
  handle.cancel()
  return true
}

async function runBattle(
  battleId: string,
  input: StartBattleInput,
  deps: BattleDeps,
  handle: BattleHandle,
): Promise<void> {
  const now = deps.now ?? Date.now
  const timeoutMs = deps.timeoutMs ?? DEFAULT_TIMEOUT_MS
  try {
    await createBattle({
      id: battleId,
      arena: input.arena,
      topic: input.topic,
      createdAt: now(),
    })

    const contestantIds = new Map<number, string>()
    for (const contestant of input.contestants) {
      const contestantId = await insertContestant({
        battleId,
        slot: contestant.slot,
        providerId: contestant.providerId,
        modelId: contestant.modelId,
        positionIndex: contestant.slot,
        status: 'running',
      })
      contestantIds.set(contestant.slot, contestantId)
    }

    const prompt = deps.arena.buildPrompt(input.topic)
    const context: RunContext = { deps, handle, battleId, prompt, contestantIds, timeoutMs }

    await emitPhase(handle, battleId, 'running')
    const runs = await Promise.all(
      input.contestants.map((contestant) => runContestant(contestant, context)),
    )

    if (handle.cancelled) {
      await emitPhase(handle, battleId, 'cancelled', { error: '对战已取消' })
      return
    }

    const succeeded = runs.filter((run) => run.submission !== null)
    if (succeeded.length === 0) {
      const reason = describeAllFailed(runs)
      await emitPhase(handle, battleId, 'failed', { error: reason })
      handle.emit({ type: 'battle.failed', reason })
      return
    }

    let verdicts: Verdict[] = []
    let judgeAvailable = false
    if (deps.judgeContext) {
      const judged = await runJudgePhase(succeeded, deps, handle, battleId)
      if (!judged) {
        await emitPhase(handle, battleId, 'cancelled', { error: '对战在裁判阶段取消' })
        return
      }
      verdicts = judged.verdicts
      judgeAvailable = judged.judgeAvailable
    }

    await emitPhase(handle, battleId, 'scoring')
    await emitPhase(handle, battleId, 'completed', { completedAt: now() })

    const result: BattleResult = {
      battleId,
      arena: input.arena,
      topic: input.topic,
      contestants: runs.map((run) => ({
        slot: run.contestant.slot,
        providerId: run.contestant.providerId,
        modelId: run.contestant.modelId,
        status: run.status,
        usage: run.submission?.usage ?? null,
        latencyMs: run.submission?.latencyMs ?? 0,
        outputLength: run.submission?.text.length ?? 0,
        code: run.submission?.code ?? null,
        errorMessage: run.errorMessage,
      })),
      verdicts,
      judgeAvailable,
    }
    handle.emit({ type: 'battle.completed', result })
  } catch (error) {
    if (handle.cancelled) {
      if (!handle.settled) await emitPhase(handle, battleId, 'cancelled', { error: '对战已取消' })
      return
    }
    if (handle.settled) return
    const reason = `对战编排失败：${readableMessage(error)}`
    handle.emit({ type: 'phase.changed', phase: 'failed' })
    await persist(() => updateBattlePhase(battleId, 'failed', { error: reason, completedAt: now() }))
    handle.emit({ type: 'battle.failed', reason })
  }
}

async function runContestant(contestant: ContestantInput, context: RunContext): Promise<ContestantRun> {
  const { deps, handle, prompt, contestantIds, timeoutMs } = context
  const contestantId = contestantIds.get(contestant.slot) ?? ''
  handle.emit({
    type: 'contestant.started',
    slot: contestant.slot,
    providerId: contestant.providerId,
    modelId: contestant.modelId,
  })

  const messages: ModelMessage[] = [{ role: 'user', content: prompt.user }]

  const timeoutController = new AbortController()
  const signal = AbortSignal.any([handle.signal, timeoutController.signal])
  const timeoutTimer = setTimeout(() => timeoutController.abort(), timeoutMs)
  let text = ''
  let usage: { prompt: number; completion: number } | null = null
  let latencyMs = 0
  let status: ContestantStatus = 'ok'
  let errorMessage: string | null = null
  let failed = false

  try {
    for await (const event of streamModel(deps.createModel(contestant), messages, {
      signal,
      apiKey: contestant.apiKey,
      providerLabel: contestant.providerId,
      system: prompt.system,
    })) {
      if (event.type === 'delta') {
        text += event.text
        handle.emit({ type: 'contestant.delta', slot: contestant.slot, text: event.text })
        continue
      }
      if (event.type === 'done') {
        usage = event.usage
        latencyMs = event.latencyMs
        continue
      }
      status = event.status
      errorMessage = event.message
      failed = true
      break
    }
  } catch (error) {
    status = 'provider_error'
    errorMessage = redact(`${contestant.providerId} 调用失败：${readableMessage(error)}`, [
      contestant.apiKey,
    ])
    failed = true
  }

  if (failed) {
    const message = errorMessage ?? '未知原因'
    handle.emit({ type: 'contestant.failed', slot: contestant.slot, status, message })
    clearTimeout(timeoutTimer)
    await persist(() =>
      updateContestantResult(contestantId, {
        status,
        errorMessage: message,
        outputText: text.length > 0 ? text : null,
        code: null,
        latencyMs,
        promptTokens: usage?.prompt ?? null,
        completionTokens: usage?.completion ?? null,
      }),
    )
    return { contestant, contestantId, submission: null, status, errorMessage: message }
  }

  clearTimeout(timeoutTimer)
  const code = deps.arena.extractCode(text)
  const submission: Submission = {
    contestant,
    text,
    code,
    usage,
    latencyMs,
    status: 'ok',
  }
  handle.emit({ type: 'contestant.done', slot: contestant.slot, usage, latencyMs })
  await persist(() =>
    updateContestantResult(contestantId, {
      status: 'ok',
      outputText: text,
      code,
      latencyMs,
      promptTokens: usage?.prompt ?? null,
      completionTokens: usage?.completion ?? null,
      errorMessage: null,
    }),
  )
  return { contestant, contestantId, submission, status: 'ok', errorMessage: null }
}

async function runJudgePhase(
  succeeded: ContestantRun[],
  deps: BattleDeps,
  handle: BattleHandle,
  battleId: string,
): Promise<{ verdicts: Verdict[]; judgeAvailable: boolean } | undefined> {
  const ctx = deps.judgeContext
  if (!ctx) return undefined
  await emitPhase(handle, battleId, 'judging')
  if (handle.cancelled) {
    await emitPhase(handle, battleId, 'cancelled', { error: '对战在裁判阶段取消' })
    return undefined
  }
  const submissions = succeeded
    .map((run) => run.submission)
    .filter((submission): submission is Submission => submission !== null)
  let verdicts: Verdict[] = []
  let judgeAvailable = false
  try {
    verdicts = await deps.arena.judge(submissions, ctx)
    judgeAvailable = verdicts.length > 0
  } catch {
    verdicts = []
    judgeAvailable = false
  }
  if (handle.cancelled) {
    await emitPhase(handle, battleId, 'cancelled', { error: '对战在裁判阶段取消' })
    return undefined
  }
  handle.emit({ type: 'judge.done', verdicts, available: judgeAvailable })
  return { verdicts, judgeAvailable }
}

async function emitPhase(
  handle: BattleHandle,
  battleId: string,
  phase: BattlePhase,
  patch: { error?: string; completedAt?: number } = {},
): Promise<void> {
  handle.emit({ type: 'phase.changed', phase })
  await persist(() => updateBattlePhase(battleId, phase, patch))
}

async function persist(op: () => Promise<unknown>, label = '数据库写入失败'): Promise<void> {
  try {
    await op()
  } catch (error) {
    console.error(redact(`${label}：${readableMessage(error)}`))
  }
}

function readableMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error)
  return redact(raw)
}

function describeAllFailed(runs: ContestantRun[]): string {
  if (runs.length === 0) return '本场没有参赛选手，无法开始对战'
  const details = runs
    .map((run) => `${run.contestant.providerId} ${run.contestant.modelId}（${contestantStatusLabels[run.status]}）`)
    .join('；')
  return `全部选手均失败，本场无法继续：${details}`
}

export function phaseLabel(phase: BattlePhase): string {
  return battlePhaseLabels[phase]
}

export function statusLabel(status: ContestantStatus): string {
  return contestantStatusLabels[status]
}
