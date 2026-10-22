import { setToken } from './sse.js'

const params = new URLSearchParams(location.search)
const API = params.get('api') || ''
const token = params.get('token') || ''
if (token) setToken(token)

const el = (id) => document.getElementById(id)
const headers = () => (token ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' } : { 'Content-Type': 'application/json' })

let currentMatch = ''
let selectedShot = 'wide'
let timer = null

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, { ...options, headers: { ...headers(), ...(options.headers || {}) } })
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`)
  return response.json()
}

async function loadMatches() {
  try {
    const data = await api('/director/matches')
    const list = el('matchList')
    el('conn').textContent = '已连接'
    el('conn').className = 'conn ok'
    if (!data.matches.length) {
      list.innerHTML = '<div class="muted">没有正在进行的比赛。开一局新的，或用下方输入 matchId 打开。</div>'
      return
    }
    list.innerHTML = data.matches
      .map(
        (m) => `<div class="mi" data-id="${escapeHtml(m.matchId)}">
          <div class="id">${escapeHtml(m.matchId)}</div>
          <div class="p">${escapeHtml(m.players.join(' vs '))} · 剩 ${m.remaining ?? '—'}</div>
        </div>`,
      )
      .join('')
    list.querySelectorAll('.mi').forEach((node) => {
      node.onclick = () => openMatch(node.dataset.id)
    })
    // 只有一个直播时自动打开：导播台第一眼就该看到画面与事件，
    // 而不是空白面板加一条需要手动点的列表项。
    if (data.matches.length === 1 && !currentMatch) {
      openMatch(data.matches[0].matchId)
    }
  } catch (error) {
    el('conn').textContent = '连接失败'
    el('conn').className = 'conn bad'
    el('matchList').innerHTML = `<div class="muted">拉取失败：${escapeHtml(String(error.message || error))}</div>`
  }
}

function highlightShot(shot) {
  selectedShot = shot
  document.querySelectorAll('.shot').forEach((node) => {
    node.classList.toggle('on', node.dataset.shot === shot)
  })
}

async function openMatch(matchId) {
  currentMatch = matchId
  el('matchId').value = matchId
  document.querySelectorAll('.mi').forEach((node) => {
    node.classList.toggle('on', node.dataset.id === matchId)
  })
  await refreshDirector()
  startPolling()
}

async function refreshDirector() {
  if (!currentMatch) return
  try {
    const data = await api(`/director/${encodeURIComponent(currentMatch)}`)
    el('autoCount').textContent = String(data.cues.auto)
    el('manualCount').textContent = String(data.cues.manual)
    el('cueList').innerHTML = data.recent.length
      ? data.recent
          .map(
            (cue) => `<div class="cue-row">
              <span class="k">${escapeHtml(cue.payload.shot)}</span>
              <span class="manual">${escapeHtml(cue.payload.trigger)}</span>
              <div class="muted">seq ${cue.seq} · ${cue.payload.durationMs}ms</div>
            </div>`,
          )
          .join('')
      : '<div class="muted">还没有人工切镜。</div>'
    el('log').innerHTML = data.lastEvents
      .map((event) => {
        const player = event.player ? ` <b>${escapeHtml(event.player)}</b>` : ''
        return `<div>${String(event.seq).padStart(3, '0')} ${escapeHtml(event.type)}${player}</div>`
      })
      .join('')
  } catch (error) {
    el('status').textContent = `刷新失败：${error.message || error}`
  }
}

async function pushCue() {
  if (!currentMatch) {
    el('status').textContent = '先选一场比赛。'
    return
  }
  const body = {
    shot: selectedShot,
    durationMs: Number(el('duration').value || 1200),
    target: el('target').value.trim() || null,
    note: el('note').value.trim(),
  }
  try {
    await api(`/director/${encodeURIComponent(currentMatch)}/cue`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    el('status').textContent = `已切：${body.shot}`
    el('note').value = ''
    await refreshDirector()
  } catch (error) {
    el('status').textContent = `切镜失败：${error.message || error}`
  }
}

function startPolling() {
  clearInterval(timer)
  timer = setInterval(refreshDirector, 1500)
}

document.querySelectorAll('.shot').forEach((node) => {
  node.onclick = () => highlightShot(node.dataset.shot)
})
el('push').onclick = pushCue
el('open').onclick = () => {
  const value = el('matchId').value.trim()
  if (value) openMatch(value)
}
el('refresh').onclick = loadMatches

highlightShot('wide')
loadMatches()
if (params.get('match')) openMatch(params.get('match'))
