import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startFakeJudge0, type FakeJudge0Server } from '../../test/fixtures/fake-judge0-server.ts'
import { judge0Backend } from './judge0.ts'

const TESTS = [
  { input: '1 2\n', expected: '3\n' },
  { input: '0 0\n', expected: '0\n' },
  { input: '-1 1\n', expected: '0\n' },
]

let server: FakeJudge0Server

before(() => {
  process.env.JUDGE0_POLL_INTERVAL_MS = '5'
  process.env.JUDGE0_MAX_POLLS = '10'
})

test('（前置）轮询参数按调用时读取环境变量', () => {
  assert.equal(process.env.JUDGE0_MAX_POLLS, '10')
})

after(async () => {
  await server.close()
})

async function withServer(
  options: Parameters<typeof startFakeJudge0>[0],
  run: (value: FakeJudge0Server) => Promise<void>,
): Promise<void> {
  server = await startFakeJudge0(options)
  process.env.JUDGE0_BASE_URL = server.baseUrl
  process.env.JUDGE0_AUTHN_HEADER = 'X-Auth-Token: test-token'
  try {
    await run(server)
  } finally {
    await server.close()
    delete process.env.JUDGE0_BASE_URL
    delete process.env.JUDGE0_AUTHN_HEADER
  }
}

test('每个测试用例各自提交一份（官方 batch 语义），全 Accepted 通过率 3/3', async () => {
  await withServer({}, async (fake) => {
    const result = await judge0Backend.run({ code: 'print(1)', tests: TESTS, timeLimitMs: 5000 })
    assert.equal(result.status, 'ok')
    assert.equal(result.passed, 3)
    assert.equal(result.total, 3)

    assert.equal(fake.submittedBatches.length, 1)
    const batch = fake.submittedBatches[0]!
    assert.equal(batch.length, 3)
    assert.deepEqual(
      batch.map((item) => item.stdin),
      TESTS.map((item) => item.input),
    )
    assert.deepEqual(
      batch.map((item) => item.expected_output),
      TESTS.map((item) => item.expected),
    )
    assert.ok(fake.paths.some((path) => path.includes('/submissions/batch')))
  })
})

test('部分用例 Wrong Answer：通过数按 Accepted 逐个数出，不折算', async () => {
  await withServer(
    {
      resultFor: (index) =>
        index === 1 ? { statusId: 4, stdout: '5\n', time: '0.02' } : { statusId: 3, stdout: '3\n', time: '0.03' },
    },
    async () => {
      const result = await judge0Backend.run({ code: 'print(1)', tests: TESTS, timeLimitMs: 5000 })
      assert.equal(result.status, 'ok')
      assert.equal(result.passed, 2)
      assert.equal(result.total, 3)
    },
  )
})

test('编译错误直接判 error 并给出可读原因', async () => {
  await withServer(
    {
      resultFor: () => ({ statusId: 6, compileOutput: "SyntaxError: invalid syntax" }),
    },
    async () => {
      const result = await judge0Backend.run({ code: 'print(', tests: TESTS, timeLimitMs: 5000 })
      assert.equal(result.status, 'error')
      assert.equal(result.passed, 0)
      assert.equal(result.errorMessage, '编译错误')
    },
  )
})

test('全部用例超时判 timeout', async () => {
  await withServer(
    {
      resultFor: () => ({ statusId: 5 }),
    },
    async () => {
      const result = await judge0Backend.run({ code: 'while True: pass', tests: TESTS, timeLimitMs: 5000 })
      assert.equal(result.status, 'timeout')
      assert.equal(result.errorMessage, '全部用例超时')
    },
  )
})

test('运行时错误（无 Accepted）判 error 并带 Judge0 状态描述', async () => {
  await withServer(
    {
      resultFor: () => ({ statusId: 7 }),
    },
    async () => {
      const result = await judge0Backend.run({ code: 'raise SystemExit(1)', tests: TESTS, timeLimitMs: 5000 })
      assert.equal(result.status, 'error')
      assert.ok((result.errorMessage ?? '').includes('Runtime Error'))
    },
  )
})

test('排队若干轮后出结果：轮询会等到 status>2', async () => {
  await withServer({ queueRounds: 3 }, async () => {
    const result = await judge0Backend.run({ code: 'print(1)', tests: TESTS, timeLimitMs: 5000 })
    assert.equal(result.status, 'ok')
    assert.equal(result.passed, 3)
  })
})

test('轮询耗尽仍未出结果判 timeout 且不抛异常', async () => {
  await withServer({ queueRounds: 999 }, async () => {
    const result = await judge0Backend.run({ code: 'print(1)', tests: TESTS, timeLimitMs: 5000 })
    assert.equal(result.status, 'timeout')
    assert.ok((result.errorMessage ?? '').includes('轮询超过'))
  })
})

test('缺少 JUDGE0_BASE_URL 时返回 error 结果而不抛出', async () => {
  delete process.env.JUDGE0_BASE_URL
  const result = await judge0Backend.run({ code: 'print(1)', tests: TESTS, timeLimitMs: 5000 })
  assert.equal(result.status, 'error')
  assert.ok((result.errorMessage ?? '').includes('JUDGE0_BASE_URL'))
})
