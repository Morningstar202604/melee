import path from 'node:path'
import { Worker } from 'node:worker_threads'
import type { SandboxBackend, SandboxResult, TestCase } from './backend.ts'

export const DEFAULT_TIME_LIMIT_MS = 15000
const WORKER_LOAD_TIMEOUT_MS = 60000

const WORKER_ENTRY = path.join(process.cwd(), 'workers', 'pyodide-worker.mjs')

interface RunInPyodideInput {
  code: string
  tests: TestCase[]
  timeLimitMs?: number
}

type WorkerResponse =
  | { type: 'ready' }
  | {
      type: 'result'
      status: SandboxResult['status']
      passed: number
      total: number
      timeMs: number
      stdoutTail: string
      errorMessage?: string
    }

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function normalizeResult(payload: WorkerResponse & { type: 'result' }, total: number, timeMs: number): SandboxResult {
  const result: SandboxResult = {
    status: payload.status,
    passed: Number(payload.passed) || 0,
    total: Number(payload.total) || total,
    timeMs: Number(payload.timeMs) || timeMs,
    stdoutTail: String(payload.stdoutTail || ''),
  }
  if (payload.errorMessage) {
    result.errorMessage = payload.errorMessage
  }
  return result
}

export async function runInPyodide(input: RunInPyodideInput): Promise<SandboxResult> {
  const tests = Array.isArray(input.tests) ? input.tests : []
  const total = tests.length
  const timeLimitMs = input.timeLimitMs ?? DEFAULT_TIME_LIMIT_MS
  const startedAt = Date.now()

  const worker = new Worker(WORKER_ENTRY, { workerData: {} })

  return await new Promise<SandboxResult>((resolve) => {
    let settled = false
    let runTimer: ReturnType<typeof setTimeout> | undefined
    let loadTimer: ReturnType<typeof setTimeout> | undefined

    const finish = (result: SandboxResult) => {
      if (settled) return
      settled = true
      if (runTimer) clearTimeout(runTimer)
      if (loadTimer) clearTimeout(loadTimer)
      runTimer = undefined
      loadTimer = undefined
      worker.terminate().catch(() => {})
      resolve(result)
    }

    const abortWithTimeout = () => {
      finish({
        status: 'timeout',
        passed: 0,
        total,
        timeMs: Date.now() - startedAt,
        stdoutTail: '',
        errorMessage: `超过 ${timeLimitMs}ms 未执行完，Worker 已被强制终止`,
      })
    }

    worker.on('message', (value: WorkerResponse) => {
      if (!value || typeof value !== 'object') return
      if (value.type === 'ready') {
        if (loadTimer) clearTimeout(loadTimer)
        loadTimer = undefined
        runTimer = setTimeout(abortWithTimeout, timeLimitMs)
        return
      }
      if (value.type === 'result') {
        finish(normalizeResult(value, total, Date.now() - startedAt))
      }
    })

    worker.on('error', (error: unknown) => {
      finish({
        status: 'error',
        passed: 0,
        total,
        timeMs: Date.now() - startedAt,
        stdoutTail: '',
        errorMessage: `沙箱 Worker 异常：${toErrorMessage(error)}`,
      })
    })

    worker.on('exit', (code: number) => {
      if (settled) return
      finish({
        status: 'error',
        passed: 0,
        total,
        timeMs: Date.now() - startedAt,
        stdoutTail: '',
        errorMessage: code === 0 ? '沙箱 Worker 提前退出' : `沙箱 Worker 异常退出（code=${code}）`,
      })
    })

    loadTimer = setTimeout(() => {
      finish({
        status: 'error',
        passed: 0,
        total,
        timeMs: Date.now() - startedAt,
        stdoutTail: '',
        errorMessage: '沙箱运行时加载超时',
      })
    }, WORKER_LOAD_TIMEOUT_MS)

    worker.postMessage({ type: 'run', code: input.code, tests })
  })
}

export const pyodideBackend: SandboxBackend = {
  id: 'pyodide',
  isolationLevel: 'wasm-worker',
  run: (input) => runInPyodide({ code: input.code, tests: input.tests, timeLimitMs: input.timeLimitMs }),
}
