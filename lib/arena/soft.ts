import type { Arena, JudgeContext, Submission, Verdict } from './types.ts'
import {
  resolveJudgeModel,
  runJudge,
  type JudgeOptions,
  type JudgeOutcome,
} from '../judge/runner.ts'

export interface SoftArenaDeps {
  runJudge: (opts: JudgeOptions) => Promise<JudgeOutcome>
}

export function createSoftArena(deps: Partial<SoftArenaDeps> = {}): Arena {
  const judgeRun: SoftArenaDeps['runJudge'] =
    deps.runJudge ?? ((opts) => runJudge(opts))

  return {
    id: 'soft',
    buildPrompt: (topic: string) => ({
      system:
        '你是「AI 大乱斗」的一名参赛选手。请严格按题目规定的格式、结构与字数作答：' +
        '直接输出正文，不要解释思路、不要寒暄、不要任何前后缀。',
      user: topic,
    }),
    extractCode: () => null,
    judge: async (subs: Submission[], ctx: JudgeContext): Promise<Verdict[]> => {
      const outcome = await judgeRun({
        topic: ctx.topic,
        submissions: subs,
        judgeModel: resolveJudgeModel(ctx),
      })
      return outcome.verdicts
    },
  }
}

export const softArena: Arena = createSoftArena()
