import type { SandboxBackend, SandboxResult, TestCase } from './backend.ts'

const LANGUAGE_ID_PYTHON3 = 100
const POLL_INTERVAL_MS = 1000
const MAX_POLLS = 30

interface Judge0SubmissionResponse {
  token: string
}

interface Judge0Status {
  id: number
  description: string
}

interface Judge0SubmissionResult {
  status?: Judge0Status
  stdout?: string | null
  stderr?: string | null
  compile_output?: string | null
  time?: string | null
  memory?: number | null
  exit_code?: number | null
  exit_signal?: number | null
  message?: string | null
}

type JudgmentStatus = 'ok' | 'error' | 'timeout'

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

async function submitToJudge0(code: string, tests: TestCase[]): Promise<string> {
  const combinedInput = tests.map((t) => t.input).join('')
  const response = await fetch(`${baseUrl()}/submissions?base64_encoded=false&wait=false`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({
      source_code: code,
      language_id: LANGUAGE_ID_PYTHON3,
      stdin: combinedInput,
      wall_time_limit: 5,
      memory_limit: 256000,
      max_processes_and_or_threads: 32,
      max_file_size: 1024,
      enable_network: false,
    }),
  })
  if (!response.ok) {
    throw new Error(`Judge0 提交失败：HTTP ${response.status}`)
  }
  const payload = (await response.json()) as Judge0SubmissionResponse
  if (!payload.token) throw new Error('Judge0 响应缺少 token')
  return payload.token
}

async function pollSubmission(token: string, total: number): Promise<SandboxResult> {
  for (let i = 0; i < MAX_POLLS; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    const response = await fetch(`${baseUrl()}/submissions/${token}?base64_encoded=false&fields=status,stdout,stderr,compile_output,time,memory,exit_code,exit_signal,message`, {
      headers: { ...authHeaders() },
    })
    if (!response.ok) {
      throw new Error(`Judge0 轮询失败：HTTP ${response.status}`)
    }
    const result = (await response.json()) as Judge0SubmissionResult
    const statusId = result.status?.id ?? 0
    if (statusId <= 2) continue

    const timeMs = result.time ? Math.round(parseFloat(result.time) * 1000) : 0
    const memoryKb = result.memory ? Math.round(result.memory) : 0
    const stdout = result.stdout ?? ''
    const stderr = result.stderr ?? ''
    const compileOutput = result.compile_output ?? ''

    if (statusId === 3) {
      return { status: 'ok', passed: total, total, timeMs, stdoutTail: stdout.slice(-2000) }
    }
    if (statusId === 4) {
      return { status: 'error', passed: 0, total, timeMs, stdoutTail: stdout.slice(-2000), errorMessage: '答案错误' }
    }
    if (statusId === 5 || statusId === 6) {
      return { status: 'timeout', passed: 0, total, timeMs, stdoutTail: stdout.slice(-2000), errorMessage: '执行超时' }
    }
    if (statusId === 7) {
      return { status: 'error', passed: 0, total, timeMs, stdoutTail: compileOutput.slice(-2000), errorMessage: '编译错误' }
    }
    return {
      status: 'error',
      passed: 0,
      total,
      timeMs,
      stdoutTail: stderr.slice(-2000) || (result.message ?? '').slice(-2000),
      errorMessage: result.status?.description ?? `Judge0 异常状态 ${statusId}`,
    }
  }
  return { status: 'timeout', passed: 0, total, timeMs: 0, stdoutTail: '', errorMessage: 'Judge0 轮询超时' }
}

export const judge0Backend: SandboxBackend = {
  id: 'judge0',
  isolationLevel: 'os-container',
  async run(input) {
    const total = input.tests.length
    try {
      const token = await submitToJudge0(input.code, input.tests)
      const result = await pollSubmission(token, total)
      return { ...result, timeMs: result.timeMs ?? 0 }
    } catch (error) {
      return {
        status: 'error',
        passed: 0,
        total,
        timeMs: 0,
        stdoutTail: '',
        errorMessage: error instanceof Error ? error.message : String(error),
      }
    }
  },
}
