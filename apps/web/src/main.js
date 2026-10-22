// 渲染用 lit-html（8KB，无构建）：模板自动转义，不再手写 innerHTML + escapeHtml。
import { html, render as litRender } from '../vendor/lit-html.js'
import { connectEvents, fetchMatch, createMatch, renderClips, clipUrl, setToken, fetchMediaTicket } from './sse.js'
import { createView, reduce, replayView, latestThoughtOf, currentCue } from './view.js'

const params = new URLSearchParams(location.search)
const API = params.get('api') || ''
const token = params.get('token') || ''
// 导航栏会把 token 带过来；不在这里注册，token 模式下观战页永远 401。
if (token) setToken(token)
const MATCH_KEY = 'melee.currentMatch'

const TEAM_LABELS = { red: '红队', blue: '蓝队', neutral: '中立', assassin: '刺客' }
const prettyTeam = (value) => TEAM_LABELS[value] || value

const GAME_LABELS = {
  'twenty-questions': '二十问',
  'tic-tac-toe': '井字棋',
  'connect-four': '四子棋',
  othello: '黑白棋',
  nim: '取石子',
  codenames: '代号',
}

const el = (id) => document.getElementById(id)

let view = createView()
let events = []
let cursor = events.length
let mode = 'live'
let speed = 1
let playing = true
let closeStream = null
let frame = null
let endTimer = null
let dismissedEnd = false
let currentMatchId = null

function fitStage() {
  const frameEl = el('frame')
  const stage = el('stage')
  // 窄屏走纵向堆叠，不需要也不应该缩放。
  if (window.matchMedia('(max-width: 900px)').matches) {
    stage.style.transform = ''
    stage.style.marginLeft = ''
    stage.style.marginTop = ''
    return
  }
  const availableW = frameEl.clientWidth
  const availableH = frameEl.clientHeight
  // 窄屏优先满足宽度：舞台整体缩小，而不是被裁掉右侧。
  const scale = Math.min(availableW / 1600, availableH / 900) * 0.985

  // transform 不改变布局盒：显式把舞台盒收窄到缩放后的尺寸，
  // 再用 margin 抵消 transform-origin 造成的偏移，才能真正居中。
  stage.style.transform = `scale(${scale})`
  stage.style.transformOrigin = 'top left'
  stage.style.width = '1600px'
  stage.style.height = '900px'
  stage.style.marginLeft = `${Math.max(0, (availableW - 1600 * scale) / 2)}px`
  stage.style.marginTop = '8px'
}

function render() {
  const shown = mode === 'live' ? view : replayView(events, cursor)
  const lastLine = shown.narration.length ? shown.narration[shown.narration.length - 1].text : ''
  el('narration').textContent = lastLine || '等待第一条解说。'
  el('headline').textContent = lastLine || '等待第一条解说。'
  const total = Math.max(shown.maxQuestions, 1)
  const remain = shown.remaining ?? total
  el('bar').style.width = `${Math.max(0, Math.min(100, (remain / total) * 100))}%`
  el('tlCount').textContent = `${events.length} 条`

  const cue = currentCue(shown)
  el('cue').textContent = cue ? `${cue.shot} · ${cue.trigger}` : '未切镜'

  renderSides(shown)
  renderArena(shown)
  renderTimeline()
  renderStatus(shown)

  el('liveLabel').textContent = shown.status === 'ended' ? '已结束' : mode === 'replay' ? '回放' : 'LIVE'
  el('liveLabel').className = shown.status === 'ended' ? 'ended' : mode === 'replay' ? 'replay' : ''

  if (shown.status === 'ended' && !dismissedEnd && mode === 'live') {
    if (!endTimer) {
      // 终局不立刻盖画面：先让观众看一眼最后一步。
      endTimer = setTimeout(() => {
        el('winner').textContent = prettyTeam(shown.winner) || '无人获胜'
        el('endreason').textContent = String(shown.endReason || '')
          .replace(/red/g, '红队')
          .replace(/blue/g, '蓝队')
        el('endcard').classList.add('on')
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

// 按选手数量动态分配到左右两栏，2 人时一人一边，4 人时 2v2。
function renderSides(shown) {
  const players = shown.players && shown.players.length ? shown.players : ['A', 'B']
  const half = Math.ceil(players.length / 2)
  litRender(html`${players.slice(0, half).map((p, i) => playerCard(p, i, shown))}`, el('sideLeft'))
  litRender(html`${players.slice(half).map((p, i) => playerCard(p, i + half, shown))}`, el('sideRight'))
}

function renderArena(shown) {
  const isQuiz = shown.gameKind === 'twenty-questions'
  el('gameLabel').textContent = 'TRANSMISSION · ' + (GAME_LABELS[shown.gameKind] || shown.gameKind)

  // 顶栏的"第 N 手 / 上限 M 手"只对二十问成立；棋类改显示事件数。
  litRender(
    isQuiz
      ? html`第 <b id="qnum">${shown.turnIndex}</b> 手 / 上限 <b id="maxq">${shown.maxQuestions}</b> 手`
      : html`第 <b id="qnum">${shown.turnIndex ?? 0}</b> 步`,
    el('turnPill'),
  )
  litRender(
    isQuiz
      ? html`候选 <b id="remain">${shown.remaining ?? '—'}</b>`
      : html`<span class="muted">${GAME_LABELS[shown.gameKind] || ''}</span>`,
    el('remainPill'),
  )
  const title = el('panelTitle')
  const meta = el('panelMeta')

  if (isQuiz) {
    title.textContent = '公开提问记录'
    litRender(html`剩 <b id="remainHead">${shown.remaining ?? '—'}</b> 个候选`, meta)
    return renderHistory(shown)
  }
  title.textContent = '当前局面'
  meta.textContent = GAME_LABELS[shown.gameKind] || shown.gameKind
  if (shown.gameKind === 'codenames') return renderCodenames(shown)
  if (shown.gameKind === 'nim') return renderPiles(shown)
  renderBoard(shown)
}

// 取石子：三堆石子用横条长度表示，一眼看出哪堆还能拿。
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

// 代号：5×5 词阵。只显示已翻开的颜色，未翻开的保持中性。
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
  litRender(
    recent.length
      ? html`${recent.map(
          (item) => html`<div class="hrow"><b class="${item.by === 'A' ? 'a' : 'b'}">${item.by}</b>
            <span>${item.value}</span><i class="${item.answer === 'yes' ? 'y' : 'n'}">${item.answer === 'yes' ? '是' : '否'}</i></div>`,
        )}`
      : html`<div class="empty">等待第一次提问</div>`,
    el('historyList'),
  )
}

function renderTimeline() {
  const fill = el('tlFill')
  const total = Math.max(events.length, 1)
  const position = mode === 'live' ? total : cursor
  fill.style.width = `${(position / total) * 100}%`
  el('scrub').max = String(total)
  el('scrub').value = String(position)
  el('tlCount').textContent = `${position} / ${total}`
  el('replayBadge').style.display = mode === 'replay' ? 'inline-flex' : 'none'
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
  // 这是实测出来的：同一局比赛，选手卡的记录会重复 8 次。
  if (events.length && event.seq <= events[events.length - 1].seq) return

  events = events.concat(event)
  if (mode === 'live') {
    view = reduce(view, event)
    cursor = events.length
    render()
  }
}

async function attach(matchId) {
  localStorage.setItem(MATCH_KEY, matchId)
  currentMatchId = matchId
  el('matchId').textContent = matchId
  closeStream?.()

  const snapshot = await fetchMatch(matchId, { baseUrl: API })
  events = snapshot.events
  view = replayView(events)
  cursor = events.length
  render()

  // connectEvents 是 async（token 模式要先换票据），必须等它返回真正的
  // close 函数；直接赋值会得到一个 Promise，下一次 attach 调用就报
  // "closeStream is not a function"。
  closeStream = await connectEvents(matchId, {
    baseUrl: API,
    onEvent: (event) => appendEvent(event),
    onError: () => {
      el('conn').textContent = '重连中'
      el('conn').className = 'conn warn'
    },
    onOpen: () => {
      el('conn').textContent = '已连接'
      el('conn').className = 'conn ok'
    },
  })
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
  // 用户主动点"回到直播"就是"我不想再看终局卡"：记住这个决定，
  // 否则已结束的比赛会每帧重新弹出终局卡，挡住整个舞台。
  dismissedEnd = true
  view = replayView(events)
  cursor = events.length
  el('btnPause').textContent = '暂停'
  render()
})

// 终局卡支持点遮罩关闭（Esc 也可以），否则只能靠"从头回放"绕开。
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

// 键盘操作：观战页的高频动作不该只能用鼠标。
// 输入框聚焦时不拦截，避免把数字打进搜索框却触发回放。
document.addEventListener('keydown', (event) => {
  const tag = (event.target && event.target.tagName) || ''
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
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
  } else if (event.key === 'Escape') {
    el('endcard').classList.remove('on')
    el('clipPanel').classList.remove('on')
    el('btnLive').click()
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
          <video controls preload="none" poster="" src="${clipUrl(matchId, clip.file, API, ticket)}"></video>
        </div>`,
      )}`,
      body,
    )
  } catch (error) {
    litRender(html`<div class="c-err">${String(error.message || error)}</div>`, body)
  }
}

// 赛制清单从服务端拉，避免前端写死。
let GAMES = [{ gameId: 'twenty-questions', label: '二十问', players: [2, 2] }]

async function loadGames() {
  try {
    const response = await fetch(`${API}/games`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!response.ok) return
    const data = await response.json()
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

// 启动：优先用 URL 里的 match，其次用上次看过的
const initial = params.get('match') || localStorage.getItem(MATCH_KEY)
loadGames()
fitStage()
render()
startPlaybackLoop()
if (initial) {
  attach(initial)
    .then(() => {
      // ?export=1 直接触发导出，用于演示与截图验证
      if (params.get('export') === '1') exportHighlights()
    })
    .catch((error) => {
      el('connectError').textContent = String(error.message || error)
    })
}
