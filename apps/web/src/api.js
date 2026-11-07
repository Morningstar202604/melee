// REST 客户端：所有页面的取数都走这里，避免每个页面各写一份 fetch 与错误处理。
// token 只存在内存与 URL 参数里，不写 localStorage（分享出去的链接不该带凭据）。
import { gameLabel, getLang, t } from './i18n.js'

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
  // 带上界面语言：建场时后端据此选择内容词表（如代号棋盘词），
  // 否则英文界面里会混进中文棋盘——本地化看起来像只做了一半。
  headers['Accept-Language'] = getLang()
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
    if (error.name === 'AbortError') throw new Error(t('common.timeout'))
    throw error
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  games: () => request('/games'),
  stats: () => request('/stats'),
  archive: ({ limit = 24, offset = 0, game = '', status = '', q = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (game) params.set('game', game)
    if (status) params.set('status', status)
    if (q) params.set('q', q)
    return request(`/archive?${params}`)
  },
  createMatch: (body) => request('/matches', { method: 'POST', body }),
  events: (matchId, after = -1) => request(`/matches/${encodeURIComponent(matchId)}/events?after=${after}`),
  state: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/state`),
  recap: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/recap`),
  presence: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/presence`),
  clips: (matchId) => request(`/matches/${encodeURIComponent(matchId)}/clips`),
  renderClips: (matchId, { limit = 3, perEventMs = 900, template = 'broadcast' } = {}) => {
    const params = new URLSearchParams({
      limit: String(limit), perEventMs: String(perEventMs), template,
    })
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

/** 分享落地页在 API 站；分端口部署时 location.origin 是静态站，会 404。 */
export function shareUrl(matchId) {
  const origin = baseUrl ? new URL(baseUrl, location.href).origin : location.origin
  return `${origin}/share/${encodeURIComponent(matchId)}`
}

// ---- UI 小工具 ----

export function el(id) {
  return document.getElementById(id)
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
  if (diff < 60) return t('time.justNow')
  if (diff < 3600) return t('time.minutesAgo', { n: Math.floor(diff / 60) })
  if (diff < 86400) return t('time.hoursAgo', { n: Math.floor(diff / 3600) })
  return t('time.daysAgo', { n: Math.floor(diff / 86400) })
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
// label 不写死在这里——赛制名要跟随语言切换，统一走 i18n 的 game.* 词条。
/* 赛制只声明"用第几号色"，具体色值在 theme.css 的 --game-N / --game-N-bg 里，
   这样换主题时赛制配色跟着走，不用改一行 JS。 */
const GAME_TONES = {
  'twenty-questions': 1,
  codenames: 2,
  'tic-tac-toe': 3,
  'connect-four': 4,
  othello: 5,
  nim: 6,
  'reverse-tic-tac-toe': 7,
  'kuhn-poker': 8,
  'game-of-pure-strategy': 9,
  'simple-tak': 10,
  'wild-tic-tac-toe': 11,
  crusade: 12,
}

const GAME_GLYPHS = {
  'twenty-questions': '?',
  codenames: '◈',
  'tic-tac-toe': '#',
  'connect-four': '▤',
  othello: '◐',
  nim: '◆',
  'reverse-tic-tac-toe': '⊘',
  'kuhn-poker': '♠',
  'game-of-pure-strategy': '♛',
  'simple-tak': '▦',
  'wild-tic-tac-toe': '⊗',
  crusade: '♞',
}

export function gameMeta(gameId, fallbackLabel = '') {
  const index = GAME_TONES[gameId] || 1
  return {
    glyph: GAME_GLYPHS[gameId] || '·',
    tone: `var(--game-${index})`,
    bg: `var(--game-${index}-bg)`,
    label: gameLabel(gameId, fallbackLabel),
  }
}

// 镜头代号是事件契约里的英文枚举，界面必须翻成当前语言：
// 观众看到「wide · 最后一手，慢镜收尾」会以为页面坏了。
export function shotLabel(shot) {
  if (!shot) return ''
  const key = `shot.${shot}`
  const text = t(key)
  return text === key ? shot : text
}

export function teamLabel(team) {
  if (!team) return ''
  const key = `team.${team}`
  const text = t(key)
  if (text !== key) return text
  // 多队编队的动态队伍：team3 → Team 3（与后端 codenames.team_label 同规则）
  const m = /^team(\d+)$/.exec(String(team))
  return m ? t('team.indexed', { n: m[1] }) : String(team)
}

export function winnerLabel(winner) {
  return teamLabel(winner)
}

// 把 api / token / match / lang 参数带到同站其它页面，免得手动再填。
export function propagateQuery() {
  const params = new URLSearchParams(location.search)
  const keep = new URLSearchParams()
  for (const key of ['api', 'token', 'match', 'season', 'lang']) {
    if (params.get(key)) keep.set(key, params.get(key))
  }
  if (!keep.toString()) return
  document.querySelectorAll('a[data-keep-query]').forEach((anchor) => {
    const url = new URL(anchor.getAttribute('href'), location.href)
    keep.forEach((value, key) => url.searchParams.set(key, value))
    anchor.setAttribute('href', url.pathname.split('/').pop() + url.search)
  })
}
