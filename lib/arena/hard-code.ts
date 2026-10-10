import type { Arena, JudgeContext, Submission, Verdict } from './types.ts'
import {
  getCodeTopic,
  codeTimeLimitMs,
  type CodeTopic,
  type CodeTestCase,
} from './topics.ts'
import { getBackend } from '../sandbox/backend.ts'
import type { SandboxBackend } from '../sandbox/backend.ts'
import {
  recordSandboxResult,
  updateContestantResult,
  type NewSandboxResult,
} from '../db/repo.ts'

const CODE_BLOCK_RE = /```(?:python|py)?\s*\n([\s\S]*?)```/m
const UNTERMINATED_CODE_BLOCK_RE = /```(?:python|py)?\s*\n([\s\S]*)$/m

export function extractCode(text: string): string | null {
  if (typeof text !== 'string' || text.trim().length === 0) return null
  const matched = text.match(CODE_BLOCK_RE)
  if (matched && matched[1]) return matched[1].trim()
  const fallback = text.match(UNTERMINATED_CODE_BLOCK_RE)
  if (fallback && fallback[1]) return fallback[1].trim()
  return null
}

interface HardJudgeOptions {
  topic: string
  tests: CodeTestCase[]
  sandbox: SandboxBackend
}

export async function judgeCodeSubmissions(
  subs: Submission[],
  options: HardJudgeOptions,
): Promise<Verdict[]> {
  const results: Verdict[] = []
  for (const sub of subs) {
    if (!sub.code) {
      results.push({
        contestantSlot: sub.contestant.slot,
        scores: { pass_rate: 0 },
        passed: 0,
        total: options.tests.length,
        rationale: '未产出可执行代码',
      })
      continue
    }

    const limit = codeTimeLimitMs({
      id: options.topic,
      title: options.topic,
      prompt: options.topic,
      tests: options.tests,
    })
    const startedAt = Date.now()
    let backend = options.sandbox.id
    let isolationLevel = options.sandbox.isolationLevel
    let degraded = 0

    try {
      const result = await options.sandbox.run({
        code: sub.code,
        tests: options.tests,
        timeLimitMs: limit,
      })

      const status = result.status
      const passed = status === 'ok' ? result.passed : 0
      const total = status === 'ok' ? result.total : options.tests.length
      const passRate = total > 0 ? Math.round((passed / total) * 100) / 100 : 0

      if (status === 'timeout') {
        await persistResult(sub.contestant.slot, {
          contestantId: '',
          backend,
          isolationLevel,
          language: 'python',
          status: 'timeout',
          passed,
          total,
          timeMs: result.timeMs,
          memoryKb: null,
          stdoutTail: result.stdoutTail,
          degraded,
        })
        results.push({
          contestantSlot: sub.contestant.slot,
          scores: { pass_rate: passRate },
          passed,
          total,
          rationale: `执行超时（>${limit}ms），沙箱已强制终止`,
        })
      } else if (status === 'error') {
        await persistResult(sub.contestant.slot, {
          contestantId: '',
          backend,
          isolationLevel,
          language: 'python',
          status: 'error',
          passed,
          total,
          timeMs: result.timeMs,
          memoryKb: null,
          stdoutTail: result.stdoutTail,
          degraded,
        })
        results.push({
          contestantSlot: sub.contestant.slot,
          scores: { pass_rate: passRate },
          passed,
          total,
          rationale: result.errorMessage ?? '代码执行出错',
        })
      } else {
        await persistResult(sub.contestant.slot, {
          contestantId: '',
          backend,
          isolationLevel,
          language: 'python',
          status: 'ok',
          passed,
          total,
          timeMs: result.timeMs,
          memoryKb: null,
          stdoutTail: result.stdoutTail,
          degraded,
        })
        results.push({
          contestantSlot: sub.contestant.slot,
          scores: { pass_rate: passRate },
          passed,
          total,
          rationale: total > 0 ? `通过 ${passed}/${total} 个测试用例` : '本题无测试用例',
        })
      }
    } catch {
      await persistResult(sub.contestant.slot, {
        contestantId: '',
        backend: 'none',
        isolationLevel: 'none',
        language: 'python',
        status: 'unavailable',
        passed: 0,
        total: options.tests.length,
        timeMs: Date.now() - startedAt,
        memoryKb: null,
        stdoutTail: '',
        degraded: 1,
      })
      results.push({
        contestantSlot: sub.contestant.slot,
        scores: { pass_rate: 0 },
        passed: 0,
        total: options.tests.length,
        rationale: '沙箱后端不可用，本次未真实执行（降级）',
      })
    }
  }
  return results
}

async function persistResult(slot: number, result: NewSandboxResult): Promise<void> {
  try {
    await recordSandboxResult(result)
  } catch (error) {
    console.error(`写入沙箱结果失败 (slot=${slot}): ${error instanceof Error ? error.message : String(error)}`)
  }
}

export function getHardCodeTopic(topicId: string): CodeTopic | undefined {
  return getCodeTopic(topicId)
}

export function createHardCodeArena(backend?: SandboxBackend): Arena {
  return {
    id: 'hard-code',
    buildPrompt: (topic: string) => ({
      system:
        '你是「AI 大乱斗」硬战场的参赛选手。请只输出一个 Python 代码块（用 ```python ... ``` 包裹），' +
        '实现题目要求的功能：从标准输入读取，向标准输出打印结果。' +
        '不要输出任何解释、寒暄或代码块以外的任何文字。',
      user: topic,
    }),
    extractCode,
    judge: async (subs: Submission[], ctx: JudgeContext): Promise<Verdict[]> => {
      let sandbox: SandboxBackend
      try {
        sandbox = backend ?? (await getBackend())
      } catch {
        return subs.map((sub) => ({
          contestantSlot: sub.contestant.slot,
          scores: { pass_rate: 0 },
          passed: 0,
          total: 0,
          rationale: '沙箱后端不可用，本次未真实执行（降级）',
        }))
      }
      return judgeCodeSubmissions(subs, {
        topic: ctx.topic,
        tests: [],
        sandbox,
      })
    },
  }
}

export const hardCodeArena: Arena = createHardCodeArena()
