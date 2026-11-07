// 观战页渲染层：只把视图状态画成 DOM，不持有播放/连接状态。
// 拆出来的原因：main.js 原先把"怎么画"和"怎么播"混在 673 行里，
// 改一个棋盘样式要穿过 SSE 重连与键盘处理。
import { html } from '../../vendor/lit-html.js'
import { paint as litRender } from '../paint.js'
import { el, gameMeta, teamLabel } from '../api.js'
import { t } from '../i18n.js'
import { latestThoughtOf, currentCue } from '../view.js'
import { elapsedAt, formatClock, marksOf, ratioAt } from './timeline.js'

// 多队配色：预置 8 色板，超出自动编号；未翻开的格子仍是深色。
const DYNAMIC_TEAM_COLORS = [
  'var(--team-1)', 'var(--team-2)', 'var(--team-3)', 'var(--team-4)',
  'var(--team-5)', 'var(--team-6)', 'var(--team-7)', 'var(--team-8)',
]

function teamColor(team) {
  const m = /^team(\d+)$/.exec(String(team || ''))
  if (m) return DYNAMIC_TEAM_COLORS[(Number(m[1]) - 1) % DYNAMIC_TEAM_COLORS.length]
  return null
}

const CELL_COLORS = {
  red: 'var(--cell-red)', blue: 'var(--cell-blue)', neutral: 'var(--cell-neutral)', assassin: 'var(--cell-assassin)',
}

function playerCard(player, index, shown) {
  const history = (shown.history || []).filter((item) => item.by === player)
  const thought = latestThoughtOf(shown, player)
  const active = shown.activePlayer === player && shown.status !== 'ended'
  const isQuiz = shown.gameKind === 'twenty-questions'
  const tone = index % 2 === 0 ? 'var(--tone-a)' : 'var(--tone-b)'

  const rows = history.slice(-8).map((item) => {
    if (item.kind === 'ask') {
      return html`<div class="qrow"><span class="idx"></span>
        <span class="q">${item.value}</span>
        <span class="ans ${item.answer === 'yes' ? 'y' : 'n'}">${item.answer === 'yes' ? t('watch.answer.yes') : t('watch.answer.no')}</span></div>`
    }
    if (item.kind === 'hint') {
      return html`<div class="qrow"><span class="idx" style="color:var(--b)">${t('watch.answer.hint')}</span>
        <span class="q">${item.value}</span></div>`
    }
    if (item.kind === 'pick') {
      const cls = { red: 'y', blue: 'n', neutral: 'n', assassin: 'n' }[item.color] || 'n'
      return html`<div class="qrow"><span class="idx"></span>
        <span class="q">${item.value}</span>
        <span class="ans ${cls}">${teamLabel(item.color)}</span></div>`
    }
    return html`<div class="qrow"><span class="idx" style="color:var(--ghost)">${t('watch.answer.step')}</span>
      <span class="q">${item.value}</span></div>`
  })

  return html`<div class="card ${active ? 'active' : ''}" style="--tone:${tone}">
    <div class="phead">
      <div class="avatar">${player.slice(0, 1)}</div>
      <div><div class="pname">${player}</div><div class="pmodel">${t('watch.player')}</div></div>
      <div class="status ${active ? 'think' : ''}">${active ? t('watch.playerThinking') : t('watch.playerWaiting')}</div>
    </div>
    <div class="big"><div class="n">${history.length}</div><div class="l">${isQuiz ? t('watch.playerAsked') : t('watch.playerMoves')}</div></div>
    <div class="label">${t('watch.playerThought')}</div>
    <div class="thought">${thought || t('watch.playerNoThought')}</div>
    <div class="label">${t('watch.playerLedger')}</div>
    <div class="ledger">${rows.length ? rows : html`<div class="empty">${t('watch.playerNoLedger')}</div>`}</div>
  </div>`
}

export function renderSides(shown) {
  if (!shown.players || !shown.players.length) {
    litRender(
      html`<div class="card" style="--tone:var(--tone-a);align-items:center;justify-content:center;text-align:center">
        <div class="pname" style="font-size:15px;color:var(--dim)">${t('watch.idle.title')}</div>
        <div class="muted" style="margin-top:8px;line-height:1.7" .innerHTML=${t('watch.idle.body')}></div>
        <a class="btn" href="index.html" style="margin-top:14px">${t('watch.idle.cta')}</a>
      </div>`,
      el('sideLeft'),
    )
    litRender(html``, el('sideRight'))
    return
  }
  const players = shown.players
  const half = Math.ceil(players.length / 2)
  litRender(html`${players.slice(0, half).map((p, i) => playerCard(p, i, shown))}`, el('sideLeft'))
  litRender(html`${players.slice(half).map((p, i) => playerCard(p, i + half, shown))}`, el('sideRight'))
}

export function renderArena(shown) {
  const isQuiz = shown.gameKind === 'twenty-questions'
  el('gameLabel').textContent = 'TRANSMISSION · ' + gameMeta(shown.gameKind).label

  litRender(
    isQuiz
      ? html`<span .innerHTML=${t('watch.turn', { n: `<b id="qnum">${shown.turnIndex}</b>`, max: `<b id="maxq">${shown.maxQuestions}</b>` })}></span>`
      : html`<span .innerHTML=${t('watch.step', { n: `<b id="qnum">${shown.turnIndex ?? 0}</b>` })}></span>`,
    el('turnPill'),
  )
  litRender(
    isQuiz
      ? html`<span .innerHTML=${t('watch.candidates', { n: `<b id="remain">${shown.remaining ?? '—'}</b>` })}></span>`
      : html``,
    el('remainPill'),
  )
  el('remainPill').style.display = isQuiz ? '' : 'none'
  const title = el('panelTitle')
  const meta = el('panelMeta')

  if (isQuiz) {
    title.textContent = t('watch.history.title')
    litRender(
      html`<span .innerHTML=${t('watch.candidatesLeft', { n: `<b id="remainHead">${shown.remaining ?? '—'}</b>` })}></span>`,
      meta,
    )
    return renderHistory(shown)
  }
  title.textContent = t('watch.board.position')
  meta.textContent = gameMeta(shown.gameKind).label
  if (shown.gameKind === 'codenames') return renderCodenames(shown)
  if (shown.gameKind === 'nim') return renderPiles(shown)
  return renderBoard(shown)
}

function renderPiles(shown) {
  const piles = shown.gameState && shown.gameState.piles
  if (!Array.isArray(piles)) return litRender(html`<div class="empty">${t('watch.board.waiting')}</div>`, el('historyList'))
  const max = Math.max(1, ...piles.map((n) => Number(n) || 0))
  litRender(
    html`<div class="piles">${piles.map(
      (count, index) => html`<div class="pile"><span class="pno">${t('watch.nim.pile', { n: index })}</span>
        <span class="pbar"><i style="width:${(Number(count) / max) * 100}%"></i></span>
        <span class="pnum">${count}</span></div>`,
    )}</div>`,
    el('historyList'),
  )
}

function renderCodenames(shown) {
  const board = shown.gameState && shown.gameState.board
  if (!Array.isArray(board)) return litRender(html`<div class="empty">${t('watch.board.waiting')}</div>`, el('historyList'))
  const COLOR = CELL_COLORS
  const teams = shown.teamRemaining || {}
  const turn = teamLabel(shown.turn || '')
  const phase = shown.phase === 'hint' ? t('watch.codenames.hintPhase') : t('watch.codenames.guessPhase')
  const hint = shown.hint
  litRender(
    html`<div class="cnHead">
        <span class="cnTurn ${shown.turn === 'blue' ? 'blue' : 'red'}">${turn} · ${phase}</span>
        <span class="cnHint">${hint ? t('watch.codenames.clue', { word: hint.word, count: hint.count }) : t('watch.codenames.noHint')}</span>
        <span class="cnLeft">${t('watch.codenames.remaining', { red: teams.red ?? '—', blue: teams.blue ?? '—' })}</span>
      </div>
      <div class="cnGrid">${board.map((cell) => {
        const bg = cell.revealed && cell.color ? COLOR[cell.color] || teamColor(cell.color) || 'var(--cell-hidden)' : 'var(--cell-hidden)'
        const style = cell.revealed ? `background:${bg};border-color:${bg};color:var(--cell-ink)` : ''
        return html`<div class="cnCell" style="${style}">${cell.word}</div>`
      })}</div>`,
    el('historyList'),
  )
}

function renderBoard(shown) {
  const board = shown.gameState && shown.gameState.board
  if (!Array.isArray(board)) return litRender(html`<div class="empty">${t('watch.board.waiting')}</div>`, el('historyList'))
  litRender(
    html`<div class="board">${board.map(
      (row) => html`<div class="brow">${row.map((cell) => html`<span class="cell">${cell || '·'}</span>`)}</div>`,
    )}</div>`,
    el('historyList'),
  )
}

function renderHistory(shown) {
  const recent = shown.history.slice(-6)
  litRender(
    recent.length
      ? html`${recent.map(
          (item) => html`<div class="hrow"><b class="a">${item.by}</b>
            <span>${item.value}</span><i class="${item.answer === 'yes' ? 'y' : 'n'}">${item.answer === 'yes' ? t('watch.answer.yes') : t('watch.answer.no')}</i></div>`,
        )}`
      : html`<div class="empty">${t('watch.history.waiting')}</div>`,
    el('historyList'),
  )
}

export function renderTimeline(events, mode, cursor, timeline = null) {
  const fill = el('tlFill')
  const total = Math.max(events.length, 1)
  // 没有比赛时显示 0 / 0，而不是用"至少 1"骗出一个可拖的时间轴。
  const position = events.length === 0 ? 0 : mode === 'live' ? total : cursor
  el('scrub').max = String(total)
  el('scrub').value = String(position)
  el('tlCount').textContent = `${position} / ${events.length}`
  el('replayBadge').style.display = mode === 'replay' ? 'inline-flex' : 'none'
  el('scrub').disabled = events.length === 0

  if (!timeline || !timeline.points.length) {
    fill.style.width = `${(position / total) * 100}%`
    return
  }
  // 进度条按真实时间画：一局二十问里提问与思考片段数量差一个数量级，
  // 按序号画会让拖动条前 90% 原地打转、最后 10% 跳完整局。
  const ratio = mode === 'live' ? 1 : ratioAt(timeline, position)
  fill.style.width = `${ratio * 100}%`

  const clock = el('tlClock')
  if (clock) {
    const elapsed = mode === 'live' ? timeline.durationMs : elapsedAt(timeline, ratio)
    clock.textContent = `${formatClock(elapsed)} / ${formatClock(timeline.durationMs)}`
  }
  renderMarks(timeline, events)
}

let markKey = ''
function renderMarks(timeline, events) {
  const box = el('tlMarks')
  if (!box) return
  const marks = marksOf(timeline, events)  // 事件流没变就不重建 DOM：SSE 每来一条都会 render，白重建会让焦点丢失。
  const key = `${events.length}:${marks.length}:${marks.length ? marks[marks.length - 1].seq : -1}`
  if (key === markKey) return
  markKey = key
  box.replaceChildren(
    ...marks.map((mark) => {
      const dot = document.createElement('button')
      dot.type = 'button'
      dot.className = 'mark'
      dot.style.left = `${mark.t * 100}%`
      dot.title = mark.type === 'match.ended' ? t('watch.mark.end') : t('watch.mark.cue')
      dot.setAttribute('aria-label', dot.title)
      // index 是 1-based 游标语义，和 player.seek 一致。
      dot.addEventListener('click', () => seekTo(mark.index))
      return dot
    }),
  )
}

// 由 main.js 注入：render.js 不持有播放状态，跳转必须回到 player。
let seekHandler = null
export function onSeek(handler) {
  seekHandler = handler
}
export function resetTimelineCache() {
  // 切台时必须清掉：上一场的 key 会和新场撞上，橙点就停在旧位置。
  markKey = ''
}
function seekTo(cursor) {
  if (typeof seekHandler === 'function') seekHandler(cursor)
}

export function renderStatus(shown) {
  el('viewStatus').textContent =
    shown.status === 'ended'
      ? t('watch.status.ended')
      : shown.status === 'live'
        ? t('watch.status.live')
        : t('watch.status.waiting')
  el('rejected').textContent = shown.rejected
  el('cues').textContent = shown.cues.length
  const last = shown.lastAction
  el('lastAction').textContent = last
    ? `${last.player} ${last.kind === 'ask' ? t('watch.action.ask') : last.kind === 'guess' ? t('watch.action.guess') : last.kind} ${last.value || ''}`
    : '—'
}

export { currentCue }
