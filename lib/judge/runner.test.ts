import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createOpenAI } from '@ai-sdk/openai'
import { startFakeServer } from '../../test/fixtures/fake-openai-server.ts'
import type { Submission } from '../arena/types.ts'
import {
  parseJudgeOutput,
  roundsAgreed,
  runJudge,
  type JudgeCall,
  type MappedVerdict,
  type JudgeOptions,
} from './runner.ts'

function judgeJson(verdicts: string): string {
  return `{"verdicts":[${verdicts}]}`
}

const TWO_VERDICTS = judgeJson(
  '{"label":"A","logic":8,"evidence":7,"relevance":9,"rationale":"立论扎实，有梗"},' +
    '{"label":"B","logic":5,"evidence":6,"relevance":7,"rationale":"有点跑题"}',
)

test('纯 JSON 字符串能解析', () => {
  const parsed = parseJudgeOutput(TWO_VERDICTS)
  assert.ok(parsed)
  assert.equal(parsed?.verdicts.length, 2)
  assert.deepEqual(parsed?.verdicts[0], {
    label: 'A',
    scores: { logic: 8, evidence: 7, relevance: 9 },
    rationale: '立论扎实，有梗',
  })
})

test('带 ```json 围栏能解析', () => {
  const parsed = parseJudgeOutput('```json\n' + TWO_VERDICTS + '\n```')
  assert.ok(parsed)
  assert.equal(parsed?.verdicts.length, 2)
  assert.equal(parsed?.verdicts[1]?.label, 'B')
})

test('JSON 前后夹带解释文字能解析', () => {
  const raw = `我来点评这场对战。\n${TWO_VERDICTS}\n以上就是本场裁判结论。`
  const parsed = parseJudgeOutput(raw)
  assert.ok(parsed)
  assert.equal(parsed?.verdicts.length, 2)
})

test('缺 rationale 字段时用默认理由，不抛错', () => {
  const parsed = parseJudgeOutput(judgeJson('{"label":"A","logic":8,"evidence":7,"relevance":9}'))
  assert.ok(parsed)
  assert.equal(parsed?.verdicts[0]?.rationale, '未提供理由')
})

test('缺失的分数补 0，不抛错', () => {
  const parsed = parseJudgeOutput(judgeJson('{"label":"A","evidence":7,"relevance":9}'))
  assert.ok(parsed)
  assert.equal(parsed?.verdicts[0]?.scores.logic ?? 0, 0)
  assert.equal(parsed?.verdicts[0]?.scores.evidence, 7)
})

test('完全不可解析时返回 null', () => {
  assert.equal(parseJudgeOutput('我认为 A 更胜一筹，因为它逻辑更自洽。'), null)
  assert.equal(parseJudgeOutput(''), null)
  assert.equal(parseJudgeOutput('抱歉，我无法完成评判。'), null)
})

function mapped(
  slot: number,
  label: string,
  total: number,
): MappedVerdict {
  return {
    slot,
    label,
    scores: { logic: total, evidence: total, relevance: total, clash: total },
    rationale: '点评',
  }
}

test('两轮名次一致 → roundsAgreed 为 true', () => {
  const roundOne = [mapped(1, 'A', 9), mapped(2, 'B', 5)]
  const roundTwo = [mapped(1, 'B', 9), mapped(2, 'A', 5)]
  assert.equal(roundsAgreed(roundOne, roundTwo), true)
})

test('两轮名次完全相反 → roundsAgreed 为 false', () => {
  const roundOne = [mapped(1, 'A', 9), mapped(2, 'B', 5)]
  const roundTwo = [mapped(1, 'A', 3), mapped(2, 'B', 8)]
  assert.equal(roundsAgreed(roundOne, roundTwo), false)
})

function submission(slot: number, text: string): Submission {
  return {
    contestant: { slot, providerId: 'deepseek', modelId: 'deepseek-chat', apiKey: 'sk-test' },
    text,
    code: null,
    usage: null,
    latencyMs: 12,
    status: 'ok',
  }
}

const ROUND_ONE = judgeJson(
  '{"label":"A","logic":8,"evidence":7,"relevance":6,"rationale":"先手发挥稳"},' +
    '{"label":"B","logic":4,"evidence":3,"relevance":2,"rationale":"后手略显疲软"}',
)

const ROUND_TWO = judgeJson(
  '{"label":"A","logic":4,"evidence":3,"relevance":2,"rationale":"换了顺序也软"},' +
    '{"label":"B","logic":6,"evidence":5,"relevance":4,"rationale":"第二轮追分了"}',
)

function scriptedJudge(rounds: string[]): { call: JudgeCall; prompts: string[] } {
  const prompts: string[] = []
  let index = 0
  const call: JudgeCall = async ({ user }) => {
    prompts.push(user)
    const text = rounds[index] ?? rounds[rounds.length - 1] ?? ''
    index += 1
    return text
  }
  return { call, prompts }
}

async function judgeWith(rounds: string[]): Promise<{
  outcome: Awaited<ReturnType<typeof runJudge>>
  prompts: string[]
}> {
  const { call, prompts } = scriptedJudge(rounds)
  const options: JudgeOptions = {
    topic: '年轻人该不该躺平',
    submissions: [submission(1, '一号选手作答'), submission(2, '二号选手作答')],
    callJudge: call,
  }
  return { outcome: await runJudge(options), prompts }
}

test('runJudge 取三轮分项均值、按 slot 输出并在三轮一致时标记 agreed', async () => {
  const { outcome, prompts } = await judgeWith([ROUND_ONE, ROUND_TWO])
  assert.equal(outcome.unavailable, false)
  assert.equal(outcome.roundsAgreed, true)
  assert.equal(outcome.verdicts.length, 2)
  assert.equal(outcome.verdicts[0]?.contestantSlot, 1)
  assert.deepEqual(outcome.verdicts[0]?.scores, { logic: 6.7, evidence: 5.7, relevance: 4.7 })
  assert.equal(outcome.verdicts[1]?.contestantSlot, 2)
  assert.deepEqual(outcome.verdicts[1]?.scores, { logic: 4, evidence: 3, relevance: 2 })
  assert.ok(outcome.verdicts[0]?.rationale.includes('先手发挥稳'))
  assert.ok(outcome.verdicts[0]?.rationale.includes('第二轮追分了'))
  assert.equal(prompts.length, 3)
})

test('runJudge 第二轮交换出场顺序（匿名标签重新分配）', async () => {
  const { prompts } = await judgeWith([ROUND_ONE, ROUND_TWO])
  assert.ok(prompts[0]?.indexOf('一号选手作答') < prompts[0]?.indexOf('二号选手作答'))
  assert.ok(prompts[1]?.indexOf('二号选手作答') < prompts[1]?.indexOf('一号选手作答'))
  assert.ok(prompts[0]?.includes('年轻人该不该躺平'))
  assert.ok(prompts[0]?.includes('[A]'))
})

test('runJudge 一轮解析失败时降级为单轮结果且 roundsAgreed 为 false', async () => {
  const { outcome } = await judgeWith([ROUND_ONE, '裁判今天不想干活'])
  assert.equal(outcome.unavailable, false)
  assert.equal(outcome.roundsAgreed, false)
  assert.equal(outcome.verdicts.length, 2)
  assert.deepEqual(outcome.verdicts[0]?.scores, { logic: 8, evidence: 7, relevance: 6 })
})

test('runJudge 两轮都解析失败时返回 unavailable', async () => {
  const { outcome } = await judgeWith(['裁判罢工了', '完全没法解析'])
  assert.deepEqual(outcome.verdicts, [])
  assert.equal(outcome.roundsAgreed, false)
  assert.equal(outcome.unavailable, true)
})

test('runJudge 调用自身异常时返回 unavailable 且不抛出', async () => {
  const outcome = await runJudge({
    topic: '题目',
    submissions: [submission(1, '作答')],
    callJudge: async () => {
      throw new Error('裁判模型限流')
    },
  })
  assert.deepEqual(outcome.verdicts, [])
  assert.equal(outcome.unavailable, true)
})

test('runJudge 没有选手时返回 unavailable', async () => {
  const { call } = scriptedJudge([ROUND_ONE])
  const outcome = await runJudge({ topic: '题目', submissions: [], callJudge: call })
  assert.deepEqual(outcome.verdicts, [])
  assert.equal(outcome.unavailable, true)
})

test('runJudge 未提供裁判模型且未注入调用时返回 unavailable', async () => {
  const outcome = await runJudge({ topic: '题目', submissions: [submission(1, '作答')] })
  assert.deepEqual(outcome.verdicts, [])
  assert.equal(outcome.unavailable, true)
})

test('默认裁判调用走结构化输出，能把模型返回的 JSON 解析成 verdicts', async () => {
  const server = await startFakeServer({ chunks: [TWO_VERDICTS], latencyMs: 1 })
  try {
    const model = createOpenAI({ baseURL: server.baseUrl, apiKey: 'test-key' }).chat('fake-model')
    const outcome = await runJudge({
      topic: '年轻人该不该躺平',
      submissions: [submission(1, '一号选手作答'), submission(2, '二号选手作答')],
      judgeModel: model,
    })
    assert.equal(outcome.unavailable, false)
    assert.equal(outcome.verdicts.length, 2)
    assert.equal(outcome.verdicts[0]?.contestantSlot, 1)
    assert.deepEqual(outcome.verdicts[0]?.scores, { logic: 6, evidence: 6.3, relevance: 7.7 })
    assert.ok(server.paths.every((path) => path === '/chat/completions'))
  } finally {
    await server.close()
  }
})

test('provider 不支持 json_schema 时退回纯文本解析，裁判不降级', async () => {
  const server = await startFakeServer({
    chunks: [TWO_VERDICTS],
    latencyMs: 1,
    rejectJsonSchema: true,
  })
  try {
    const model = createOpenAI({ baseURL: server.baseUrl, apiKey: 'test-key' }).chat('fake-model')
    const outcome = await runJudge({
      topic: '年轻人该不该躺平',
      submissions: [submission(1, '一号选手作答'), submission(2, '二号选手作答')],
      judgeModel: model,
    })
    assert.equal(outcome.unavailable, false)
    assert.equal(outcome.verdicts.length, 2)
    assert.deepEqual(outcome.verdicts[0]?.scores, { logic: 6, evidence: 6.3, relevance: 7.7 })
  } finally {
    await server.close()
  }
})
