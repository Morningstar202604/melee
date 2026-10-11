import type { SandboxBackend } from './backend.ts'

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

async function submitToJudge0(code: string, stdin: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`${baseUrl()}/submissions?base64_encoded=false&wait=false`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify({
      source_code: code,
      language_id: LANGUAGE_ID_PYTHON3,
      stdin,
      wall_time_limit: 5,
      memory_limit: 256000,
      max_processes_and_or_threads: 32,
      max_file_size: 1024,
      enable_network: false,
    }),
    signal,
  })
  if (!response.ok) {
    throw new Error(`Judge0 提交失败：HTTP ${response.status}`)
  }
  const payload = (await response.json()) as Judge0SubmissionResponse
  if (!payload.token) throw new Error('Judge0 响应缺少 token')
  return payload.token
}

async function pollSubmission(token: string, signal?: AbortSignal): Promise<Judge0SubmissionResult> {
  for (let i = 0; i < MAX_POLLS; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
    const response = await fetch(`${baseUrl()}/submissions/${token}?base64_encoded=false&fields=status,stdout,stderr,compile_output,time,memory,exit_code,exit_signal,message`, {
      headers: { ...authHeaders() },
      signal,
    })
    if (!response.ok) {
      throw new Error(`Judge0 轮询失败：HTTP ${response.status}`)
    }
    const result = (await response.json()) as Judge0SubmissionResult
    const statusId = result.status?.id ?? 0
    if (statusId <= 2) continue
    return result
  }
  return { status: { id: 5, description: '轮询超时' } }
}

function compareOutputs(actual: string, expected: string): boolean {
  const normalize = (s: string) => s.replace(/\r\n/g, '\n').trim()
  return normalize(actual) === normalize(expected)
}

const FETCH_TIMEOUT_MS = 30000

export const judge0Backend: SandboxBackend = {
  id: 'judge0',
  isolationLevel: 'os-container',
  async run(input) {
    const total = input.tests.length
    const combinedInput = input.tests.map((t) => t.input).join('')
    const combinedExpected = input.tests.map((t) => t.expected).join('\n')
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS)

    try {
      const token = await submitToJudge0(input.code, combinedInput, controller.signal)
      const result = await pollSubmission(token, controller.signal)

      const timeMs = result.time ? Math.round(parseFloat(result.time) * 1000) : 0
      const stdout = result.stdout ?? ''
      const stderr = result.stderr ?? ''
      const compileOutput = result.compile_output ?? ''
      const statusId = result.status?.id ?? 0

      if (statusId === 11) {
        return {
          status: 'error',
          passed: 0,
          total,
          timeMs,
          stdoutTail: compileOutput.slice(-2000),
          errorMessage: '编译错误',
        }
      }

      if (statusId === 5 || statusId === 6 || statusId === 7 || statusId === 8 || statusId === 9 || statusId === 10) {
        return {
          status: 'timeout',
          passed: 0,
          total,
          timeMs,
          stdoutTail: stdout.slice(-2000),
          errorMessage: result.status?.description ?? '执行超时',
        }
      }

      if (statusId >= 4 && statusId <= 13) {
        const errorMessage =
          statusId === 4 ? '答案错误' :
          statusId === 12 ? '内存超限' :
          result.status?.description ?? `Judge0 异常状态 ${statusId}`
        return {
          status: 'error',
          passed: 0,
          total,
          timeMs,
          stdoutTail: stderr.slice(-2000) || stdout.slice(-2000),
          errorMessage,
        }
      }

      if (statusId === 3) {
        const combinedActual = stdout.trimEnd()
        const actualLines = combinedActual.split('\n').filter((l) => l.length > 0)
        const expectedLines = combinedExpected.split('\n').filter((l) => l.length > 0)
        let passed = 0
        const maxLen = Math.max(actualLines.length, expectedLines.length)
        for (let i = 0; i < maxLen; i += 1) {
          if (compareOutputs(actualLines[i] ?? '', expectedLines[i] ?? '')) {
            passed += 1
          }
        }
        if (total > 0) {
          const ratioPassed = Math.round((passed / Math.max(expectedLines.length, 1)) * total)
          passed = Math.min(ratioPassed, total)
        }
        return {
          status: 'ok',
          passed,
          total,
          timeMs,
          stdoutTail: stdout.slice(-2000),
        }
      }

      return {
        status: 'error',
        passed: 0,
        total,
        timeMs,
        stdoutTail: stderr.slice(-2000),
        errorMessage: result.status?.description ?? `Judge0 未知状态 ${statusId}`,
      }
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
