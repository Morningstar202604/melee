import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { rmSync } from 'node:fs'
import { join } from 'node:path'
import { createHardCodeArena, extractCode, judgeCodeSubmissions } from './hard-code.ts'
import { getCodeTopic } from './topics.ts'
import type { SandboxBackend } from '../sandbox/backend.ts'
import type { SandboxProgress, Submission } from './types.ts'
import { closeDb } from '../db/client.ts'

const dbFile = join(
  'data',
  `test-hardcode-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.db`,
)

before(() => {
  process.env.MELEE_DB = dbFile
})

after(() => {
  closeDb()
  for (const suffix of ['', '-wal', '-shm']) rmSync(dbFile + suffix, { force: true })
})

const TESTS = [
  { input: '1\n', expected: '1\n' },
  { input: '2\n', expected: '2\n' },
]

function submission(slot: number, code: string | null): Submission {
  return {
    contestant: { slot, providerId: 'ollama', modelId: 'm', apiKey: 'k' },
    text: code ?? '',
    code,
    usage: null,
    latencyMs: 5,
    status: 'ok',
  }
}

test('extractCode 取第一个代码块', () => {
  const text = '先解释一下\n```python\nprint("a")\n```\n然后是第二个\n```python\nprint("b")\n```'
  assert.equal(extractCode(text), 'print("a")')
})

test('extractCode 对无代码块返回 null', () => {
  assert.equal(extractCode('就是一段普通文字，没有围栏'), null)
  assert.equal(extractCode(''), null)
})

test('extractCode 带语言标识的围栏也能取', () => {
  const text = '```py\nx = 1\n```'
  assert.equal(extractCode(text), 'x = 1')
})

test('extractCode 围栏未闭合时仍尽力取出内容', () => {
  const text = '一些前言\n```python\nwhile True: pass'
  assert.equal(extractCode(text), 'while True: pass')
})

test('extractCode 对纯空白输入返回 null', () => {
  assert.equal(extractCode('   '), null)
})

test('extractCode 不带语言标识的围栏也能取', () => {
  const text = '```\nhello\n```'
  assert.equal(extractCode(text), 'hello')
})

test('judgeCodeSubmissions 并行跑沙箱并按选手逐条上报进度', async () => {
  let inflight = 0
  let maxInflight = 0
  const progress: SandboxProgress[] = []
  const sandbox: SandboxBackend = {
    id: 'pyodide',
    isolationLevel: 'wasm-worker',
    async run(input) {
      inflight += 1
      maxInflight = Math.max(maxInflight, inflight)
      await new Promise((resolve) => setTimeout(resolve, 10))
      inflight -= 1
      return {
        status: 'ok',
        passed: input.tests.length,
        total: input.tests.length,
        timeMs: 10,
        stdoutTail: '',
      }
    },
  }

  const verdicts = await judgeCodeSubmissions(
    [submission(1, 'print(1)'), submission(2, 'print(2)'), submission(3, 'print(3)')],
    {
      tests: TESTS,
      sandbox,
      timeLimitMs: 1000,
      onSandboxResult: (item) => progress.push(item),
    },
  )

  assert.equal(maxInflight, 3, '三名选手的沙箱执行必须并发进入，而不是串行')
  assert.equal(verdicts.length, 3)
  assert.equal(verdicts[0]?.passed, 2)
  assert.deepEqual(
    progress.map((item) => item.slot).sort((a, b) => a - b),
    [1, 2, 3],
  )
  assert.ok(progress.every((item) => item.status === 'ok' && item.total === 2))
})

test('judgeCodeSubmissions 对未产出代码的选手不调用沙箱，并上报 unavailable', async () => {
  let calls = 0
  const progress: SandboxProgress[] = []
  const sandbox: SandboxBackend = {
    id: 'pyodide',
    isolationLevel: 'wasm-worker',
    async run() {
      calls += 1
      return { status: 'ok', passed: 0, total: 0, timeMs: 0, stdoutTail: '' }
    },
  }

  const verdicts = await judgeCodeSubmissions([submission(1, null)], {
    tests: TESTS,
    sandbox,
    timeLimitMs: 1000,
    onSandboxResult: (item) => progress.push(item),
  })

  assert.equal(calls, 0)
  assert.equal(verdicts[0]?.rationale, '未产出可执行代码')
  assert.equal(progress[0]?.status, 'unavailable')
})

function workingSandbox(): { sandbox: SandboxBackend; calls: () => number } {
  let calls = 0
  const sandbox: SandboxBackend = {
    id: 'pyodide',
    isolationLevel: 'wasm-worker',
    async run(input) {
      calls += 1
      return {
        status: 'ok',
        passed: input.tests.length,
        total: input.tests.length,
        timeMs: 5,
        stdoutTail: '',
      }
    },
  }
  return { sandbox, calls: () => calls }
}

test('hardCodeArena.judge 接受题面文本作为 topic（前端与 API 的发送形态）', async () => {
  const topic = getCodeTopic('code-two-sum')
  assert.ok(topic)
  const { sandbox, calls } = workingSandbox()
  const arena = createHardCodeArena(sandbox)

  const verdicts = await arena.judge([submission(1, 'print(1)'), submission(2, 'print(2)')], {
    topic: topic.prompt,
    judgeModelId: '',
    judgeApiKey: '',
  })

  assert.equal(calls(), 2, '题面文本必须解析到题目并真正跑沙箱')
  assert.ok(
    verdicts.every((verdict) => verdict.total === topic.tests.length),
    '用例总数必须来自题目定义，不能是 0',
  )
  assert.ok(
    verdicts.every((verdict) => verdict.passed === topic.tests.length),
    '全部通过时 passed 必须等于用例数',
  )
  assert.ok(
    verdicts.every((verdict) => verdict.rationale !== '题目不存在'),
    '题面文本不应被判定为题目不存在',
  )
})

test('hardCodeArena.judge 也接受题目 id（程序化调用兼容）', async () => {
  const topic = getCodeTopic('code-two-sum')
  assert.ok(topic)
  const { sandbox, calls } = workingSandbox()
  const arena = createHardCodeArena(sandbox)

  const verdicts = await arena.judge([submission(1, 'print(1)')], {
    topic: 'code-two-sum',
    judgeModelId: '',
    judgeApiKey: '',
  })

  assert.equal(calls(), 1)
  assert.equal(verdicts[0]?.total, topic.tests.length)
  assert.equal(verdicts[0]?.passed, topic.tests.length)
})
