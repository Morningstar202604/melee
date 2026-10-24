// SSE 客户端：只负责把事件交出去，不掺任何状态逻辑。
// Last-Event-ID 由浏览器自动带上，断线重连不会漏帧也不会重复。
import { authHeaders, token } from './api.js'

export async function connectEvents(matchId, { onEvent, onOpen, onError, baseUrl = '' } = {}) {
  // EventSource 不能带请求头：token 模式下先换一张 60 秒票据，
  // 票据只出现在 URL 里、一次即废、很快过期，不会像 token 那样长期泄漏。
  let ticket = ''
  if (token()) {
    try {
      const response = await fetch(`${baseUrl}/auth/ticket`, { method: 'POST', headers: authHeaders() })
      if (response.ok) ticket = (await response.json()).ticket || ''
    } catch {
      ticket = ''
    }
  }
  const query = ticket ? `?ticket=${encodeURIComponent(ticket)}` : ''
  const url = `${baseUrl}/matches/${encodeURIComponent(matchId)}/stream${query}`
  const source = new EventSource(url)

  source.onopen = () => onOpen?.()

  source.onmessage = (message) => {
    if (!message.data) return
    try {
      onEvent?.(JSON.parse(message.data), Number(message.lastEventId))
    } catch (error) {
      onError?.(error)
    }
  }

  source.onerror = (event) => onError?.(event)

  return () => source.close()
}

export async function fetchMatch(matchId, { baseUrl = '' } = {}) {
  const response = await fetch(`${baseUrl}/matches/${encodeURIComponent(matchId)}/events`, { headers: authHeaders() })
  if (!response.ok) throw new Error(`拉取事件失败：${response.status}`)
  return response.json()
}

export async function createMatch(body, { baseUrl = '' } = {}) {
  const response = await fetch(`${baseUrl}/matches`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(`建场失败：${response.status} ${await response.text()}`)
  return response.json()
}

export async function renderClips(matchId, { baseUrl = '', limit = 3, perEventMs = 900 } = {}) {
  const params = new URLSearchParams({ limit: String(limit), perEventMs: String(perEventMs) })
  const response = await fetch(
    `${baseUrl}/matches/${encodeURIComponent(matchId)}/clips/render?${params}`,
    { method: 'POST', headers: authHeaders() },
  )
  if (!response.ok) throw new Error(`渲染失败：${response.status} ${await response.text()}`)
  return response.json()
}

export async function fetchMediaTicket(matchId, { baseUrl = '' } = {}) {
  if (!token()) return ''
  try {
    const response = await fetch(`${baseUrl}/matches/${encodeURIComponent(matchId)}/media-ticket`, {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!response.ok) return ''
    return (await response.json()).ticket || ''
  } catch {
    return ''
  }
}

export function clipUrl(matchId, file, baseUrl = '', ticket = '') {
  const query = ticket ? `?ticket=${encodeURIComponent(ticket)}` : ''
  return `${baseUrl}/matches/${encodeURIComponent(matchId)}/clips/${encodeURIComponent(file)}${query}`
}
