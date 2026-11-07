// 全局错误兜底：页面级未捕获异常至少要在控制台和界面上留下痕迹。
//
// 为什么需要：之前任何 async 函数里漏 catch 的 rejection 都会静默消失——
// 用户看到"按钮点了没反应"，控制台也没有线索，排查只能靠猜。
// 这里不试图"修复"错误（不该猜业务意图），只做三件事：
//   1. 打一条结构化日志（页面、消息、栈），让问题可追；
//   2. 给用户一个可见但不打扰的提示（同一错误 10s 内只提示一次，避免刷屏）；
//   3. 允许页面注册自己的降级处理（例如列表页重新拉数）。
//
// 注意不要吞掉错误：浏览器默认行为（控制台打印）保留。

const DEDUPE_MS = 10_000
const seen = new Map()
const handlers = []

/** 注册页面级降级处理。返回解绑函数。 */
export function onFatal(handler) {
  handlers.push(handler)
  return () => {
    const index = handlers.indexOf(handler)
    if (index >= 0) handlers.splice(index, 1)
  }
}

function describe(error) {
  if (!error) return 'unknown error'
  if (typeof error === 'string') return error
  return String(error.message || error.reason?.message || error)
}

function report(kind, error) {
  const message = describe(error)
  const now = Date.now()
  const last = seen.get(message) || 0
  const shouldNotify = now - last > DEDUPE_MS
  seen.set(message, now)

  // 结构化日志：带上页面与类型，便于从用户截图里定位。
  // eslint-disable-next-line no-console
  console.error(`[melee:${kind}]`, { page: location.pathname, message, error })

  if (!shouldNotify) return
  for (const handler of handlers) {
    try {
      handler(message, error)
    } catch (nested) {
      // 降级处理自己再出错时不能再抛，否则会形成错误风暴
      // eslint-disable-next-line no-console
      console.error('[melee:handler]', nested)
    }
  }
}

let installed = false

/** 安装全局兜底。每个页面启动时调一次；重复调用无副作用。 */
export function installFatalHandlers() {
  if (installed) return
  installed = true

  window.addEventListener('error', (event) => {
    // 资源加载失败（img/script）也走这里，但没有 error 对象，消息足够定位
    report('error', event.error || event.message || `${event.target?.tagName || 'resource'} failed`)
  })

  window.addEventListener('unhandledrejection', (event) => {
    report('rejection', event.reason)
    // 不在控制台重复打印：report 里已经打过带上下文的版本
    event.preventDefault?.()
  })
}
