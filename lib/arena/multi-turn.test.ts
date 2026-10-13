import assert from 'node:assert/strict'
import { test } from 'node:test'
import { BANTER_PLAN, BANTER_ROLES, banterRoleForSlot } from './banter.ts'
import { DEBATE_PLAN, debateSideForSlot } from './debate.ts'
import { BANTER_TOPICS, DEBATE_TOPICS, banterTopic } from './topics.ts'

test('互辩：1 号执正方、2 号执反方', () => {
  assert.equal(debateSideForSlot(1).name, '正方')
  assert.equal(debateSideForSlot(2).name, '反方')
})

test('互辩第二轮提示词包含对方立论、且不含本轮内容', () => {
  const own = [{ round: 1, text: '我方立论' }]
  const others = [{ slot: 2, turns: [{ round: 1, text: '对方立论要点' }] }]
  const prompt = DEBATE_PLAN.buildTurnPrompt('该不该躺平', 2, 1, own, others)
  assert.ok(prompt.user.includes('该不该躺平'))
  assert.ok(prompt.user.includes('正方'))
  assert.ok(prompt.user.includes('对方立论要点'))
  assert.ok(prompt.user.includes('驳斥'))
  assert.ok(!prompt.user.includes('我方立论'))
})

test('互辩第三轮提示词是结辩并带上对方全部发言', () => {
  const others = [
    {
      slot: 2,
      turns: [
        { round: 1, text: '立论B' },
        { round: 2, text: '驳斥B' },
      ],
    },
  ]
  const prompt = DEBATE_PLAN.buildTurnPrompt('该不该躺平', 3, 1, [{ round: 1, text: '立论A' }], others)
  assert.ok(prompt.user.includes('结辩'))
  assert.ok(prompt.user.includes('立论B'))
  assert.ok(prompt.user.includes('驳斥B'))
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

test('互怼第二轮提示词引用对方上一轮原话并要求接招', () => {
  const others = [{ slot: 2, turns: [{ round: 1, text: '“你这方案还不如不做”' }] }]
  const prompt = BANTER_PLAN.buildTurnPrompt('互怼大会：项目延期谁背锅\n场景：复盘现场', 2, 1, [], others)
  assert.ok(prompt.user.includes('你这方案还不如不做'))
  assert.ok(prompt.user.includes('接招'))
  assert.ok(prompt.user.includes('80 字'))
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
