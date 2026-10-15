import path from 'node:path'
import { Worker } from 'node:worker_threads'
import { logger } from '../log/logger.ts'
import type { SandboxBackend, SandboxResult, TestCase } from './backend.ts'

export const DEFAULT_TIME_LIMIT_MS = 15000
const WORKER_LOAD_TIMEOUT_MS = 60000
const POOL_SIZE = 2

const WORKER_ENTRY = path.join(process.cwd(), 'workers', 'pyodide-worker.mjs')

interface RunInPyodideInput {
  code: string
  tests: TestCase[]
  timeLimitMs?: number
}

type WorkerMessage =
  | { type: 'ready' }
  | { type: 'boot-error'; errorMessage?: string }
  | {
      type: 'result'
      status: SandboxResult['status']
      passed: number
      total: number
      timeMs: number
      stdoutTail: string
      errorMessage?: string
    }

interface PooledWorker {
  worker: Worker
  loaded: boolean
  dead: boolean
}

const idle: PooledWorker[] = []
const waiters: Array<(worker: PooledWorker) => void> = []
const alive = new Set<PooledWorker>()

function toErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  return String(error)
}

function spawnWorker(): PooledWorker {
  // 该 worker 会被打包器编译成 chunk；pyodide 必须保持外部化（见 next.config.ts
  // 的 serverExternalPackages），否则其运行时资源加载会被替换成抛错桩。
  const worker = new Worker(WORKER_ENTRY, { workerData: {} })
  const pooled: PooledWorker = { worker, loaded: false, dead: false }
  alive.add(pooled)
  worker.unref()
  worker.on('exit', () => {
    alive.delete(pooled)
    pooled.dead = true
    const index = idle.indexOf(pooled)
    if (index >= 0) idle.splice(index, 1)
    const waiter = waiters.shift()
    if (waiter) waiter(spawnWorker())
  })
  return pooled
}

function acquire(): Promise<PooledWorker> {
  const free = idle.pop()
  if (free && !free.dead) return Promise.resolve(free)
  if (alive.size < POOL_SIZE) return Promise.resolve(spawnWorker())
  return new Promise((resolve) => waiters.push(resolve))
}

function release(pooled: PooledWorker): void {
  if (pooled.dead) return
  const waiter = waiters.shift()
  if (waiter) {
    waiter(pooled)
    return
  }
  idle.push(pooled)
}

function discard(pooled: PooledWorker): void {
  if (pooled.dead) return
  pooled.dead = true
  void pooled.worker.terminate().catch(() => {})
}

function normalizeResult(
  payload: Extract<WorkerMessage, { type: 'result' }>,
  total: number,
  timeMs: number,
): SandboxResult {
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

function runOn(pooled: PooledWorker, input: RunInPyodideInput): Promise<SandboxResult> {
  const tests = Array.isArray(input.tests) ? input.tests : []
  const total = tests.length
  const timeLimitMs = input.timeLimitMs ?? DEFAULT_TIME_LIMIT_MS
  const startedAt = Date.now()

  return new Promise<SandboxResult>((resolve) => {
    let settled = false
    let runTimer: ReturnType<typeof setTimeout> | undefined
    let loadTimer: ReturnType<typeof setTimeout> | undefined

    const cleanup = () => {
      if (runTimer) clearTimeout(runTimer)
      if (loadTimer) clearTimeout(loadTimer)
      runTimer = undefined
      loadTimer = undefined
      pooled.worker.off('message', onMessage)
      pooled.worker.off('error', onError)
      pooled.worker.off('exit', onExit)
    }

    const settle = (result: SandboxResult, discardWorker: boolean) => {
      if (settled) return
      settled = true
      cleanup()
      if (discardWorker) discard(pooled)
      else release(pooled)
      resolve(result)
    }

    const failed = (errorMessage: string, discardWorker: boolean) => {
      settle(
        {
          status: 'error',
          passed: 0,
          total,
          timeMs: Date.now() - startedAt,
          stdoutTail: '',
          errorMessage,
        },
        discardWorker,
      )
    }

    function onMessage(value: WorkerMessage) {
      if (!value || typeof value !== 'object') return
      if (value.type === 'ready') {
        pooled.loaded = true
        if (loadTimer) clearTimeout(loadTimer)
        loadTimer = undefined
        runTimer = setTimeout(abortWithTimeout, timeLimitMs)
        return
      }
      if (value.type === 'boot-error') {
        failed(`沙箱运行时加载失败：${value.errorMessage ?? '未知原因'}`, true)
        return
      }
      if (value.type === 'result') {
        settle(normalizeResult(value, total, Date.now() - startedAt), false)
      }
    }

    function onError(error: unknown) {
      failed(`沙箱 Worker 异常：${toErrorMessage(error)}`, true)
    }

    function onExit(code: number) {
      failed(code === 0 ? '沙箱 Worker 提前退出' : `沙箱 Worker 异常退出（code=${code}）`, false)
    }

    function abortWithTimeout() {
      settle(
        {
          status: 'timeout',
          passed: 0,
          total,
          timeMs: Date.now() - startedAt,
          stdoutTail: '',
          errorMessage: `超过 ${timeLimitMs}ms 未执行完，Worker 已被强制终止`,
        },
        true,
      )
    }

    pooled.worker.on('message', onMessage)
    pooled.worker.on('error', onError)
    pooled.worker.on('exit', onExit)

    if (pooled.loaded) {
      runTimer = setTimeout(abortWithTimeout, timeLimitMs)
    } else {
      loadTimer = setTimeout(
        () => failed('沙箱运行时加载超时', true),
        WORKER_LOAD_TIMEOUT_MS,
      )
    }

    pooled.worker.postMessage({ type: 'run', code: input.code, tests })
  })
}

export async function runInPyodide(input: RunInPyodideInput): Promise<SandboxResult> {
  const pooled = await acquire()
  return runOn(pooled, input)
}

/** 预热一个沙箱 Worker（加载 Pyodide WASM），让首场硬战场不用等运行时加载。 */
export async function warmupPyodide(): Promise<void> {
  let pooled: PooledWorker | null = null
  try {
    pooled = await acquire()
    if (pooled.loaded) {
      release(pooled)
      return
    }
    const target = pooled
    await new Promise<void>((resolve, reject) => {
      let settled = false
      let bootError: string | undefined
      function finish(ok: boolean) {
        if (settled) return
        settled = true
        clearTimeout(timer)
        target.worker.off('message', onMessage)
        target.worker.off('error', onError)
        if (ok) resolve()
        else reject(new Error(bootError ? `沙箱预热失败：${bootError}` : '沙箱预热失败'))
      }
      function onMessage(value: WorkerMessage) {
        if (!value || typeof value !== 'object') return
        if (value.type === 'ready') finish(true)
        if (value.type === 'boot-error') {
          bootError = value.errorMessage ?? '未知原因'
          finish(false)
        }
      }
      function onError(error: unknown) {
        bootError = toErrorMessage(error)
        finish(false)
      }
      const timer = setTimeout(() => finish(false), WORKER_LOAD_TIMEOUT_MS)
      target.worker.on('message', onMessage)
      target.worker.on('error', onError)
      target.worker.postMessage({ type: 'prewarm' })
    })
    target.loaded = true
    release(target)
  } catch (error) {
    if (pooled) discard(pooled)
    logger.warn('沙箱预热失败，首次硬战场将走冷启动', {
      error: error instanceof Error ? error.message : String(error),
    })
  }
}

export const pyodideBackend: SandboxBackend = {
  id: 'pyodide',
  isolationLevel: 'wasm-worker',
  run: (input) => runInPyodide({ code: input.code, tests: input.tests, timeLimitMs: input.timeLimitMs }),
}
