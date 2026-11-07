// SSE 客户端：只负责建立事件流与断线重连，不掺任何状态逻辑。
// HTTP 取数一律走 api.js：两套 fetch 会各写一份超时、鉴权与错误格式。
import { api, authHeaders, token } from './api.js'

async function fetchTicket() {
  try {
    return (await api.ticket()).ticket || ''
  } catch {
    return ''
  }
}

export async function connectEvents(matchId, { onEvent, onOpen, onError, baseUrl = '' } = {}) {
  // EventSource 不能带请求头：token 模式下先换一张 60 秒票据，
  // 票据只出现在 URL 里、一次即废、很快过期，不会像 token 那样长期泄漏。
  //
  // 关键：浏览器自动重连会复用同一个 URL，60 秒后票据过期 → 服务端 401 →
  // 浏览器继续指数退避重试同一个废票据，永远连不上。所以这里自己管重连：
  // 每次重连前重新换票，并手动带上 Last-Event-ID 续传。
  let closed = false
  let source = null
  let attempt = 0
  let cursor = null

  const open = async () => {
    if (closed) return
    if (source) {
      source.close()
      source = null
    }
    const ticket = token() ? await fetchTicket() : ''
    if (closed) return
    const params = new URLSearchParams()
    if (ticket) params.set('ticket', ticket)
    if (cursor !== null) params.set('lastEventId', String(cursor))
    // 带上 lastEventId 就必须关掉浏览器自带重连（它的 URL 里是旧票据）。
    const query = params.toString() ? `?${params}` : ''
    const url = `${baseUrl}/matches/${encodeURIComponent(matchId)}/stream${query}`
    source = new EventSource(url)

    source.onopen = () => {
      attempt = 0
      onOpen?.()
    }

    source.onmessage = (message) => {
      if (message.lastEventId) cursor = Number(message.lastEventId)
      if (!message.data) return
      try {
        onEvent?.(JSON.parse(message.data), Number(message.lastEventId))
      } catch (error) {
        onError?.(error)
      }
    }

    source.onerror = (event) => {
      onError?.(event)
      if (closed) return
      source?.close()
      source = null
      // 1s 起、上限 15s 的退避；每次都换新票据，断线久了也能接上。
      attempt += 1
      const delay = Math.min(15000, 1000 * 2 ** Math.min(attempt, 4))
      setTimeout(open, delay)
    }
  }

  await open()

  return () => {
    closed = true
    source?.close()
    source = null
  }
}

export { authHeaders }
