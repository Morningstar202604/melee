// 观战页：事件流 → 视图状态 → DOM。直播与回放共用同一个 reduce(events)。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import {
  api, configure, el, gameMeta, propagateQuery, toast, apiBase,
} from './api.js'
import { connectEvents, fetchMatch, createMatch, renderClips, clipUrl, fetchMediaTicket } from './sse.js'
import { createView, reduce, replayView, latestThoughtOf, currentCue } from './view.js'

const params = new URLSearchParams(location.search)
configure()
propagateQuery()
const API = apiBase()
const MATCH_KEY = 'melee.currentMatch'

const TEAM_LABELS = { red: '红队', blue: '蓝队', neutral: '中立', assassin: '刺客' }
const prettyTeam = (value) => TEAM_LABELS[value] || value

let view = createView()
let events = []
let cursor = 0
let mode = 'live'
let speed = 1
let playing = true
let closeStream = null
let frame = null
let endTimer = null
let dismissedEnd = false
let currentMatchId = null
let presenceTimer = null

function fitStage() {
  const frameEl = el('frame')
  const stage = el('stage')
  if (window.matchMedia('(max-width: 900px)').matches) {
    // 内联的 1600×900 会盖掉 CSS 的 width:100%，从桌面缩到手机时必须清掉，
    // 否则舞台保持桌面尺寸、控制器被 overflow:hidden 裁掉。
    stage.style.transform = ''
    stage.style.marginLeft = ''
    stage.style.marginTop = ''
    stage.style.width = ''
    stage.style.height = ''
    return
  }
  const availableW = frameEl.clientWidth
  const availableH = frameEl.clientHeight
  const scale = Math.min(availableW / 1600, availableH / 900) * 0.985
  stage.style.transform = `scale(${scale})`
  stage.style.transformOrigin = 'top left'
  stage.style.width = '1600px'
  stage.style.height = '900px'
  stage.style.marginLeft = `${Math.max(0, (availableW - 1600 * scale) / 2)}px`
  // 垂直也居中：只做水平居中会在宽屏下留一条大黑边。
  stage.style.marginTop = `${Math.max(0, (availableH - 900 * scale) / 2)}px`
}

function render() {
  const shown = mode === 'live' ? view : replayView(events, cursor)
  const lastLine = shown.narration.length ? shown.narration[shown.narration.length - 1].text : ''
  el('narration').textContent = lastLine || '等待第一条解说。'
  el('headline').textContent = lastLine || '等待第一条解说。'
  const total = Math.max(shown.maxQuestions, 1)
  const remain = shown.remaining ?? total
  el('bar').style.width = `${Math.max(0, Math.min(100, (remain / total) * 100))}%`

  const cue = currentCue(shown)
  el('cue').textContent = cue ? `${cue.shot} · ${cue.trigger}` : '未切镜'

  const idle = !shown.players || !shown.players.length
  el('stage').classList.toggle('idle', idle)
  if (!currentMatchId) {
    el('liveLabel').textContent = '待命'
    el('liveLabel').className = 'ended'
  }
  renderSides(shown)
  if (!idle) renderArena(shown)
  renderTimeline()
  renderStatus(shown)

  const hasMatch = Boolean(currentMatchId)
  el('liveLabel').textContent = !hasMatch
    ? '待命'
    : shown.status === 'ended'
      ? '已结束'
      : mode === 'replay'
        ? '回放'
        : 'LIVE'
  el('liveLabel').className = !hasMatch ? 'ended' : shown.status === 'ended' ? 'ended' : mode === 'replay' ? 'replay' : ''
  el('liveDot').style.display = hasMatch && shown.status !== 'ended' ? '' : 'none'

  if (shown.status === 'ended' && !dismissedEnd && mode === 'live') {
    if (!endTimer) {
      endTimer = setTimeout(() => {
        el('winner').textContent = prettyTeam(shown.winner) || '无人获胜'
        el('endreason').textContent = String(shown.endReason || '')
        el('endcard').classList.add('on')
        el('endcard').focus()
      }, 1200)
    }
  } else if (shown.status !== 'ended' || dismissedEnd || mode !== 'live') {
    clearTimeout(endTimer)
    endTimer = null
    el('endcard').classList.remove('on')
  }
}

function playerCard(player, index, shown) {
  const history = (shown.history || []).filter((item) => item.by === player)
  const thought = latestThoughtOf(shown, player)
  const active = shown.activePlayer === player && shown.status !== 'ended'
  const isQuiz = shown.gameKind === 'twenty-questions'
  const tone = index % 2 === 0 ? '#22D3EE' : '#F5A524'

  const rows = history.slice(-8).map((item) => {
    if (item.kind === 'ask') {
      return html`<div class="qrow"><span class="idx"></span>
        <span class="q">${item.value}</span>
        <span class="ans ${item.answer === 'yes' ? 'y' : 'n'}">${item.answer === 'yes' ? '是' : '否'}</span></div>`
    }
    if (item.kind === 'hint') {
      return html`<div class="qrow"><span class="idx" style="color:#F5A524">线索</span>
        <span class="q">${item.value}</span></div>`
    }
    if (item.kind === 'pick') {
      const cls = { red: 'y', blue: 'n', neutral: 'n', assassin: 'n' }[item.color] || 'n'
      return html`<div class="qrow"><span class="idx"></span>
        <span class="q">${item.value}</span>
        <span class="ans ${cls}">${TEAM_LABELS[item.color] || ''}</span></div>`
    }
    return html`<div class="qrow"><span class="idx" style="color:#3E4658">步</span>
      <span class="q">${item.value}</span></div>`
  })

  return html`<div class="card ${active ? 'active' : ''}" style="--tone:${tone}">
    <div class="phead">
      <div class="avatar">${player.slice(0, 1)}</div>
      <div><div class="pname">${player}</div><div class="pmodel">选手</div></div>
      <div class="status ${active ? 'think' : ''}">${active ? '思考中' : '等待'}</div>
    </div>
    <div class="big"><div class="n">${history.length}</div><div class="l">${isQuiz ? '已问' : '出手'}</div></div>
    <div class="label">内心戏</div>
    <div class="thought">${thought || '（还没有内心戏）'}</div>
    <div class="label">记录</div>
    <div class="ledger">${rows.length ? rows : html`<div class="empty">暂无</div>`}</div>
  </div>`
}

function renderSides(shown) {
  if (!shown.players || !shown.players.length) {
    litRender(
      html`<div class="card" style="--tone:#22D3EE;align-items:center;justify-content:center;text-align:center">
        <div class="pname" style="font-size:15px;color:#8A93A5">还没有选择比赛</div>
        <div class="muted" style="margin-top:8px;line-height:1.7">从首页的赛事库挑一场，<br>或点顶部「新开一局」。</div>
        <a class="btn" href="index.html" style="margin-top:14px">去赛事库</a>
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

function renderArena(shown) {
  const isQuiz = shown.gameKind === 'twenty-questions'
  el('gameLabel').textContent = 'TRANSMISSION · ' + (gameMeta(shown.gameKind).label || shown.gameKind)

  litRender(
    isQuiz
      ? html`第 <b id="qnum">${shown.turnIndex}</b> 手 / 上限 <b id="maxq">${shown.maxQuestions}</b> 手`
      : html`第 <b id="qnum">${shown.turnIndex ?? 0}</b> 步`,
    el('turnPill'),
  )
  litRender(isQuiz ? html`候选 <b id="remain">${shown.remaining ?? '—'}</b>` : html``, el('remainPill'))
  el('remainPill').style.display = isQuiz ? '' : 'none'
  const title = el('panelTitle')
  const meta = el('panelMeta')

  if (isQuiz) {
    title.textContent = '公开提问记录'
    litRender(html`剩 <b id="remainHead">${shown.remaining ?? '—'}</b> 个候选`, meta)
    return renderHistory(shown)
  }
  title.textContent = '当前局面'
  meta.textContent = gameMeta(shown.gameKind).label
  if (shown.gameKind === 'codenames') return renderCodenames(shown)
  if (shown.gameKind === 'nim') return renderPiles(shown)
  renderBoard(shown)
}

function renderPiles(shown) {
  const piles = shown.gameState && shown.gameState.piles
  if (!Array.isArray(piles)) return litRender(html`<div class="empty">等待局面状态…</div>`, el('historyList'))
  const max = Math.max(1, ...piles.map((n) => Number(n) || 0))
  litRender(
    html`<div class="piles">${piles.map(
      (count, index) => html`<div class="pile"><span class="pno">堆 ${index}</span>
        <span class="pbar"><i style="width:${(Number(count) / max) * 100}%"></i></span>
        <span class="pnum">${count}</span></div>`,
    )}</div>`,
    el('historyList'),
  )
}

function renderCodenames(shown) {
  const board = shown.gameState && shown.gameState.board
  if (!Array.isArray(board)) return litRender(html`<div class="empty">等待棋盘状态…</div>`, el('historyList'))
  const COLOR = { red: '#F87171', blue: '#60A5FA', neutral: '#6B7280', assassin: '#111827' }
  const teams = shown.teamRemaining || {}
  const turn = shown.turn === 'blue' ? '蓝队' : shown.turn === 'red' ? '红队' : ''
  const phase = shown.phase === 'hint' ? '队长给线索' : '队员猜词'
  const hint = shown.hint
  litRender(
    html`<div class="cnHead">
        <span class="cnTurn ${shown.turn === 'blue' ? 'blue' : 'red'}">${turn} · ${phase}</span>
        <span class="cnHint">${hint ? `线索「${hint.word}」× ${hint.count}` : '还没有线索'}</span>
        <span class="cnLeft">红 ${teams.red ?? '—'} · 蓝 ${teams.blue ?? '—'}</span>
      </div>
      <div class="cnGrid">${board.map((cell) => {
        const bg = cell.revealed && cell.color ? COLOR[cell.color] || '#1C2230' : '#0E121A'
        const style = cell.revealed ? `background:${bg};border-color:${bg};color:#fff` : ''
        return html`<div class="cnCell" style="${style}">${cell.word}</div>`
      })}</div>`,
    el('historyList'),
  )
}

function renderBoard(shown) {
  const board = shown.gameState && shown.gameState.board
  if (!Array.isArray(board)) return litRender(html`<div class="empty">等待棋盘状态…</div>`, el('historyList'))
  litRender(
    html`<div class="board">${board.map(
      (row) => html`<div class="brow">${row.map((cell) => html`<span class="cell">${cell || '·'}</span>`)}</div>`,
    )}</div>`,
    el('historyList'),
  )
}

function renderHistory(shown) {
  const recent = shown.history.slice(-6)
  const label = (by) => (shown.players.includes(by) ? by : by)
  litRender(
    recent.length
      ? html`${recent.map(
          (item) => html`<div class="hrow"><b class="a">${label(item.by)}</b>
            <span>${item.value}</span><i class="${item.answer === 'yes' ? 'y' : 'n'}">${item.answer === 'yes' ? '是' : '否'}</i></div>`,
        )}`
      : html`<div class="empty">等待第一次提问</div>`,
    el('historyList'),
  )
}

function renderTimeline() {
  const fill = el('tlFill')
  const total = Math.max(events.length, 1)
  // 没有比赛时显示 0 / 0，而不是用"至少 1"骗出一个可拖的时间轴。
  const position = events.length === 0 ? 0 : mode === 'live' ? total : cursor
  fill.style.width = `${(position / total) * 100}%`
  el('scrub').max = String(total)
  el('scrub').value = String(position)
  el('tlCount').textContent = `${position} / ${events.length}`
  el('replayBadge').style.display = mode === 'replay' ? 'inline-flex' : 'none'
  el('scrub').disabled = events.length === 0
}

function renderStatus(shown) {
  el('viewStatus').textContent =
    shown.status === 'ended' ? '已结束' : shown.status === 'live' ? '进行中' : '等待开赛'
  el('rejected').textContent = shown.rejected
  el('cues').textContent = shown.cues.length
  const last = shown.lastAction
  el('lastAction').textContent = last
    ? `${last.player} ${last.kind === 'ask' ? '提问' : last.kind === 'guess' ? '报答案' : last.kind} ${last.value || ''}`
    : '—'
}

function appendEvent(event) {
  // 快照已经拉过一遍全量，SSE 又从 seq 0 推一遍；不按 seq 去重就会翻倍。
  if (events.length && event.seq <= events[events.length - 1].seq) return

  events = events.concat(event)
  if (mode === 'live') {
    view = reduce(view, event)
    cursor = events.length
    render()
  }
}

let attachToken = 0

async function attach(matchId) {
  // 切台可能并发：后发起的请求必须先完成才算数，否则慢的旧请求会覆盖新画面。
  const token = ++attachToken
  localStorage.setItem(MATCH_KEY, matchId)
  currentMatchId = matchId
  el('matchId').textContent = matchId
  el('btnRecapEnd').href = `recap.html?match=${encodeURIComponent(matchId)}`
  dismissedEnd = false
  // 切台必须回到直播态：上一场的"暂停/回放"状态带过来会让新场永远停在回放。
  mode = 'live'
  playing = true
  speed = Number(el('speed').value || 1)
  el('btnPause').textContent = '暂停'
  clearTimeout(endTimer)
  endTimer = null
  closeStream?.()
  closeStream = null

  let snapshot
  try {
    snapshot = await fetchMatch(matchId, { baseUrl: API })
  } catch (error) {
    if (token === attachToken) {
      el('conn').textContent = '加载失败'
      el('conn').className = 'conn bad'
      currentMatchId = null
      events = []
      view = createView()
      cursor = 0
      mode = 'live'
      playing = true
      el('stage').classList.add('idle')
      el('matchId').textContent = '—'
      el('connectError').textContent = `拉取失败：${String(error.message || error)}`
      litRender(
        html`<div class="card" style="--tone:#F87171;align-items:center;justify-content:center;text-align:center">
          <div class="pname" style="font-size:15px;color:#F87171">这场没加载出来</div>
          <div class="muted" style="margin-top:8px;line-height:1.7">${String(error.message || error)}</div>
          <div style="display:flex;gap:8px;margin-top:14px">
            <a class="btn" href="index.html">去赛事库</a>
            <button class="btn" type="button" id="retryLoad">重试</button>
          </div>
        </div>`,
        el('sideLeft'),
      )
      litRender(html``, el('sideRight'))
      el('retryLoad').onclick = () => attach(matchId).catch(() => {})
      el('liveLabel').textContent = '加载失败'
      el('liveLabel').className = 'ended'
    }
    throw error
  }
  if (token !== attachToken) return
  events = snapshot.events
  view = replayView(events)
  cursor = events.length
  render()

  if (view.status === 'ended') {
    // 已结束的比赛不需要长连接：接上 SSE 只会立刻被服务端关闭，然后无限重连。
    el('conn').textContent = '已结束'
    el('conn').className = 'conn'
    startPresence(matchId)
    return
  }

  closeStream = await connectEvents(matchId, {
    baseUrl: API,
    onEvent: (event) => {
      appendEvent(event)
      if (event.type === 'match.ended') {
        closeStream?.()
        closeStream = null
        el('conn').textContent = '已结束'
        el('conn').className = 'conn'
      }
    },
    onError: () => {
      if (view.status === 'ended') return
      el('conn').textContent = '重连中'
      el('conn').className = 'conn warn'
    },
    onOpen: () => {
      el('conn').textContent = '已连接'
      el('conn').className = 'conn ok'
    },
  })
  startPresence(matchId)
}

function startPresence(matchId) {
  clearInterval(presenceTimer)
  const poll = async () => {
    try {
      const data = await api.presence(matchId)
      el('viewerCount').textContent = String(data.viewers ?? 0)
    } catch {
      /* 人数是展示项，失败不打扰观众 */
    }
  }
  poll()
  presenceTimer = setInterval(() => {
    if (!document.hidden) poll()
  }, 8000)
}

function markIdle() {
  el('conn').textContent = '待命'
  el('conn').className = 'conn'
  el('viewers').style.display = 'none'
}

function startPlaybackLoop() {
  cancelAnimationFrame(frame)
  const tick = () => {
    if (mode === 'replay' && playing && cursor < events.length) {
      cursor = Math.min(events.length, cursor + 1)
      render()
    }
    const delay = 900 / speed
    setTimeout(() => {
      frame = requestAnimationFrame(tick)
    }, delay)
  }
  tick()
}

// --- 交互 ---

el('scrub').addEventListener('input', (e) => {
  mode = 'replay'
  playing = false
  cursor = Number(e.target.value)
  el('btnPause').textContent = '继续'
  render()
})

el('btnPause').addEventListener('click', () => {
  if (mode === 'live') {
    mode = 'replay'
    cursor = events.length
  }
  playing = !playing
  el('btnPause').textContent = playing ? '暂停' : '继续'
})

el('btnLive').addEventListener('click', () => {
  mode = 'live'
  playing = true
  dismissedEnd = true
  view = replayView(events)
  cursor = events.length
  el('btnPause').textContent = '暂停'
  render()
})

el('endcard').addEventListener('click', (event) => {
  if (event.target === el('endcard')) {
    dismissedEnd = true
    el('endcard').classList.remove('on')
  }
})

function replayFromStart() {
  mode = 'replay'
  playing = true
  cursor = 0
  dismissedEnd = true
  el('btnPause').textContent = '暂停'
  el('endcard').classList.remove('on')
  render()
}

document.addEventListener('keydown', (event) => {
  const target = event.target
  const tag = (target && target.tagName) || ''
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
  // 空格在按钮/链接上是浏览器原生的"激活"，不能被播放器抢走。
  if (target && (target.tagName === 'BUTTON' || target.tagName === 'A' || target.isContentEditable)) return
  if (event.code === 'Space') {
    event.preventDefault()
    el('btnPause').click()
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    mode = 'replay'
    playing = false
    cursor = Math.max(0, cursor - 1)
    render()
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    mode = 'replay'
    playing = false
    cursor = Math.min(events.length, cursor + 1)
    render()
  } else if (event.key === 'l' || event.key === 'L') {
    el('btnLive').click()
  } else if (event.key === 'f' || event.key === 'F') {
    toggleTheater()
  } else if (event.key === 'Escape') {
    el('endcard').classList.remove('on')
    el('clipPanel').classList.remove('on')
  }
})

el('btnReplay').addEventListener('click', replayFromStart)
el('btnReplayEnd').addEventListener('click', replayFromStart)

el('speed').addEventListener('change', (e) => {
  speed = Number(e.target.value)
})

el('btnRender').addEventListener('click', exportHighlights)
el('btnRenderEnd').addEventListener('click', exportHighlights)
el('clipClose').addEventListener('click', () => el('clipPanel').classList.remove('on'))
el('btnTheater').addEventListener('click', () => toggleTheater())
el('theaterExit').addEventListener('click', () => toggleTheater(false))

function toggleTheater(force) {
  const next = typeof force === 'boolean' ? force : !document.body.classList.contains('theater')
  document.body.classList.toggle('theater', next)
  el('theaterExit').style.display = next ? 'flex' : 'none'
  el('btnTheater').textContent = next ? '退出剧场' : '剧场'
  if (next) el('theaterExit').focus()
  requestAnimationFrame(fitStage)
}

async function shareMatch() {
  if (!currentMatchId) return
  // 分端口部署时分享页在 API 站，不在静态站。
  const origin = API ? new URL(API, location.href).origin : location.origin
  const url = `${origin}/share/${currentMatchId}`
  try {
    if (navigator.share) {
      await navigator.share({ title: 'melee 比赛', url })
      return
    }
    await navigator.clipboard.writeText(url)
    toast('分享链接已复制')
  } catch {
    toast('复制失败，请手动复制：' + url, 'bad')
  }
}

el('btnShare').addEventListener('click', shareMatch)

async function exportHighlights() {
  const matchId = currentMatchId
  if (!matchId) return

  const panel = el('clipPanel')
  const body = el('clipBody')
  panel.classList.add('on')
  litRender(html`<div class="cp-empty">正在渲染…（几秒到十几秒）</div>`, body)

  try {
    const result = await renderClips(matchId, { baseUrl: API, limit: 3, perEventMs: 900 })
    if (!result.clips.length) {
      litRender(html`<div class="cp-empty">这场比赛没有可剪的高光</div>`, body)
      return
    }
    const ticket = await fetchMediaTicket(matchId, { baseUrl: API })
    litRender(
      html`${result.clips.map(
        (clip) => html`<div class="clip">
          <div class="c-title">${clip.title}</div>
          <div class="c-meta">${clip.kind} · ${(clip.durationMs / 1000).toFixed(1)}s · ${clip.frames} 帧</div>
          <video controls preload="none" src="${clipUrl(matchId, clip.file, API, ticket)}"></video>
        </div>`,
      )}`,
      body,
    )
  } catch (error) {
    litRender(html`<div class="c-err">${String(error.message || error)}</div>`, body)
  }
}

async function loadNextMatch() {
  try {
    const data = await api.archive({ limit: 8, status: 'ended' })
    const next = data.items.find((item) => item.matchId !== currentMatchId)
    if (!next) {
      toast('没有其它已结束的比赛')
      return
    }
    await attach(next.matchId)
  } catch (error) {
    toast(String(error.message || error), 'bad')
  }
}
el('btnNext').addEventListener('click', loadNextMatch)

// 赛制清单从服务端拉，避免前端写死。
let GAMES = [{ gameId: 'twenty-questions', label: '二十问', players: [2, 2] }]

async function loadGames() {
  try {
    const data = await api.games()
    if (Array.isArray(data.games) && data.games.length) {
      GAMES = data.games
      const picker = el('gamePick')
      litRender(
        html`${GAMES.map((g) => html`<option value="${g.gameId}">${g.label}</option>`)}`,
        picker,
      )
    }
  } catch {
    /* 拉不到就用内置默认 */
  }
}

function playerIdsFor(gameId) {
  const spec = GAMES.find((g) => g.gameId === gameId)
  const count = spec ? spec.players[0] : 2
  if (gameId === 'codenames') return ['红队队长', '蓝队队长', '红队队员', '蓝队队员'].slice(0, count)
  return ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'].slice(0, count)
}

el('btnNew').addEventListener('click', async () => {
  el('connectError').textContent = ''
  const gameId = el('gamePick').value || 'twenty-questions'
  const ids = playerIdsFor(gameId)
  const body = {
    game: gameId,
    seed: Math.floor(Math.random() * 1_000_000),
    players: ids.map((id) => ({ id, delayMs: 1200 })),
  }
  if (gameId === 'twenty-questions') body.maxQuestions = 21

  try {
    const result = await createMatch(body, { baseUrl: API })
    await attach(result.matchId)
  } catch (error) {
    el('connectError').textContent = String(error.message || error)
  }
})

el('btnOpen').addEventListener('click', async () => {
  const id = el('openId').value.trim()
  if (!id) return
  el('connectError').textContent = ''
  try {
    await attach(id)
  } catch (error) {
    el('connectError').textContent = String(error.message || error)
  }
})

window.addEventListener('resize', fitStage)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) fitStage()
})

const initial = params.get('match') || localStorage.getItem(MATCH_KEY)
loadGames()
fitStage()
render()
startPlaybackLoop()
if (initial) {
  attach(initial)
    .then(() => {
      if (params.get('export') === '1') exportHighlights()
    })
    .catch((error) => {
      el('connectError').textContent = String(error.message || error)
    })
} else {
  // 没有指定比赛时，自动接最近一场直播；没有直播就显示空状态，不伪造选手。
  markIdle()
  api.archive({ limit: 1, status: 'live' })
    .then((data) => {
      if (data.items.length) attach(data.items[0].matchId).catch(() => {})
    })
    .catch(() => {})
}
