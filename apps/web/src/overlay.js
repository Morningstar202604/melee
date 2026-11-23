// OBS / 直播叠加层：只读事件流，画一张给镜头看的记分牌。
// 与观战页共用 view.js 的 reducer；不复制一套规则，避免直播和叠加层各说各话。
import { api, configure, gameMeta } from './api.js'
import { connectEvents } from './sse.js'
import { createView, reduce, replayView } from './view.js'
import { getLang, resolveLang, setLang, t } from './i18n.js'

const params = new URLSearchParams(location.search)
if (params.get('preview') === '1') document.body.classList.add('preview')
setLang(resolveLang())
configure()

const root = document.getElementById('overlay')
const matchId = params.get('match') || localStorage.getItem('melee.currentMatch') || ''
let view = createView()
let events = []
let closeStream = null
let reconnectTimer = null

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[ch]))

const PIECE_VALUES = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 }

function material(board) {
  if (!Array.isArray(board)) return null
  let white = 0
  let black = 0
  for (const row of board) {
    if (!Array.isArray(row)) continue
    for (const cell of row) {
      const piece = String(cell || '')
      if (!piece) continue
      const value = PIECE_VALUES[piece.toLowerCase()] ?? 0
      if (piece === piece.toUpperCase()) white += value
      else black += value
    }
  }
  return { white, black, diff: white - black }
}

function boardMarkup(board, lastMove) {
  if (!Array.isArray(board)) {
    return `<div class="ob-empty">${esc(t('overlay.noBoard'))}</div>`
  }
  const rows = board.length
  const cols = Array.isArray(board[0]) ? board[0].length : 0
  const marked = new Set()
  if (lastMove && rows === 8 && cols === 8) {
    for (const key of ['from', 'to']) {
      const value = String(lastMove[key] || '')
      if (/^[a-h][1-8]$/.test(value)) marked.add(`${8 - Number(value[1])}:${value.charCodeAt(0) - 97}`)
    }
  }
  return `<div class="ob-board" style="--cols:${Math.max(1, cols)}">${board.map((row, r) => `
    <div class="ob-row">${row.map((cell, c) => `
      <span class="ob-cell ${((r + c) % 2 === 0) ? 'light' : 'dark'} ${marked.has(`${r}:${c}`) ? 'last' : ''}">${esc(cell || '')}</span>`).join('')}
    </div>`).join('')}</div>`
}

function playerLine(name, index, active, winner) {
  const label = winner && winner === name ? '<b class="ob-win">WIN</b>' : ''
  return `<div class="ob-player ${active ? 'active' : ''}">
    <span class="ob-seat">${index + 1}</span><span class="ob-name">${esc(name)}</span>${label}
  </div>`
}

function render() {
  if (!matchId) {
    root.innerHTML = `<div class="ob-panel ob-bottom"><span class="ob-kicker">melee</span>
      <span class="ob-muted">${esc(t('overlay.waiting'))} · match=…</span></div>`
    return
  }
  const status = view.status === 'ended' ? t('overlay.ended') : view.status === 'live' ? t('overlay.live') : t('overlay.waiting')
  const active = view.currentPlayer || view.activePlayer
  const board = view.gameState && view.gameState.board
  const lastMove = view.gameState && view.gameState.lastMove
  const mat = material(board)
  const last = view.lastAction
  const narration = view.narration.length ? view.narration[view.narration.length - 1].text : ''
  const players = view.players || []
  const source = view.imported ? (view.sourceLabel || view.source || 'external') : ''
  const game = gameMeta(view.gameKind).label
  const eventCount = view.seq >= 0 ? view.seq + 1 : 0
  const moveText = lastMove ? `${lastMove.from || '—'} → ${lastMove.to || '—'}` : (last ? `${last.player} ${last.value || ''}` : '—')
  const matText = mat && (mat.diff !== 0)
    ? `${mat.diff > 0 ? 'WHITE' : 'BLACK'} +${Math.abs(mat.diff)}`
    : t('overlay.material')

  root.innerHTML = `
    <div class="ob-top ob-panel">
      <div class="ob-headline"><span class="ob-dot ${view.status}"></span><b>${esc(status)}</b><span class="ob-game">${esc(game)}</span></div>
      <div class="ob-players">${players.map((name, index) => playerLine(name, index, active === name, view.winner)).join('')}</div>
    </div>
    ${board ? `<div class="ob-boardWrap ob-panel">${boardMarkup(board, lastMove)}
      <div class="ob-boardFoot"><span>${esc(t('overlay.board'))}</span><span>${esc(matText)}</span></div></div>` : ''}
    <div class="ob-bottom ob-panel">
      <div class="ob-kicker">${esc(source || 'melee')}${view.episodeId ? ` · #${esc(view.episodeId)}` : ''}</div>
      <div class="ob-last"><span>${esc(t('overlay.lastMove'))}</span><b>${esc(moveText)}</b><span class="ob-events">${esc(t('overlay.events', { n: eventCount }))}</span></div>
      <div class="ob-narr">${esc(narration || '—')}</div>
    </div>`
}

async function load() {
  try {
    events = (await api.events(matchId, -1, 320)).events || []
    view = replayView(events)
    render()
    closeStream = await connectEvents(matchId, {
      after: events.length ? events[events.length - 1].seq : null,
      onEvent: (event) => {
        events.push(event)
        view = reduce(view, event)
        render()
      },
      onError: () => {
        root.dataset.error = '1'
      },
    })
  } catch (error) {
    root.innerHTML = `<div class="ob-panel ob-bottom"><span class="ob-kicker">melee</span>
      <span class="ob-muted">${esc(t('overlay.disconnected'))}</span></div>`
    reconnectTimer = setTimeout(load, 5000)
  }
}

window.addEventListener('beforeunload', () => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  closeStream?.()
})

if (!matchId) render()
else load()
