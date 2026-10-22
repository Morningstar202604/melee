import { clipUrl, fetchMediaTicket, setToken } from './sse.js'

const params = new URLSearchParams(location.search)
const API = params.get('api') || ''
const token = params.get('token') || ''
if (token) setToken(token)
const matchId = params.get('match') || ''

const el = (id) => document.getElementById(id)
const headers = () => (token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' })

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

const TEAM_LABELS = { red: '红队', blue: '蓝队' }
const prettyWinner = (value) => (value ? TEAM_LABELS[value] || value : '')

async function load() {
  if (!matchId) {
    el('error').textContent = '缺少 match 参数。用法：recap.html?match=<matchId>&api=<服务地址>'
    return
  }
  try {
    const response = await fetch(`${API}/matches/${encodeURIComponent(matchId)}/recap`, { headers: headers() })
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
    const data = await response.json()

    el('kick').textContent = `${matchId} · 已结束`
    el('winner').textContent = data.winner ? `${prettyWinner(data.winner)} 胜` : '无人获胜'
    el('oneLine').textContent = data.oneLine

    const isQuiz = data.game === 'twenty-questions' || !data.game
    const stats = isQuiz
      ? [
          ['回合', data.turns],
          ['提问', data.asks],
          ['报答案', data.guesses],
          ['犯规', data.rejected],
        ]
      : [
          ['步数', data.moves],
          ['犯规', data.rejected],
        ]
    el('stats').innerHTML = stats
      .map(([label, value]) => `<div class="stat"><b>${value}</b><span>${label}</span></div>`)
      .join('')

    el('highlights').innerHTML = data.highlights.length
      ? data.highlights
          .map(
            (h) => `<div class="hlc ${h.weight >= 80 ? 'hot' : ''}">
              <div class="k">${escapeHtml(h.kind)} · seq ${h.seq}</div>
              <div class="t">${escapeHtml(h.title)}</div>
              <div class="d">${escapeHtml(h.detail)}</div>
            </div>`,
          )
          .join('')
      : '<div class="muted">这场比赛没有明显转折点。</div>'
  } catch (error) {
    el('error').textContent = String(error.message || error)
  }
}

async function renderClips() {
  if (!matchId) return
  el('clips').innerHTML = '<div class="muted">渲染中…</div>'
  try {
    const response = await fetch(`${API}/matches/${encodeURIComponent(matchId)}/clips/render?limit=3`, {
      method: 'POST',
      headers: headers(),
    })
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
    const data = await response.json()
    const ticket = await fetchMediaTicket(matchId, { baseUrl: API })
    el('clips').innerHTML = data.clips.length
      ? data.clips
          .map(
            (clip) => `<div class="clip">
              <video controls preload="none" src="${clipUrl(matchId, clip.file, API, ticket)}"></video>
              <div class="m">${escapeHtml(clip.title)} · ${(clip.durationMs / 1000).toFixed(1)}s</div>
            </div>`,
          )
          .join('')
      : '<div class="muted">没有可剪的高光。</div>'
  } catch (error) {
    el('clips').innerHTML = `<div class="error">${escapeHtml(String(error.message || error))}</div>`
  }
}

el('render').onclick = renderClips
load()
