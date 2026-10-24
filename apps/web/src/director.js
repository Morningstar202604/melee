// 导播台：选台、切镜、看事件。人工切镜写进事件流，回放能重现。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, configure, el, propagateQuery, toast, gameMeta } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'

configure()
propagateQuery()
mountTopbar({ active: 'director' })

const params = new URLSearchParams(location.search)
const SHOTS = ['wide', 'close', 'thought', 'reveal', 'slowmo']
let currentMatch = ''
let selectedShot = 'wide'
let timer = null

function setStatus(message, kind = '') {
  const node = el('status')
  node.textContent = message
  node.className = kind === 'bad' ? 'notice warn' : 'muted'
}

function setConn(text, kind = '') {
  const node = el('conn')
  node.textContent = text
  node.className = kind === 'ok' ? '' : kind === 'bad' ? 'muted' : 'muted'
  node.style.color = kind === 'ok' ? 'var(--ok)' : kind === 'bad' ? 'var(--bad)' : ''
}

async function loadMatches() {
  try {
    const data = await api.directorMatches()
    setConn('已连接', 'ok')
    if (!data.matches.length) {
      litRender(
        html`<div class="muted">没有正在进行的比赛。开一局新的，或用下方输入 matchId 打开。</div>`,
        el('matchList'),
      )
      return
    }
    litRender(
      html`${data.matches.map(
        (m) => html`<button class="mi" type="button" data-id="${m.matchId}">
          <div class="id">${m.matchId}</div>
          <div class="p">${(m.players || []).join(' vs ') || '—'}</div>
          <div class="m"><span>${gameMeta(m.game).label}</span><span>剩 ${m.remaining ?? '—'}</span></div>
        </button>`,
      )}`,
      el('matchList'),
    )
    for (const node of el('matchList').querySelectorAll('.mi')) {
      node.onclick = () => openMatch(node.dataset.id)
    }
    if (data.matches.length === 1 && !currentMatch) {
      openMatch(data.matches[0].matchId)
    }
  } catch (error) {
    setConn('连接失败', 'bad')
    litRender(html`<div class="muted">拉取失败：${String(error.message || error)}</div>`, el('matchList'))
  }
}

function highlightShot(shot) {
  selectedShot = shot
  document.querySelectorAll('.shot').forEach((node) => {
    const on = node.dataset.shot === shot
    node.classList.toggle('on', on)
    node.setAttribute('aria-pressed', String(on))
  })
}

async function openMatch(matchId) {
  currentMatch = matchId
  el('matchId').value = matchId
  el('btnWatch').href = `watch.html?match=${encodeURIComponent(matchId)}`
  // 从 URL 直接打开的比赛可能还没出现在"正在直播"列表里（worker 刚领取），
  // 补一条占位项，避免左侧显示"没有比赛"但中间已经在导播。
  if (!document.querySelector(`.mi[data-id="${CSS.escape(matchId)}"]`)) {
    const list = el('matchList')
    if (list.querySelector('.muted')) list.textContent = ''
    const item = document.createElement('button')
    item.className = 'mi'
    item.type = 'button'
    item.dataset.id = matchId
    item.innerHTML = `<div class="id"></div><div class="p">已打开的场次</div>`
    item.querySelector('.id').textContent = matchId
    item.onclick = () => openMatch(matchId)
    list.prepend(item)
  }
  document.querySelectorAll('.mi').forEach((node) => {
    node.classList.toggle('on', node.dataset.id === matchId)
  })
  await refreshDirector()
  startPolling()
}

async function refreshDirector() {
  if (!currentMatch) return
  try {
    const data = await api.directorState(currentMatch)
    el('autoCount').textContent = String(data.cues.auto ?? 0)
    el('manualCount').textContent = String(data.cues.manual ?? 0)
    el('eventCount').textContent = String(data.eventCount ?? data.lastEvents.length)
    litRender(
      data.recent.length
        ? html`${data.recent.map(
            (cue) => html`<div class="cue-row">
              <span class="k">${cue.payload.shot}</span>
              <span class="manual">${cue.payload.trigger}</span>
              <div class="muted">seq ${cue.seq} · ${cue.payload.durationMs}ms${cue.payload.target ? ' · ' + cue.payload.target : ''}</div>
            </div>`,
          )}`
        : html`<div class="muted">还没有人工切镜。</div>`,
      el('cueList'),
    )
    litRender(
      data.lastEvents.length
        ? html`${data.lastEvents.map(
            (event) => html`<div><span class="t">${String(event.seq).padStart(3, '0')}</span> ${event.type}${
              event.player ? html` <b>${event.player}</b>` : ''
            }</div>`,
          )}`
        : html`<div class="muted">还没有事件。</div>`,
      el('log'),
    )
    setConn('已连接 · 自动刷新', 'ok')
  } catch (error) {
    setStatus(`刷新失败：${error.message || error}`, 'bad')
  }
}

async function pushCue() {
  if (!currentMatch) {
    setStatus('先选一场比赛。', 'bad')
    return
  }
  const body = {
    shot: selectedShot,
    durationMs: Number(el('duration').value || 1200),
    target: el('target').value.trim() || null,
    note: el('note').value.trim(),
  }
  try {
    await api.pushCue(currentMatch, body)
    setStatus(`已切：${body.shot}`)
    el('note').value = ''
    await refreshDirector()
  } catch (error) {
    setStatus(`切镜失败：${error.message || error}`, 'bad')
  }
}

function startPolling() {
  clearInterval(timer)
  timer = setInterval(() => {
    if (!document.hidden) refreshDirector()
  }, 1500)
}

document.querySelectorAll('.shot').forEach((node) => {
  const activate = () => highlightShot(node.dataset.shot)
  node.onclick = activate
  node.onkeydown = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      activate()
    }
  }
})

document.addEventListener('keydown', (event) => {
  const tag = (event.target && event.target.tagName) || ''
  if (tag === 'INPUT' || tag === 'TEXTAREA') {
    if (event.key === 'Enter' && event.target.id === 'note') pushCue()
    return
  }
  const index = Number(event.key)
  if (index >= 1 && index <= SHOTS.length) highlightShot(SHOTS[index - 1])
  else if (event.key === 'Enter') pushCue()
  else if (event.key === 'r' || event.key === 'R') refreshDirector()
  else if (event.key === 'Escape') el('note').value = ''
})

el('push').onclick = pushCue
el('open').onclick = () => {
  const value = el('matchId').value.trim()
  if (value) openMatch(value)
}
el('refresh').onclick = loadMatches

highlightShot('wide')
loadMatches()
setInterval(() => {
  if (!document.hidden) loadMatches()
}, 10000)
if (params.get('match')) openMatch(params.get('match'))
mountFooter()
