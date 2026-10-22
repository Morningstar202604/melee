import { html, render as litRender } from '../vendor/lit-html.js'
import { clipUrl, fetchMediaTicket, setToken } from './sse.js'

const params = new URLSearchParams(location.search)
const API = params.get('api') || ''
const token = params.get('token') || ''
if (token) setToken(token)
const matchId = params.get('match') || ''

const el = (id) => document.getElementById(id)
const headers = () => (token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' })

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
    litRender(
      html`${stats.map(([label, value]) => html`<div class="stat"><b>${value}</b><span>${label}</span></div>`)}`,
      el('stats'),
    )

    litRender(
      data.highlights.length
        ? html`${data.highlights.map(
            (h) => html`<div class="hlc ${h.weight >= 80 ? 'hot' : ''}">
              <div class="k">${h.kind} · seq ${h.seq}</div>
              <div class="t">${h.title}</div>
              <div class="d">${h.detail}</div>
            </div>`,
          )}`
        : html`<div class="muted">这场比赛没有明显转折点。</div>`,
      el('highlights'),
    )
  } catch (error) {
    el('error').textContent = String(error.message || error)
  }
}

async function renderClips() {
  if (!matchId) return
  litRender(html`<div class="muted">渲染中…</div>`, el('clips'))
  try {
    const response = await fetch(`${API}/matches/${encodeURIComponent(matchId)}/clips/render?limit=3`, {
      method: 'POST',
      headers: headers(),
    })
    if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
    const data = await response.json()
    const ticket = await fetchMediaTicket(matchId, { baseUrl: API })
    litRender(
      data.clips.length
        ? html`${data.clips.map(
            (clip) => html`<div class="clip">
              <video controls preload="none" src="${clipUrl(matchId, clip.file, API, ticket)}"></video>
              <div class="m">${clip.title} · ${(clip.durationMs / 1000).toFixed(1)}s</div>
            </div>`,
          )}`
        : html`<div class="muted">没有可剪的高光。</div>`,
      el('clips'),
    )
  } catch (error) {
    litRender(html`<div class="error">${String(error.message || error)}</div>`, el('clips'))
  }
}

el('render').onclick = renderClips
load()
