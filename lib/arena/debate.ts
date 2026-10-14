import type { ModelMessage } from 'ai'
import type { Arena, JudgeContext, Submission, TurnPlan, Verdict } from './types.ts'
import { DEBATE_AXES } from './axes.ts'
import { runJudge, resolveJudgeModel, type JudgeOptions } from '../judge/runner.ts'
import { logger } from '../log/logger.ts'

export { DEBATE_AXES }

export function debateSideForSlot(slot: number): { name: string; detail: string } {
  if (slot % 2 === 1) return { name: '正方', detail: '支持该辩题' }
  return { name: '反方', detail: '反对该辩题' }
}

export const DEBATE_SYSTEM =
  '你是「AI 大乱斗」互辩场的参赛辩手，正在与对手进行一场三轮制辩论（立论 → 驳斥 → 结辩）。' +
  '要求：观点鲜明、语言有锋芒但克制；每轮严格按字数上限输出；' +
  '严禁人身攻击、歧视、脏话或任何越线内容；直接输出正文，不要标题、不要解释、不要任何前后缀。'

function othersBlock(
  others: Array<{ slot: number; turns: Array<{ round: number; text: string }> }>,
  upToRound: number,
): string {
  const parts: string[] = []
  for (const item of others) {
    const visible = item.turns.filter((turn) => turn.round <= upToRound && turn.text.length > 0)
    if (visible.length === 0) continue
    parts.push(
      `【${item.slot} 号对手的发言】\n` +
        visible.map((turn) => `（第 ${turn.round} 轮）${turn.text}`).join('\n'),
    )
  }
  return parts.length > 0 ? parts.join('\n\n') : '（对手暂无发言）'
}

export const DEBATE_PLAN: TurnPlan = {
  totalRounds: 3,
  roundLabel: (round) => (round === 1 ? '立论' : round === 2 ? '驳斥' : '结辩'),
  buildTurnMessages: (topic, round, slot, own, others) => {
    const side = debateSideForSlot(slot)
    const messages: ModelMessage[] = [
      {
        role: 'user',
        content: [
          `辩题：${topic}`,
          `你执${side.name}（${side.detail}）。`,
          '',
          '请完成【立论】（不超过 300 字）：按「我方立场（一句话）→ 两条论据（每条两三句，须给出具体例子）→ 收束句」输出。',
        ].join('\n'),
      },
    ]

    if (round >= 2) {
      messages.push({ role: 'assistant', content: own.find((turn) => turn.round === 1)?.text ?? '' })
      messages.push({
        role: 'user',
        content: [
          othersBlock(others, 1),
          '',
          '请完成【驳斥】（不超过 200 字）：直接引用对方的具体论据并逐条反驳，不要再重复自己的立论。',
        ].join('\n'),
      })
    }

    if (round >= 3) {
      messages.push({ role: 'assistant', content: own.find((turn) => turn.round === 2)?.text ?? '' })
      messages.push({
        role: 'user',
        content: [
          othersBlock(others, 2),
          '',
          '请完成【结辩】（不超过 150 字）：收束全场交锋，点出对方最致命的漏洞，给自己的立场一个有力的收尾。',
        ].join('\n'),
      })
    }

    return { system: DEBATE_SYSTEM, messages }
  },
}

export interface MultiTurnDeps {
  runJudge: (opts: JudgeOptions) => Promise<Awaited<ReturnType<typeof runJudge>>>
}

export function createDebateArena(deps: Partial<MultiTurnDeps> = {}): Arena {
  const judgeRun: MultiTurnDeps['runJudge'] = deps.runJudge ?? ((opts) => runJudge(opts))
  return {
    id: 'debate',
    plan: DEBATE_PLAN,
    axes: DEBATE_AXES,
    minContestants: 2,
    maxContestants: 2,
    turnTimeoutMs: 120000,
    battleTimeoutMs: 420000,
    buildPrompt: (topic) => {
      const first = DEBATE_PLAN.buildTurnMessages(topic, 1, 1, [], [])
      const firstUser = first.messages.find((message) => message.role === 'user')
      return {
        system: DEBATE_SYSTEM,
        user: typeof firstUser?.content === 'string' ? firstUser.content : '',
      }
    },
    extractCode: () => null,
    judge: async (subs: Submission[], ctx: JudgeContext): Promise<Verdict[]> => {
      try {
        const outcome = await judgeRun({
          topic: ctx.topic,
          submissions: subs,
          judgeModel: resolveJudgeModel(ctx),
          axes: DEBATE_AXES,
        })
        return outcome.verdicts
      } catch (error) {
        logger.error('互辩裁判异常', { error: error instanceof Error ? error.message : String(error) })
        return []
      }
    },
  }
}

export const debateArena: Arena = createDebateArena()
