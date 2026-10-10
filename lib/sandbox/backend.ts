export interface TestCase {
  input: string
  expected: string
}

export type SandboxStatus = 'ok' | 'timeout' | 'error'

export interface SandboxResult {
  status: SandboxStatus
  passed: number
  total: number
  timeMs: number
  stdoutTail: string
  errorMessage?: string
}

export interface SandboxRunInput {
  code: string
  tests: TestCase[]
  timeLimitMs: number
}

export interface SandboxBackend {
  id: 'pyodide' | 'judge0'
  isolationLevel: 'wasm-worker' | 'os-container'
  run(input: SandboxRunInput): Promise<SandboxResult>
}

export const SANDBOX_BACKENDS = ['pyodide', 'judge0'] as const
export type SandboxBackendId = (typeof SANDBOX_BACKENDS)[number]

function currentBackendId(): SandboxBackendId {
  const configured = (process.env.SANDBOX_BACKEND ?? 'pyodide').trim()
  if ((SANDBOX_BACKENDS as readonly string[]).includes(configured)) {
    return configured as SandboxBackendId
  }
  throw new Error(`未知的 SANDBOX_BACKEND：${configured}（可选值：pyodide / judge0）`)
}

export async function getBackend(): Promise<SandboxBackend> {
  const id = currentBackendId()
  if (id === 'pyodide') {
    const { pyodideBackend } = await import('./pyodide-worker.ts')
    return pyodideBackend
  }
  const { judge0Backend } = await import('./judge0.ts')
  return judge0Backend
}
