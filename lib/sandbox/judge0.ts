import type { SandboxBackend } from './backend.ts'

const LANGUAGE_ID_PYTHON3 = 100
const DEFAULT_POLL_INTERVAL_MS = 1000
const DEFAULT_MAX_POLLS = 30
const FETCH_TIMEOUT_MS = 30000

function pollIntervalMs(): number {
  const value = Number(process.env.JUDGE0_POLL_INTERVAL_MS ?? DEFAULT_POLL_INTERVAL_MS)
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_POLL_INTERVAL_MS
}

function maxPolls(): number {
  const value = Number(process.env.JUDGE0_MAX_POLLS ?? DEFAULT_MAX_POLLS)
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_MAX_POLLS
}

const STATUS_ACCEPTED = 3
const STATUS_WRONG_ANSWER = 4
const STATUS_TIME_LIMIT = 5
const STATUS_COMPILE_ERROR = 6
const STATUS_INTERNAL_ERROR = 13

interface Judge0Status {
  id: number
  description?: string
}

interface Judge0SubmissionResult {
  token?: string
  status?: Judge0Status
  stdout?: string | null
  stderr?: string | null
  compile_output?: string | null
  time?: string | null
  memory?: number | null
}

function baseUrl(): string {
  const raw = (process.env.JUDGE0_BASE_URL ?? '').trim().replace(/\/+$/, '')
  if (!raw) throw new Error('judge0 后端需要 JUDGE0_BASE_URL 环境变量')
  return raw
}

function authHeaders(): Record<string, string> {
  const header = (process.env.JUDGE0_AUTHN_HEADER ?? '').trim()
  if (!header) return {}
  const separator = header.indexOf(':')
  if (separator <= 0) return {}
  const key = header.slice(0, separator).trim()
  const value = header.slice(separator + 1).trim()
  if (!key || !value) return {}
  return { [key]: value }
}

const BATCH_FIELDS = 'status,stdout,stderr,compile_output,time,memory'
const COMMON_LIMITS = {
  language_id: LANGUAGE_ID_PYTHON3,
  wall_time_limit: 5,
  memory_limit: 256000,
  max_processes_and_or_threads: 32,
  max_file_size: 1024,
  enable_network: false,
} as const

/** 官方 batch 提交：每个测试用例各自一份提交，携带 expected_output 由 Judge0 判定。 */
async function submitBatch(
  code: string,
  tests: Array<{ input: string; expected: string }>,
  signal: AbortSignal,
): Promise<string[]> {
  const response = await fetch(`${baseUrl()}/submissions/batch?base64_encoded=false`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({
      submissions: tests.map((test) => ({
        source_code: code,
        stdin: test.input,
        expected_output: test.expected,
        ...COMMON_LIMITS,
      })),
    }),
    signal,
  })
  if (!response.ok) {
    throw new Error(`Judge0 批量提交失败：HTTP ${response.status}`)
  }
  const payload = (await response.json()) as { submissions?: Array<{ token?: string }> }
  const tokens = (payload.submissions ?? [])
    .map((item) => item?.token)
    .filter((token): token is string => typeof token === 'string' && token.length > 0)
  if (tokens.length !== tests.length) {
    throw new Error(`Judge0 批量提交返回的 token 数量不符：${tokens.length}/${tests.length}`)
  }
  return tokens
}

async function pollBatch(tokens: string[], signal: AbortSignal): Promise<Judge0SubmissionResult[] | null> {
  const attempts = maxPolls()
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs()))
    const response = await fetch(
      `${baseUrl()}/submissions/batch?tokens=${tokens.join(',')}&base64_encoded=false&fields=${BATCH_FIELDS}`,
      { headers: { ...authHeaders() }, signal },
    )
    if (!response.ok) {
      throw new Error(`Judge0 批量轮询失败：HTTP ${response.status}`)
    }
    const payload = (await response.json()) as { submissions?: Judge0SubmissionResult[] }
    const results = payload.submissions ?? []
    if (results.length !== tokens.length) continue
    const allSettled = results.every((item) => (item.status?.id ?? 0) > 2)
    if (allSettled) return results
  }
  return null
}

function sumTimeMs(results: Judge0SubmissionResult[]): number {
  let totalMs = 0
  for (const item of results) {
    const seconds = item.time ? Number.parseFloat(item.time) : 0
    if (Number.isFinite(seconds)) totalMs += Math.round(seconds * 1000)
  }
  return totalMs
}

function tailOf(results: Judge0SubmissionResult[]): string {
  const parts: string[] = []
  for (const item of results) {
    const text = (item.compile_output ?? '').trim() || (item.stderr ?? '').trim() || (item.stdout ?? '').trim()
    if (text) parts.push(text)
  }
  return parts.join('\n---\n').slice(-2000)
}

export const judge0Backend: SandboxBackend = {
  id: 'judge0',
  isolationLevel: 'os-container',
  async run(input) {
    const tests = input.tests.map((test) => ({ input: test.input, expected: test.expected }))
    const total = tests.length
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)

    try {
      if (total === 0) {
        return { status: 'ok', passed: 0, total: 0, timeMs: 0, stdoutTail: '' }
      }

      const tokens = await submitBatch(input.code, tests, controller.signal)
      const results = await pollBatch(tokens, controller.signal)

      if (results === null) {
        return {
          status: 'timeout',
          passed: 0,
          total,
          timeMs: 0,
          stdoutTail: '',
          errorMessage: `轮询超过 ${maxPolls()} 次仍未出结果`,
        }
      }

      const compileError = results.find((item) => item.status?.id === STATUS_COMPILE_ERROR)
      if (compileError) {
        return {
          status: 'error',
          passed: 0,
          total,
          timeMs: sumTimeMs(results),
          stdoutTail: tailOf(results),
          errorMessage: '编译错误',
        }
      }

      const passed = results.filter((item) => item.status?.id === STATUS_ACCEPTED).length
      const timeMs = sumTimeMs(results)
      const statusIds = results.map((item) => item.status?.id ?? 0)

      if (passed === 0 && statusIds.every((id) => id === STATUS_TIME_LIMIT)) {
        return {
          status: 'timeout',
          passed: 0,
          total,
          timeMs,
          stdoutTail: tailOf(results),
          errorMessage: '全部用例超时',
        }
      }

      const hardFailure = results.find((item) => {
        const id = item.status?.id ?? 0
        return id >= 7 && id <= 12
      })
      if (passed === 0 && hardFailure) {
        return {
          status: 'error',
          passed: 0,
          total,
          timeMs,
          stdoutTail: tailOf(results),
          errorMessage: hardFailure.status?.description ?? `运行时错误（状态 ${hardFailure.status?.id}）`,
        }
      }

      if (passed === 0 && statusIds.some((id) => id === STATUS_INTERNAL_ERROR)) {
        return {
          status: 'error',
          passed: 0,
          total,
          timeMs,
          stdoutTail: tailOf(results),
          errorMessage: 'Judge0 内部错误',
        }
      }

      return { status: 'ok', passed, total, timeMs, stdoutTail: tailOf(results) }
    } catch (error) {
      return {
        status: 'error',
        passed: 0,
        total,
        timeMs: 0,
        stdoutTail: '',
        errorMessage: error instanceof Error ? error.message : String(error),
      }
    } finally {
      clearTimeout(timer)
    }
  },
}

export const JUDGE0_STATUS = {
  ACCEPTED: STATUS_ACCEPTED,
  WRONG_ANSWER: STATUS_WRONG_ANSWER,
}
