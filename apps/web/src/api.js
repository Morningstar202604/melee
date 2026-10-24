// REST 客户端：所有页面的取数都走这里，避免每个页面各写一份 fetch 与错误处理。
// token 只存在内存与 URL 参数里，不写 localStorage（分享出去的链接不该带凭据）。

let authToken = ''
let baseUrl = ''

export function configure({ token = '', api = '' } = {}) {
  if (token) authToken = token
  if (api) baseUrl = api

  const params = new URLSearchParams(location.search)
  if (!authToken && params.get('token')) authToken = params.get('token')
  if (!baseUrl && params.get('api')) baseUrl = params.get('api')
}

export function token() {
  return authToken
}

export function apiBase() {
  return baseUrl
}

export function authHeaders(extra = {}) {
  const headers = { ...extra }
  if (authToken) headers.Authorization = `Bearer ${authToken}`
  return headers
}

export async function request(path, { method = 'GET', body, headers = {}, timeoutMs = 30000 } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: authHeaders(body !== undefined ? { 'Content-Type': 'application/json', ...headers } : headers),
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    if (!response.ok) {
      const text = await response.text().catch(() => '')
      let detail = text
      try {
        const parsed = JSON.parse(text)
        detail = parsed.detail || text
      } catch {
        /* 非 JSON 错误体：原样用文本 */
      }
      throw new Error(`${response.status} ${detail || response.statusText}`)
    }
    const type = response.headers.get('content-type') || ''
    return type.includes('application/json') ? response.json() : response.text()
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请检查服务是否在线')
    throw error
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  games: () => request('/games'),
  stats: () => request('/stats'),
  archive: ({ limit = 24, offset = 0, game = '', status = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (game) params.set('game', game)
    if (status) params.set('status', status)
    return request(`/archive?${params}`)
  },
  createMatch: (body) => request('/matches', { method: 'POST', body }),
  events: (matchId, after = -1) => request(`/matches/${encodeURIComponent(matchId)}/events?after=${after}`),
  state: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/state`),
  recap: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/recap`),
  presence: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/presence`),
  clips: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/clips`),
  renderClips: (matchId, { limit = 3, perEventMs = 900 } = {}) => {
    const params = new URLSearchParams({ limit: String(limit), perEventMs: String(perEventMs) })
    return request(`/matches/${encodeURIComponent(matchId)}/clips/render?${params}`, { method: 'POST', timeoutMs: 180000 })
  },
  mediaTicket: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/media-ticket`, { method: 'POST' }),
  seasons: () => request('/seasons'),
  season: (seasonId) => request(`/seasons/${encodeURIComponent(seasonId)}`),
  createSeason: (body) => request('/seasons', { method: 'POST', body }),
  runSeason: (seasonId, maxFixtures) => {
    const query = maxFixtures ? `?maxFixtures=${maxFixtures}` : ''
    return request(`/seasons/${encodeURIComponent(seasonId)}/run${query}`, { method: 'POST', timeoutMs: 300000 })
  },
  directorMatches: () => request('/director/matches'),
  directorState: (matchId) => request(`/director/${encodeURIComponent(matchId)}`),
  pushCue: (matchId, body) => request(`/director/${encodeURIComponent(matchId)}/cue`, { method: 'POST', body }),
  ticket: () => request('/auth/ticket', { method: 'POST' }),
  quota: () => request('/quota'),
}

export function clipUrl(matchId, file, ticket = '') {
  const query = ticket ? `?ticket=${encodeURIComponent(ticket)}` : ''
  return `${baseUrl}/matches/${encodeURIComponent(matchId)}/clips/${encodeURIComponent(file)}${query}`
}

export function cardUrl(matchId) {
  return `${baseUrl}/matches/${encodeURIComponent(matchId)}/card.png`
}

export function shareUrl(matchId) {
  const origin = baseUrl ? new URL(baseUrl, location.href).origin : location.origin
  return `${origin}/share/${encodeURIComponent(matchId)}`
}

// ---- UI 小工具 ----

export function el(id) {
  return document.getElementById(id)
}

export function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[char]))
}

let toastTimer = null
export function toast(message, kind = '') {
  let node = document.getElementById('toast')
  if (!node) {
    node = document.createElement('div')
    node.id = 'toast'
    node.className = 'toast'
    node.setAttribute('role', 'status')
    document.body.appendChild(node)
  }
  node.textContent = message
  node.className = `toast on ${kind}`
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { node.className = 'toast' }, 2600)
}

export function formatTime(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function relativeTime(iso) {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (Number.isNaN(diff)) return ''
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

export function formatDuration(firstIso, lastIso) {
  if (!firstIso || !lastIso) return ''
  const ms = new Date(lastIso).getTime() - new Date(firstIso).getTime()
  if (!Number.isFinite(ms) || ms <= 0) return ''
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, '0')}s`
}

// 赛制视觉标识：颜色与图标只在这里定义，页面不各写一套。
export const GAME_META = {
  'twenty-questions': { label: '二十问', glyph: '?', tone: '#22D3EE', bg: 'rgba(34,211,238,.10)' },
  codenames: { label: '代号', glyph: '◈', tone: '#F5A524', bg: 'rgba(245,165,36,.10)' },
  'tic-tac-toe': { label: '井字棋', glyph: '#', tone: '#34D399', bg: 'rgba(52,211,153,.10)' },
  'connect-four': { label: '四子棋', glyph: '▤', tone: '#60A5FA', bg: 'rgba(96,165,250,.10)' },
  othello: { label: '黑白棋', glyph: '◐', tone: '#A78BFA', bg: 'rgba(167,139,250,.10)' },
  nim: { label: '取石子', glyph: '◆', tone: '#F87171', bg: 'rgba(248,113,113,.10)' },
}

export function gameMeta(gameId) {
  return GAME_META[gameId] || { label: gameId, glyph: '·', tone: '#22D3EE', bg: 'rgba(34,211,238,.10)' }
}

export function winnerLabel(winner) {
  if (!winner) return ''
  return { red: '红队', blue: '蓝队' }[winner] || winner
}

// 把 api / token / match 参数带到同站其它页面，免得手动再填。
export function propagateQuery() {
  const params = new URLSearchParams(location.search)
  const keep = new URLSearchParams()
  for (const key of ['api', 'token', 'match', 'season']) {
    if (params.get(key)) keep.set(key, params.get(key))
  }
  if (!keep.toString()) return
  document.querySelectorAll('a[data-keep-query]').forEach((anchor) => {
    const url = new URL(anchor.getAttribute('href'), location.href)
    keep.forEach((value, key) => url.searchParams.set(key, value))
    anchor.setAttribute('href', url.pathname.split('/').pop() + url.search)
  })
}
