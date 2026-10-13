import type { JudgeAxis } from './types.ts'

/** 纯数据评分轴：服务端裁判与客户端战报共用，避免两处漂移。 */

export const SOFT_AXES: JudgeAxis[] = [
  { key: 'logic', label: '逻辑', description: '立论与结论是否自洽，有没有自相矛盾、循环论证。' },
  { key: 'evidence', label: '论据', description: '有没有具体例子、数据或细节支撑，还是空喊口号。' },
  { key: 'relevance', label: '切题', description: '有没有正面回应题目，还是绕圈、答非所问。' },
]

export const DEBATE_AXES: JudgeAxis[] = [
  { key: 'logic', label: '逻辑', description: '立论与反驳是否自洽，有没有自相矛盾。' },
  { key: 'evidence', label: '论据', description: '有没有具体例子、数据或细节支撑，还是空喊口号。' },
  { key: 'clash', label: '交锋', description: '有没有正面回应并压制对方的具体论点，而不是各说各话。' },
]

export const BANTER_AXES: JudgeAxis[] = [
  { key: 'hook', label: '接梗', description: '是否接住并利用对方上一轮的话柄反击，而不是自说自话。' },
  { key: 'sting', label: '杀伤', description: '回击的犀利与机智程度：有梗、有反转、刀刀见血但不脏。' },
  { key: 'poise', label: '分寸', description: '毒舌但守线：没有人身攻击、歧视、脏话或越线内容；越界此项给 0-3 分。' },
]
