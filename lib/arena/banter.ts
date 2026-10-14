import type { ModelMessage } from 'ai'
import type { Arena, JudgeContext, Submission, TurnPlan, Verdict } from './types.ts'
import { BANTER_AXES } from './axes.ts'
import { runJudge, resolveJudgeModel, type JudgeOptions } from '../judge/runner.ts'
import { logger } from '../log/logger.ts'

export { BANTER_AXES }

export interface BanterRole {
  name: string
  quirk: string
}

export const BANTER_ROLES: BanterRole[] = [
  { name: '暴躁老哥', quirk: '嗓门大、爱拍桌、短句连发' },
  { name: '阴阳怪气师', quirk: '不骂人但句句带刺，擅长反讽' },
  { name: '杠精本精', quirk: '专抓逻辑漏洞抬杠，爱挑字眼' },
  { name: '和事佬（拱火版）', quirk: '表面劝架，实际上句句拱火' },
]

export function banterRoleForSlot(slot: number): BanterRole {
  const index = ((slot - 1) % BANTER_ROLES.length + BANTER_ROLES.length) % BANTER_ROLES.length
  return BANTER_ROLES[index]!
}

export const BANTER_SYSTEM =
  '你是「AI 大乱斗」互怼大会的参赛选手。规则：三轮互怼，每轮只发一条消息（不超过 80 字），' +
  '像群聊一样短、快、有梗；可以引用对方上一轮的原话反击，越会接梗越有杀伤力。' +
  '底线：严禁人身攻击、地域/性别/职业歧视、脏话、涉政与真实人物；越线直接扣分。' +
  '只输出你要发的那条消息本身，不要解释、不要加引号或前缀。'

function othersBlock(
  others: Array<{ slot: number; turns: Array<{ round: number; text: string }> }>,
  upToRound: number,
): string {
  const parts: string[] = []
  for (const item of others) {
    const visible = item.turns.filter((turn) => turn.round <= upToRound && turn.text.length > 0)
    if (visible.length === 0) continue
    parts.push(
      `${item.slot} 号选手的发言：\n` +
        visible.map((turn) => `（第 ${turn.round} 轮）${turn.text}`).join('\n'),
    )
  }
  return parts.length > 0 ? parts.join('\n\n') : '（暂时无人发言）'
}

export const BANTER_PLAN: TurnPlan = {
  totalRounds: 3,
  roundLabel: (round) => (round === 1 ? '开局炸场' : round === 2 ? '接招反击' : '收场补刀'),
  buildTurnMessages: (topic, round, slot, own, others) => {
    const role = banterRoleForSlot(slot)
    const roleLine = `你的角色：${role.name}（${role.quirk}）。`
    const messages: ModelMessage[] = [
      {
        role: 'user',
        content: [
          topic,
          roleLine,
          '',
          '现在开场：先立住你的角色人设，把火点起来（第 1 轮，≤80 字）。',
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
          '现在接招：引用对方上一轮的原话（可以夸张引用），反击回去（第 2 轮，≤80 字）。',
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
          '现在收场：最后一句要留一个让人记住的梗，顺便把全场总结成一句话（第 3 轮，≤80 字）。',
        ].join('\n'),
      })
    }

    return { system: BANTER_SYSTEM, messages }
  },
}

export interface MultiTurnDeps {
  runJudge: (opts: JudgeOptions) => Promise<Awaited<ReturnType<typeof runJudge>>>
}

export function createBanterArena(deps: Partial<MultiTurnDeps> = {}): Arena {
  const judgeRun: MultiTurnDeps['runJudge'] = deps.runJudge ?? ((opts) => runJudge(opts))
  return {
    id: 'banter',
    plan: BANTER_PLAN,
    maxOutputTokens: 800,
    axes: BANTER_AXES,
    minContestants: 2,
    maxContestants: 4,
    turnTimeoutMs: 90000,
    battleTimeoutMs: 420000,
    buildPrompt: (topic) => {
      const first = BANTER_PLAN.buildTurnMessages(topic, 1, 1, [], [])
      const firstUser = first.messages.find((message) => message.role === 'user')
      return {
        system: BANTER_SYSTEM,
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
          axes: BANTER_AXES,
        })
        return outcome.verdicts
      } catch (error) {
        logger.error('互怼裁判异常', { error: error instanceof Error ? error.message : String(error) })
        return []
      }
    },
  }
}

export const banterArena: Arena = createBanterArena()
