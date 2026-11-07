// 导播台：选台、切镜、看事件。人工切镜写进事件流，回放能重现。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, configure, el, propagateQuery, shotLabel, toast, gameMeta } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'
import { activateOnKey } from './a11y.js'
import { applyI18n, resolveLang, setLang, t } from './i18n.js'

setLang(resolveLang())
configure()
propagateQuery()
mountTopbar({ active: 'director' })
document.title = t('director.title')
applyI18n()

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
    // 列表拉通只说明 API 可达；没有打开任何比赛时，"已连接"会让人
    // 以为正在导播某一场。连接状态跟"是否在导播"绑定，而不是跟"能否拉列表"。
    setConn(currentMatch ? t('director.connected') : t('director.noMatch'), currentMatch ? 'ok' : '')
    if (!data.matches.length) {
      // 从 URL 直接打开的比赛可能尚未进入列表；已在导播时不能显示
      // “没有正在进行的比赛”，否则左栏与中间画面自相矛盾。
      if (currentMatch) {
        litRender(
          html`<button class="mi on" type="button" data-id="${currentMatch}">
            <div class="id">${currentMatch}</div>
            <div class="p">${t('director.opened')}</div>
          </button>`,
          el('matchList'),
        )
        el('matchList').querySelector('.mi').onclick = () => openMatch(currentMatch)
      } else {
        litRender(
          html`<div class="muted">${t('director.empty')}</div>`,
          el('matchList'),
        )
      }
      return
    }
    litRender(
      html`${data.matches.map(
        (m) => html`<button class="mi" type="button" data-id="${m.matchId}">
          <div class="id">${m.matchId}</div>
          <div class="p">${(m.players || []).join(' vs ') || '—'}</div>
          <div class="m"><span>${gameMeta(m.game).label}</span><span>${t('director.remaining', { n: m.remaining ?? '—' })}</span></div>
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
    setConn(t('director.connectFailed'), 'bad')
    litRender(html`<div class="muted">${t('director.fetchFailed', { error: String(error.message || error) })}</div>`, el('matchList'))
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
    item.innerHTML = `<div class="id"></div><div class="p">${t('director.opened')}</div>`
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
              <span class="k">${shotLabel(cue.payload.shot)}</span>
              <span class="manual">${cue.payload.trigger}</span>
              <div class="muted">seq ${cue.seq} · ${cue.payload.durationMs}ms${cue.payload.target ? ' · ' + cue.payload.target : ''}</div>
            </div>`,
          )}`
        : html`<div class="muted">${t('director.noCues')}</div>`,
      el('cueList'),
    )
    litRender(
      data.lastEvents.length
        ? html`${data.lastEvents.map(
            (event) => html`<div><span class="t">${String(event.seq).padStart(3, '0')}</span> ${event.type}${
              event.player ? html` <b>${event.player}</b>` : ''
            }</div>`,
          )}`
        : html`<div class="muted">${t('director.noEvents')}</div>`,
      el('log'),
    )
    setConn(t('director.connectedAuto'), 'ok')
  } catch (error) {
    setStatus(t('director.refreshFailed', { error: error.message || error }), 'bad')
  }
}

async function pushCue() {
  if (!currentMatch) {
    setStatus(t('director.pickFirst'), 'bad')
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
    setStatus(t('director.cutDone', { shot: shotLabel(body.shot) }))
    el('note').value = ''
    await refreshDirector()
  } catch (error) {
    setStatus(t('director.cutFailed', { error: error.message || error }), 'bad')
  }
}

function startPolling() {
  clearInterval(timer)
  timer = setInterval(() => {
    if (!document.hidden) refreshDirector()
  }, 1500)
}

const shotNodes = [...document.querySelectorAll('.shot')]
for (const node of shotNodes) node.onclick = () => highlightShot(node.dataset.shot)
activateOnKey(shotNodes, (node) => highlightShot(node.dataset.shot))

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
