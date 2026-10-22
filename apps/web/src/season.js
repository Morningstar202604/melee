import { setToken } from './sse.js'

const params = new URLSearchParams(location.search)
const API = params.get('api') || ''
const token = params.get('token') || ''
if (token) setToken(token)
const seasonId = params.get('season') || ''

const el = (id) => document.getElementById(id)
const headers = () => (token ? { Authorization: `Bearer ${token}` } : {})

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

async function load() {
  if (!seasonId) {
    el('subtitle').textContent = '缺少 season 参数。用法：season.html?season=<seasonId>&api=<服务地址>'
    return
  }
  try {
    const response = await fetch(`${API}/seasons/${encodeURIComponent(seasonId)}`, { headers: headers() })
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
    const data = await response.json()

    el('title').textContent = data.name || seasonId
    el('subtitle').textContent = `${data.standings.length} 位选手 · ${data.fixtures.length} 场赛程`

    el('standings').innerHTML = data.standings
      .map(
        (row, index) => `<div class="rank">
          <span class="no">${index + 1}</span>
          <span class="nm">${escapeHtml(row.display || row.playerId)}</span>
          <span class="w">${row.wins}</span>
          <span class="l">${row.losses}</span>
          <span class="r">${row.rating.toFixed(1)}</span>
        </div>`,
      )
      .join('')

    el('rivalries').innerHTML = data.rivalries.length
      ? data.rivalries
          .map(
            (r) => `<div class="fixture"><span>${escapeHtml(r.a)} vs ${escapeHtml(r.b)}</span>
              <span style="margin-left:auto">${r.games} 场 · ${r.aWins}-${r.bWins} · 领先 ${escapeHtml(r.leador)}</span></div>`,
          )
          .join('')
      : '<div class="muted">暂无（需交手 2 场以上）</div>'

    el('fixtures').innerHTML = data.fixtures
      .map(
        (f) => `<div class="fixture"><span class="rd">R${f.round}</span>
          <span>${escapeHtml(f.a)} vs ${escapeHtml(f.b)}</span>
          <span style="margin-left:auto">${f.scheduledAt ? formatWhen(f.scheduledAt) : ''}</span></div>`,
      )
      .join('')
    renderNextUp(data.fixtures)
  } catch (error) {
    el('error').textContent = String(error.message || error)
  }
}

function formatWhen(unixSeconds) {
  const date = new Date(unixSeconds * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function countdownText(unixSeconds) {
  const diff = Math.round(unixSeconds - Date.now() / 1000)
  if (diff <= 0) return '已到开赛时间'
  const hours = Math.floor(diff / 3600)
  const minutes = Math.ceil((diff % 3600) / 60)
  return hours > 0 ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`
}

let nextUpTimer = null
let nextUpAt = null

function renderNextUp(fixtures) {
  const upcoming = fixtures.filter((f) => f.scheduledAt && !f.played).sort((a, b) => a.scheduledAt - b.scheduledAt)
  const next = upcoming.find((f) => f.scheduledAt > Date.now() / 1000 - 60) || upcoming[0]
  clearInterval(nextUpTimer)
  if (!next) {
    el('nextUp').style.display = 'none'
    return
  }
  nextUpAt = next.scheduledAt
  el('nextUp').style.display = ''
  const paint = () => {
    el('nextUpText').textContent = `${formatWhen(nextUpAt)} · ${next.a} vs ${next.b} · ${
      countdownText(nextUpAt) === '已到开赛时间' ? '已到开赛时间' : '还有 ' + countdownText(nextUpAt)
    }`
  }
  paint()
  nextUpTimer = setInterval(paint, 1000)
}

load()
