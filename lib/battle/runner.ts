import type { LanguageModel, ModelMessage } from 'ai'
import type {
  Arena,
  ArenaId,
  ContestantInput,
  ContestantStatus,
  JudgeContext,
  Submission,
  TurnRecord,
  Verdict,
} from '../arena/types.ts'
import {
  DEFAULT_BATTLE_TIMEOUT_MS,
  DEFAULT_TURN_TIMEOUT_MS,
  serializeTurns,
} from '../arena/types.ts'
import {
  createBattleWithContestants,
  recordJudgement,
  updateBattlePhase,
  updateContestantResult,
} from '../db/repo.ts'
import { redact } from '../log/redact.ts'
import { logger } from '../log/logger.ts'
import { streamModel } from '../providers/stream.ts'
import { contestantStatusLabels, type BattlePhase, type BattleResult } from './events.ts'
import {
  createBattleHandle,
  getBattleHandle,
  registerBattle,
  unregisterBattle,
  type BattleHandle,
} from './registry.ts'

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
  battleTimeoutMs?: number
  now?: () => number
}

interface ContestantRun {
  contestant: ContestantInput
  contestantId: string
  turns: TurnRecord[]
  status: ContestantStatus
  errorMessage: string | null
  failed: boolean
  usage: { prompt: number; completion: number } | null
  startedAt: number
  submission: Submission | null
}

interface RunContext {
  deps: BattleDeps
  handle: BattleHandle
  battleId: string
  arena: Arena
  topic: string
  prompt: { system?: string; user: string }
  contestantIds: Map<number, string>
  turnTimeoutMs: number
  battleSignal: AbortSignal
  transcripts: Map<number, TurnRecord[]>
}

export async function startBattle(input: StartBattleInput, deps: BattleDeps): Promise<string> {
  const newContestants = input.contestants.map((c) => ({
    slot: c.slot,
    providerId: c.providerId,
    modelId: c.modelId,
    positionIndex: c.slot,
    status: 'running' as const,
  }))
  const { battleId, contestantIds } = await createBattleWithContestants(
    {
      arena: input.arena,
      topic: input.topic,
      createdAt: (deps.now ?? Date.now)(),
    },
    newContestants,
  )
  const handle = createBattleHandle(battleId)
  registerBattle(battleId, handle)

  const timer = setTimeout(() => {
    void runBattle(battleId, input, deps, handle, contestantIds)
      .finally(() => scheduleRecycle(battleId))
  }, 0)
  timer.unref()

  return battleId
}

function scheduleRecycle(battleId: string): void {
  const timer = setTimeout(() => {
    unregisterBattle(battleId)
  }, RECYCLE_GRACE_MS)
  timer.unref()
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
  contestantIds: Map<number, string>,
): Promise<void> {
  const now = deps.now ?? Date.now
  const battleTimeoutMs = deps.battleTimeoutMs ?? deps.arena.battleTimeoutMs ?? DEFAULT_BATTLE_TIMEOUT_MS
  const battleController = new AbortController()
  const battleTimer = setTimeout(() => battleController.abort(), battleTimeoutMs)
  battleTimer.unref()

  try {
    const prompt = deps.arena.buildPrompt(input.topic)
    const context: RunContext = {
      deps,
      handle,
      battleId,
      arena: deps.arena,
      topic: input.topic,
      prompt,
      contestantIds,
      turnTimeoutMs: deps.timeoutMs ?? deps.arena.turnTimeoutMs ?? DEFAULT_TURN_TIMEOUT_MS,
      battleSignal: battleController.signal,
      transcripts: new Map(input.contestants.map((c) => [c.slot, []])),
    }

    await emitPhase(handle, battleId, 'running')

    const runs = input.contestants.map((contestant) => createRun(contestant, context))
    const totalRounds = deps.arena.plan?.totalRounds ?? 1

    for (let round = 1; round <= totalRounds; round += 1) {
      if (handle.cancelled) break
      if (totalRounds > 1) {
        handle.emit({ type: 'round.changed', round, totalRounds })
      }
      const active = runs.filter((run) => !run.failed)
      if (active.length === 0) break
      await Promise.all(active.map((run) => runTurn(run, round, context)))
    }

    for (const run of runs) {
      await finalizeRun(run, context)
    }

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
    const judgeCtx = resolveJudgeContext(deps, input.topic)
    if (judgeCtx) {
      const judged = await runJudgePhase(succeeded, deps, handle, battleId, input.arena, judgeCtx)
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
        usage: run.submission?.usage ?? run.usage,
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
  } finally {
    clearTimeout(battleTimer)
  }
}

function createRun(contestant: ContestantInput, context: RunContext): ContestantRun {
  return {
    contestant,
    contestantId: context.contestantIds.get(contestant.slot) ?? '',
    turns: [],
    status: 'ok',
    errorMessage: null,
    failed: false,
    usage: null,
    startedAt: Date.now(),
    submission: null,
  }
}

function otherTranscripts(
  context: RunContext,
  slot: number,
  round: number,
): Array<{ slot: number; turns: TurnRecord[] }> {
  const others: Array<{ slot: number; turns: TurnRecord[] }> = []
  for (const [otherSlot, turns] of context.transcripts) {
    if (otherSlot === slot) continue
    const visible = turns.filter((turn) => turn.round < round)
    others.push({ slot: otherSlot, turns: visible })
  }
  others.sort((a, b) => a.slot - b.slot)
  return others
}

async function runTurn(run: ContestantRun, round: number, context: RunContext): Promise<void> {
  const { deps, handle, arena, topic, prompt, turnTimeoutMs, battleSignal } = context
  const contestant = run.contestant
  const plan = arena.plan
  const ownTurns = run.turns.filter((turn) => turn.round < round)
  const turnInput = plan
    ? plan.buildTurnMessages(
        topic,
        round,
        contestant.slot,
        ownTurns,
        otherTranscripts(context, contestant.slot, round),
      )
    : { system: prompt.system, messages: [{ role: 'user' as const, content: prompt.user }] }

  handle.emit({
    type: 'contestant.started',
    slot: contestant.slot,
    providerId: contestant.providerId,
    modelId: contestant.modelId,
    ...(plan ? { round } : {}),
  })

  const messages: ModelMessage[] = turnInput.messages
  const turnController = new AbortController()
  const signal = AbortSignal.any([handle.signal, battleSignal, turnController.signal])
  const turnTimer = setTimeout(() => turnController.abort(), turnTimeoutMs)

  let text = ''
  let reasoning = ''
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
      system: turnInput.system,
      maxOutputTokens: arena.maxOutputTokens,
    })) {
      if (event.type === 'delta') {
        text += event.text
        handle.emit({ type: 'contestant.delta', slot: contestant.slot, text: event.text })
        continue
      }
      if (event.type === 'reasoning') {
        reasoning += event.text
        handle.emit({ type: 'contestant.reasoning', slot: contestant.slot, text: event.text })
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
  } finally {
    clearTimeout(turnTimer)
  }

  if (!failed && text.length === 0 && signal.aborted) {
    failed = true
    status = 'timeout'
    errorMessage = '对战已中断，本回合未完成'
  }

  if (failed) {
    const message = errorMessage ?? '未知原因'
    handle.emit({
      type: 'contestant.failed',
      slot: contestant.slot,
      status,
      message,
      ...(plan ? { round } : {}),
    })
    run.failed = true
    run.status = status
    run.errorMessage = message
    if (text.length > 0 || reasoning.length > 0) {
      const partial: TurnRecord = { round, text, ...(reasoning ? { reasoning } : {}) }
      run.turns.push(partial)
      context.transcripts.get(contestant.slot)?.push(partial)
    }
    return
  }

  const turn: TurnRecord = { round, text, ...(reasoning ? { reasoning } : {}) }
  run.turns.push(turn)
  context.transcripts.get(contestant.slot)?.push(turn)
  if (usage) {
    run.usage = {
      prompt: (run.usage?.prompt ?? 0) + usage.prompt,
      completion: (run.usage?.completion ?? 0) + usage.completion,
    }
  }
  const isFinalTurn = !plan || round >= plan.totalRounds
  const totalLatencyMs = isFinalTurn && plan ? Date.now() - run.startedAt : latencyMs
  handle.emit({
    type: 'contestant.done',
    slot: contestant.slot,
    usage: isFinalTurn && plan ? run.usage : usage,
    latencyMs: totalLatencyMs,
    ...(plan ? { round } : {}),
  })
}

async function finalizeRun(run: ContestantRun, context: RunContext): Promise<void> {
  const { arena } = context
  const contestant = run.contestant
  const plan = arena.plan

  const singleReasoning = plan ? null : run.turns[0]?.reasoning ?? null

  if (run.failed) {
    const fallbackText = run.turns[0]?.text && run.turns[0].text.length > 0 ? run.turns[0].text : null
    const outputText = plan && run.turns.length > 0 ? serializeTurns(run.turns) : fallbackText
    await persist(() =>
      updateContestantResult(run.contestantId, {
        status: run.status,
        errorMessage: run.errorMessage,
        outputText,
        reasoningText: singleReasoning,
        code: null,
        latencyMs: 0,
        promptTokens: run.usage?.prompt ?? null,
        completionTokens: run.usage?.completion ?? null,
      }),
    )
    return
  }

  const text = plan ? serializeTurns(run.turns) : run.turns[0]?.text ?? ''
  const code = arena.extractCode(text)
  const submission: Submission = {
    contestant,
    text,
    code,
    usage: run.usage,
    latencyMs: Date.now() - run.startedAt,
    status: 'ok',
    dbContestantId: run.contestantId,
  }
  run.submission = submission
  run.status = 'ok'
  await persist(() =>
    updateContestantResult(run.contestantId, {
      status: 'ok',
      outputText: text,
      reasoningText: singleReasoning,
      code,
      latencyMs: submission.latencyMs,
      promptTokens: run.usage?.prompt ?? null,
      completionTokens: run.usage?.completion ?? null,
      errorMessage: null,
    }),
  )
}

/** 判定上下文：LLM 裁判轨需要配置；沙箱 / 残局这类确定性判定不需要。 */
function resolveJudgeContext(deps: BattleDeps, topic: string): JudgeContext | undefined {
  if (deps.judgeContext) return deps.judgeContext
  if (deps.arena.judgeNeedsLlm === false) {
    return { topic, judgeModelId: '', judgeApiKey: '' }
  }
  return undefined
}

async function runJudgePhase(
  succeeded: ContestantRun[],
  deps: BattleDeps,
  handle: BattleHandle,
  battleId: string,
  arena: ArenaId,
  ctx: JudgeContext,
): Promise<{ verdicts: Verdict[]; judgeAvailable: boolean } | undefined> {
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
    verdicts = await deps.arena.judge(submissions, {
      ...ctx,
      onSandboxResult: (progress) => {
        handle.emit({
          type: 'sandbox.result',
          slot: progress.slot,
          status: progress.status,
          passed: progress.passed,
          total: progress.total,
          timeMs: progress.timeMs,
          memoryKb: progress.memoryKb,
        })
      },
    })
    judgeAvailable = verdicts.length > 0
    if (arena !== 'hard-code' && verdicts.length > 0) {
      await persistJudgements(succeeded, verdicts, ctx.judgeModelId, battleId)
    }
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

async function persistJudgements(
  succeeded: ContestantRun[],
  verdicts: Verdict[],
  judgeModel: string,
  battleId: string,
): Promise<void> {
  const contestantIdBySlot = new Map(
    succeeded.map((run) => [run.contestant.slot, run.contestantId]),
  )
  for (const verdict of verdicts) {
    const contestantId = contestantIdBySlot.get(verdict.contestantSlot)
    if (!contestantId) continue
    await persist(() =>
      recordJudgement({
        battleId,
        contestantId,
        round: 0,
        judgeModel,
        positionIndex: verdict.contestantSlot,
        scoresJson: JSON.stringify(verdict.scores),
        rationale: verdict.rationale,
      }),
    )
  }
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
    logger.error(label, { msg: readableMessage(error) })
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

