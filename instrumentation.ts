/**
 * Next.js 启动钩子：预热一个沙箱 Worker（加载 Pyodide WASM）。
 *
 * 预热是尽力而为的：失败或未启用 pyodide 后端都不影响启动，
 * 首场硬战场会退回冷启动路径。
 */
export function register(): void {
  if (process.env.NEXT_RUNTIME !== 'nodejs') return
  if ((process.env.SANDBOX_BACKEND ?? 'pyodide').trim() === 'judge0') return

  void import('./lib/sandbox/pyodide-worker.ts')
    .then(({ warmupPyodide }) => warmupPyodide())
    .catch(() => {})
}
