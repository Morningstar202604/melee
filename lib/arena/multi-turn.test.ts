import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BANTER_PLAN, BANTER_ROLES, banterRoleForSlot } from './banter.ts'
import { DEBATE_PLAN, debateSideForSlot } from './debate.ts'
import { BANTER_TOPICS, DEBATE_TOPICS, banterTopic } from './topics.ts'

test('互辩：1 号执正方、2 号执反方', () => {
  assert.equal(debateSideForSlot(1).name, '正方')
  assert.equal(debateSideForSlot(2).name, '反方')
})

test('互辩第二轮是官方消息数组：user/assistant 交替，带对方立论', () => {
  const own = [{ round: 1, text: '我方立论' }]
  const others = [{ slot: 2, turns: [{ round: 1, text: '对方立论要点' }] }]
  const { messages } = DEBATE_PLAN.buildTurnMessages('该不该躺平', 2, 1, own, others)
  assert.equal(messages.length, 3)
  assert.equal(messages[0]!.role, 'user')
  assert.equal(messages[1]!.role, 'assistant')
  assert.equal(messages[1]!.content, '我方立论')

  const first = String(messages[0]!.content)
  assert.ok(first.includes('该不该躺平'))
  assert.ok(first.includes('正方'))
  assert.ok(first.includes('立论'))

  const rebuttal = String(messages[2]!.content)
  assert.equal(messages[2]!.role, 'user')
  assert.ok(rebuttal.includes('对方立论要点'))
  assert.ok(rebuttal.includes('驳斥'))
})

test('互辩第三轮：五段对话交替，结辩带上对方全部发言', () => {
  const others = [
    {
      slot: 2,
      turns: [
        { round: 1, text: '立论B' },
        { round: 2, text: '驳斥B' },
      ],
    },
  ]
  const { messages } = DEBATE_PLAN.buildTurnMessages(
    '该不该躺平',
    3,
    1,
    [
      { round: 1, text: '立论A' },
      { round: 2, text: '驳斥A' },
    ],
    others,
  )
  assert.deepEqual(
    messages.map((message) => message.role),
    ['user', 'assistant', 'user', 'assistant', 'user'],
  )
  assert.equal(messages[3]!.content, '驳斥A')
  const closing = String(messages[4]!.content)
  assert.ok(closing.includes('结辩'))
  assert.ok(closing.includes('立论B'))
  assert.ok(closing.includes('驳斥B'))
})

test('互辩轮次标签：立论/驳斥/结辩', () => {
  assert.equal(DEBATE_PLAN.roundLabel(1), '立论')
  assert.equal(DEBATE_PLAN.roundLabel(2), '驳斥')
  assert.equal(DEBATE_PLAN.roundLabel(3), '结辩')
  assert.equal(DEBATE_PLAN.totalRounds, 3)
})

test('互怼：角色按座位轮转且相邻座位风格不同', () => {
  assert.equal(banterRoleForSlot(1).name, BANTER_ROLES[0]!.name)
  assert.equal(banterRoleForSlot(2).name, BANTER_ROLES[1]!.name)
  assert.notEqual(banterRoleForSlot(1).name, banterRoleForSlot(2).name)
  assert.equal(banterRoleForSlot(5).name, BANTER_ROLES[0]!.name)
})

test('互怼第二轮：消息数组引用对方原话并要求接招', () => {
  const others = [{ slot: 2, turns: [{ round: 1, text: '“你这方案还不如不做”' }] }]
  const { messages } = BANTER_PLAN.buildTurnMessages(
    '互怼大会：项目延期谁背锅\n场景：复盘现场',
    2,
    1,
    [{ round: 1, text: '你才不做呢' }],
    others,
  )
  assert.deepEqual(
    messages.map((message) => message.role),
    ['user', 'assistant', 'user'],
  )
  assert.equal(messages[1]!.content, '你才不做呢')
  const poke = String(messages[2]!.content)
  assert.ok(poke.includes('你这方案还不如不做'))
  assert.ok(poke.includes('接招'))
  assert.ok(poke.includes('80 字'))
})

test('吵架题库：场景串包含标题与场景描述', () => {
  for (const topic of BANTER_TOPICS) {
    const composed = banterTopic(topic)
    assert.ok(composed.includes(topic.title))
    assert.ok(composed.includes(topic.scene))
  }
})

test('辩题库：motion 一律是中立命题（正反都可展开）', () => {
  for (const topic of DEBATE_TOPICS) {
    assert.ok(topic.motion.length > 4)
    assert.ok(!topic.motion.includes('任选一方'))
  }
})
