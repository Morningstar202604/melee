import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Arena, Submission } from './types.ts'
import { createSoftArena, softArena } from './soft.ts'
import { CODE_TOPICS, SOFT_TOPICS } from './topics.ts'

const TOPIC = SOFT_TOPICS[0]

test('buildPrompt 带上题目且 system 要求按格式作答', () => {
  const prompt = softArena.buildPrompt(TOPIC?.prompt ?? '')
  assert.ok(prompt.user.includes(TOPIC?.prompt ?? ''))
  assert.ok(prompt.system && prompt.system.length > 0)
  assert.ok(prompt.system?.includes('格式'))
  assert.ok(prompt.system?.includes('参赛'))
})

test('extractCode 对软战场恒返回 null', () => {
  assert.equal(softArena.extractCode('```python\nprint(1)\n```'), null)
  assert.equal(softArena.extractCode('纯文字作答，没有代码'), null)
  assert.equal(softArena.extractCode(''), null)
})

function submission(slot: number): Submission {
  return {
    contestant: { slot, providerId: 'deepseek', modelId: 'deepseek-chat', apiKey: 'sk-test' },
    text: `选手 ${slot} 的作答`,
    code: null,
    usage: null,
    latencyMs: 10,
    status: 'ok',
  }
}

const JUDGE_CONTEXT = {
  topic: TOPIC?.title ?? '软战场题目',
  judgeModelId: 'deepseek-chat',
  judgeApiKey: 'sk-test',
}

test('judge 在裁判不可用时返回空 verdicts 但不抛错', async () => {
  const arena = createSoftArena({
    runJudge: async () => ({ verdicts: [], roundsAgreed: false, unavailable: true }),
  })
  const verdicts = await arena.judge([submission(1), submission(2)], JUDGE_CONTEXT)
  assert.deepEqual(verdicts, [])
})

test('judge 把裁判结果填成 logic/evidence/relevance 分项并保留原文理由', async () => {
  const arena = createSoftArena({
    runJudge: async () => ({
      verdicts: [
        {
          contestantSlot: 1,
          scores: { logic: 7, evidence: 6, relevance: 5 },
          passed: 0,
          total: 0,
          rationale: '先手稳，一波连胜',
        },
      ],
      roundsAgreed: true,
      unavailable: false,
    }),
  })
  const verdicts = await arena.judge([submission(1)], JUDGE_CONTEXT)
  assert.equal(verdicts.length, 1)
  assert.equal(verdicts[0]?.contestantSlot, 1)
  assert.deepEqual(verdicts[0]?.scores, { logic: 7, evidence: 6, relevance: 5 })
  assert.equal(verdicts[0]?.rationale, '先手稳，一波连胜')
})

test('题库自洽：软题 6 道且三类齐全、id 唯一、prompt 写明格式要求', () => {
  const kinds = new Set(SOFT_TOPICS.map((topic) => topic.kind))
  assert.equal(SOFT_TOPICS.length, 6)
  assert.deepEqual([...kinds].sort(), ['creative', 'debate', 'roleplay'])
  assert.equal(kinds.size, 3)
  const ids = new Set(SOFT_TOPICS.map((topic) => topic.id))
  assert.equal(ids.size, SOFT_TOPICS.length)
  for (const topic of SOFT_TOPICS) {
    assert.ok(topic.title.length > 0, `${topic.id} 缺少标题`)
    assert.ok(topic.prompt.length > 0, `${topic.id} 缺少题干`)
    assert.ok(/格式|结构|字数|输出/.test(topic.prompt), `${topic.id} 的 prompt 未写明输出格式`)
  }
  const counted = SOFT_TOPICS.filter((topic) => topic.kind === 'debate').length
  assert.equal(counted, 2)
  assert.equal(SOFT_TOPICS.filter((topic) => topic.kind === 'creative').length, 2)
  assert.equal(SOFT_TOPICS.filter((topic) => topic.kind === 'roleplay').length, 2)
})

test('题库自洽：代码题 5~8 道，测试用例非空、id 唯一', () => {
  assert.ok(CODE_TOPICS.length >= 5 && CODE_TOPICS.length <= 8)
  const ids = new Set(CODE_TOPICS.map((topic) => topic.id))
  assert.equal(ids.size, CODE_TOPICS.length)
  for (const topic of CODE_TOPICS) {
    assert.ok(topic.tests.length >= 2, `${topic.id} 至少需要 2 个测试用例`)
    for (const testCase of topic.tests) {
      assert.equal(typeof testCase.input, 'string')
      assert.equal(typeof testCase.expected, 'string')
      assert.ok(testCase.expected.endsWith('\n'), `${topic.id} 的期望输出应以换行结尾`)
    }
  }
})

test('softArena 满足 Arena 接口', () => {
  const arena: Arena = softArena
  assert.equal(arena.id, 'soft')
})
