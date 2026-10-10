import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'
import { getBackend } from './backend.ts'
import { DEFAULT_TIME_LIMIT_MS, runInPyodide } from './pyodide-worker.ts'

test('getBackend 默认返回 pyodide 后端且隔离级别为 wasm-worker', async () => {
  const backend = await getBackend()
  assert.equal(backend.id, 'pyodide')
  assert.equal(backend.isolationLevel, 'wasm-worker')
})

test('getBackend 在 SANDBOX_BACKEND=judge0 时报尚未实现', async () => {
  const previous = process.env.SANDBOX_BACKEND
  process.env.SANDBOX_BACKEND = 'judge0'
  try {
    await assert.rejects(() => getBackend(), /尚未实现/)
  } finally {
    if (previous === undefined) delete process.env.SANDBOX_BACKEND
    else process.env.SANDBOX_BACKEND = previous
  }
})

test('Worker 入口不含宿主凭证读取（不读 process.env、不引入项目模块）', () => {
  const source = readFileSync(
    path.join(process.cwd(), 'workers', 'pyodide-worker.mjs'),
    'utf8',
  )
  assert.ok(!source.includes('process.env'), 'Worker 不得读取 process.env')
  assert.ok(!/\brequire\s*\(/.test(source), 'Worker 不得使用 require')
  assert.ok(!/from\s+'\.\.?\//.test(source), 'Worker 不得引入项目内相对模块')
  assert.ok(source.includes("from 'pyodide'"), 'Worker 必须从 pyodide 包导入 loadPyodide')
  assert.ok(source.includes('node_modules'), 'indexURL 必须指向本地 node_modules/pyodide')
})

test('正常代码通过全部用例', async () => {
  const result = await runInPyodide({
    code: [
      'def solve(text):',
      "    parts = text.split()",
      '    return str(sum(int(x) for x in parts))',
    ].join('\n'),
    tests: [
      { input: '1 2\n', expected: '3\n' },
      { input: '10 -3 5\n', expected: '12\n' },
      { input: '0\n', expected: '0\n' },
    ],
  })

  assert.equal(result.status, 'ok')
  assert.equal(result.passed, result.total)
  assert.equal(result.total, 3)
  assert.ok(result.stdoutTail.includes('3'), `stdoutTail 缺 3：${result.stdoutTail}`)
  assert.ok(result.stdoutTail.includes('12'), `stdoutTail 缺 12：${result.stdoutTail}`)
})

test('沙箱输出超过 64KB 上限即中止，不得继续跑完', async () => {
  const result = await runInPyodide({
    code: [
      'def solve(text):',
      '    for i in range(3000):',
      "        print('spam-' + 'x' * 200)",
      "    return 'done'",
    ].join('\n'),
    tests: [
      { input: '', expected: 'done' },
      { input: '', expected: 'done' },
    ],
  })

  assert.equal(result.status, 'error')
  assert.equal(result.passed, 0)
  assert.match(result.errorMessage ?? '', /65536|上限/)
})

test('死循环被 terminate 截断，返回 timeout', async () => {
  const startedAt = Date.now()
  const result = await runInPyodide({
    code: 'i = 0\nwhile True:\n    i += 1\n',
    tests: [{ input: '', expected: '' }],
    timeLimitMs: 5000,
  })
  const elapsed = Date.now() - startedAt

  assert.equal(result.status, 'timeout')
  assert.equal(result.passed, 0)
  assert.equal(result.total, 1)
  assert.ok(elapsed < 20000, `耗时 ${elapsed}ms 超过 20s 上限`)
})

test('代码读 os.environ 拿不到任何值', async () => {
  const result = await runInPyodide({
    code: [
      'import json',
      'import os',
      '',
      'def solve(text):',
      '    lines = []',
      "    for name in ['PATH', 'USERPROFILE', 'HOME', 'APPDATA', 'COMPUTERNAME', 'TEMP']:",
      '        try:',
      "            lines.append(name + '=' + os.environ[name])",
      '        except KeyError:',
      "            lines.append(name + '=KeyError')",
      "    lines.append('ENV=' + json.dumps(dict(os.environ)))",
      "    return '\\n'.join(lines)",
    ].join('\n'),
    tests: [{ input: '', expected: '不应相等' }],
    timeLimitMs: DEFAULT_TIME_LIMIT_MS,
  })

  assert.equal(result.status, 'ok')
  assert.ok(
    result.stdoutTail.includes('KeyError'),
    `输出未出现 KeyError：${result.stdoutTail}`,
  )
  assert.ok(
    result.stdoutTail.includes('ENV={}'),
    `os.environ 未被清空：${result.stdoutTail}`,
  )

  const hostValues = Object.values(process.env).filter(
    (value): value is string => typeof value === 'string' && value.length >= 5,
  )
  for (const value of hostValues) {
    assert.ok(
      !result.stdoutTail.includes(value),
      `宿主机环境变量值泄漏进沙箱输出：${value}`,
    )
  }
})
