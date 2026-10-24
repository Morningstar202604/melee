// 首页：赛事库 + 快速开赛。所有计数来自 /stats 与 /archive，没有占位数字。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import {
  api, apiBase, configure, el, gameMeta, propagateQuery, relativeTime, formatDuration, winnerLabel, toast, GAME_META,
} from './api.js'
import { mountTopbar, mountFooter } from './nav.js'

configure()
propagateQuery()
mountTopbar({ active: 'home' })
mountFooter()

const state = {
  games: [],
  filter: '',
  status: '',
  offset: 0,
  limit: 12,
  total: 0,
  loading: false,
  archive: [],
  live: [],
}

function toneStyle(meta) {
  return `--game-tone:${meta.tone};--game-bg:${meta.bg}`
}

function skeleton(count = 6) {
  return Array.from({ length: count }, () => html`<div class="skeleton"></div>`)
}

function renderLive() {
  const strip = el('liveStrip')
  if (!state.live.length) {
    litRender(
      html`<div class="empty-state" style="grid-column:1/-1">
        当前没有直播中的比赛。<button class="btn sm" id="emptyQuick" type="button" style="margin-left:8px">开一局</button>
      </div>`,
      strip,
    )
    const button = document.getElementById('emptyQuick')
    if (button) button.onclick = openQuick
    el('liveCount').textContent = ''
    return
  }
  el('liveCount').textContent = `${state.live.length} 场进行中`
  litRender(
    html`${state.live.map((item) => {
      const meta = gameMeta(item.game)
      return html`<a class="live-card" style=${toneStyle(meta)} href="watch.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>
        <div class="row">
          <span class="badge-live"><span class="dot-live"></span>LIVE</span>
          <span class="game">${meta.label}</span>
          <span class="muted mono" style="margin-left:auto">${item.events} 事件</span>
        </div>
        <div class="players">${item.players.join(' vs ')}</div>
        <div class="meta">
          <span>${relativeTime(item.createdAt)}开赛</span>
          <span class="mono">${item.matchId}</span>
        </div>
      </a>`
    })}`,
    strip,
  )
}

function matchCard(item, maxEvents) {
  const meta = gameMeta(item.game)
  const winner = winnerLabel(item.winner)
  // 只画真实数据：这一场的事件数占当前列表最大值的比例。
  const share = maxEvents > 0 ? Math.max(6, Math.round((item.events / maxEvents) * 100)) : 6
  return html`<article class="mcard" style=${toneStyle(meta)}>
    <div class="top">
      <span class="glyph">${meta.glyph}</span>
      <span class="game-label">${meta.label}</span>
      <span class="spacer" style="margin-left:auto"></span>
      ${item.status === 'live'
        ? html`<span class="badge-live"><span class="dot-live"></span>LIVE</span>`
        : html`<span class="tag">已结束</span>`}
    </div>
    <div class="title">
      ${winner ? html`<span class="winner">${winner}</span><span class="vs">胜</span>` : html`<span>${item.players.join(' vs ')}</span>`}
    </div>
    ${winner ? html`<div class="muted" style="font-size:12px">${item.players.join(' vs ')}</div>` : null}
    <div class="mid">
      <span class="mono">${item.events} 事件 · ${formatDuration(item.firstAt, item.lastAt) || '—'}</span>
      <span>${relativeTime(item.createdAt)}</span>
    </div>
    <div class="meter" aria-hidden="true" title="事件量相对本页最大值"><i style="width:${share}%"></i></div>
    <div class="footer">
      <a class="btn sm" href="watch.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>观战</a>
      <a class="btn sm ghost" href="recap.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>战报</a>
      <span class="spacer" style="margin-left:auto"></span>
      <button class="btn sm ghost" type="button" data-copy="${item.matchId}" title="复制分享链接">分享</button>
    </div>
  </article>`
}

function renderArchive() {
  const box = el('archive')
  if (state.loading && !state.archive.length) {
    litRender(html`${skeleton(6)}`, box)
    return
  }
  if (!state.archive.length) {
    litRender(
      html`<div class="empty-state" style="grid-column:1/-1">还没有比赛。点「立即开一局」跑第一场。</div>`,
      box,
    )
    el('btnMore').style.display = 'none'
    return
  }
  const maxEvents = Math.max(1, ...state.archive.map((item) => item.events))
  litRender(html`${state.archive.map((item) => matchCard(item, maxEvents))}`, box)
  for (const button of box.querySelectorAll('[data-copy]')) {
    button.onclick = () => share(button.dataset.copy)
  }
  el('btnMore').style.display = state.archive.length < state.total ? '' : 'none'
  propagateQuery()
}

async function share(matchId) {
  // 分享页在 API 站；分端口部署时 location.origin 是静态站，会 404。
  const origin = apiBase() ? new URL(apiBase(), location.href).origin : location.origin
  const url = `${origin}/share/${matchId}`
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

let archiveToken = 0

async function loadArchive({ reset = false } = {}) {
  // 筛选连点时不能直接 return：后一次筛选必须赢，否则选中态和列表会对不上。
  const token = ++archiveToken
  state.loading = true
  if (reset) {
    state.offset = 0
    state.archive = []
    renderArchive()
  }
  try {
    const data = await api.archive({
      limit: state.limit,
      offset: state.offset,
      game: state.filter,
      status: state.status,
    })
    if (token !== archiveToken) return
    state.total = data.count
    state.archive = state.offset === 0 ? data.items : state.archive.concat(data.items)
    state.offset = state.archive.length
  } catch (error) {
    if (token !== archiveToken) return
    litRender(html`<div class="empty-state" style="grid-column:1/-1">加载失败：${String(error.message || error)}</div>`, el('archive'))
    state.loading = false
    return
  }
  if (token !== archiveToken) return
  state.loading = false
  renderArchive()
}

async function loadLive() {
  try {
    const data = await api.archive({ limit: 3, status: 'live' })
    state.live = data.items
  } catch {
    state.live = []
  }
  renderLive()
}

async function loadStats() {
  try {
    const data = await api.stats()
    const kpis = el('kpis').querySelectorAll('.kpi b')
    kpis[0].textContent = String(data.matches ?? 0)
    kpis[1].textContent = String(data.events ?? 0)
    kpis[2].textContent = String((data.byGame || []).length || state.games.length)
    kpis[3].textContent = String(data.viewers ?? 0)

    const days = data.activity || []
    const byDay = new Map(days.map((day) => [day.day, day.matches]))
    const slots = []
    for (let offset = 13; offset >= 0; offset -= 1) {
      const date = new Date(Date.now() - offset * 86400000)
      const key = date.toISOString().slice(0, 10)
      slots.push({ day: key, matches: byDay.get(key) || 0 })
    }
    const max = Math.max(1, ...slots.map((day) => day.matches))
    litRender(
      html`${slots.map(
        (day) => html`<i style="height:${Math.max(3, (day.matches / max) * 100)}%" title="${day.day} · ${day.matches} 场"></i>`,
      )}`,
      el('activity'),
    )
    el('activityMeta').textContent = days.length
      ? `14 天共 ${days.reduce((sum, d) => sum + d.matches, 0)} 场 · 峰值 ${max} 场/天`
      : '最近 14 天没有比赛'

    litRender(
      data.topWinners && data.topWinners.length
        ? html`${data.topWinners.map(
            (row, index) => html`<div class="leader"><span class="rk">${index + 1}</span>
              <span class="nm">${winnerLabel(row.player)}</span><span class="w">${row.wins} 胜</span></div>`,
          )}`
        : html`<span class="muted">还没有比赛结果</span>`,
      el('leaders'),
    )
  } catch {
    /* 统计失败不影响主内容 */
  }
}

function renderGames() {
  litRender(
    html`${state.games.map((game) => {
      const meta = gameMeta(game.gameId)
      return html`<button class="gcard" type="button" data-game="${game.gameId}" style=${toneStyle(meta)}>
        <div class="g">
          <span class="glyph">${meta.glyph}</span>
          <b>${game.label}</b>
        </div>
        <p>${game.source === 'textarena' ? 'TextArena 确定性环境' : '内置引擎'} · ${game.players[0]} 人</p>
        <div class="pill-row"><span class="tag info">同种子同结果</span><span class="tag">实时解说</span></div>
      </button>`
    })}`,
    el('games'),
  )
  for (const card of el('games').querySelectorAll('[data-game]')) {
    card.onclick = () => openQuick(card.dataset.game)
  }
}

function renderFilters() {
  const filters = [{ id: '', label: '全部' }, ...state.games.map((game) => ({
    id: game.gameId,
    label: GAME_META[game.gameId]?.label || game.label,
  }))]
  litRender(
    html`${filters.map(
      (item) => html`<button class="chip ${state.filter === item.id ? 'on' : ''}" type="button" data-filter="${item.id}">${item.label}</button>`,
    )}`,
    el('gameFilter'),
  )
  for (const chip of el('gameFilter').querySelectorAll('[data-filter]')) {
    chip.onclick = () => {
      state.filter = chip.dataset.filter
      renderFilters()
      loadArchive({ reset: true })
    }
  }
}

async function loadGames() {
  try {
    const data = await api.games()
    state.games = data.games || []
  } catch {
    state.games = []
  }
  renderGames()
  renderFilters()
}

// ---- 快速开赛 ----
function defaultPlayers(gameId) {
  const game = state.games.find((item) => item.gameId === gameId)
  const count = game ? game.players[0] : 2
  if (gameId === 'codenames') return ['红队队长', '蓝队队长', '红队队员', '蓝队队员'].slice(0, count)
  return ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛'].slice(0, count)
}

function renderQuickPlayers(gameId) {
  const players = defaultPlayers(gameId)
  const box = el('qPlayers')
  litRender(
    html`${players.map(
      (name, index) => html`<div class="field" style="margin-bottom:0">
        <label>选手 ${index + 1}</label>
        <input class="input" data-player="${index}" value="${name}" maxlength="32">
      </div>`,
    )}`,
    box,
  )
  const game = state.games.find((item) => item.gameId === gameId)
  el('qGameHint').textContent = game
    ? `${game.label} · ${game.players[0]} 人 · ${game.source === 'textarena' ? 'TextArena 环境' : '内置引擎'}`
    : ''
}

function openQuick(gameId = '') {
  if (!state.games.length) {
    toast('赛制列表还没加载完，稍后再试', 'bad')
    return
  }
  const pick = el('qGame')
  // 每次都重画：保证 option 与当前 state.games 一致，且 value 一定落在合法项上。
  litRender(
    html`${state.games.map((game) => html`<option value="${game.gameId}">${game.label}</option>`)}`,
    pick,
  )
  const wanted = gameId || pick.value
  pick.value = state.games.some((game) => game.gameId === wanted) ? wanted : state.games[0].gameId
  renderQuickPlayers(pick.value)
  el('quickModal').classList.add('on')
  el('qGame').focus()
}

function closeQuick() {
  el('quickModal').classList.remove('on')
}

async function startQuick() {
  const gameId = el('qGame').value
  const names = [...el('qPlayers').querySelectorAll('[data-player]')].map((input) => input.value.trim())
  if (names.some((name) => !name)) {
    toast('选手名不能为空', 'bad')
    return
  }
  if (new Set(names).size !== names.length) {
    toast('选手名必须唯一', 'bad')
    return
  }
  const seedText = el('qSeed').value.trim()
  const body = {
    game: gameId,
    players: names.map((id) => ({ id })),
  }
  if (seedText) {
    const seed = Number(seedText)
    if (!Number.isFinite(seed)) {
      toast('种子必须是数字', 'bad')
      return
    }
    body.seed = Math.trunc(seed)
  }

  const base = el('qBase').value.trim()
  const model = el('qModel').value.trim()
  const key = el('qKey').value
  if (base || model || key) {
    if (!(base && model && key)) {
      toast('Base URL、模型名、API Key 必须同时填写', 'bad')
      return
    }
    body.players[0].model = { baseUrl: base, model, apiKey: key }
    el('qKey').value = ''
  }

  const button = el('quickStart')
  button.disabled = true
  button.textContent = '开赛中…'
  try {
    const result = await api.createMatch(body)
    location.href = `watch.html?match=${encodeURIComponent(result.matchId)}`
  } catch (error) {
    toast(String(error.message || error), 'bad')
    button.disabled = false
    button.textContent = '开赛并观战'
  }
}

el('btnQuick').onclick = () => openQuick()
el('quickClose').onclick = closeQuick
el('quickCancel').onclick = closeQuick
el('quickStart').onclick = startQuick
el('qGame').onchange = (event) => renderQuickPlayers(event.target.value)
el('quickModal').addEventListener('click', (event) => {
  if (event.target === el('quickModal')) closeQuick()
})
document.addEventListener('keydown', (event) => {
  if (!el('quickModal').classList.contains('on')) return
  if (event.key === 'Escape') closeQuick()
  if (event.key === 'Enter' && event.target.tagName === 'INPUT') startQuick()
})

el('btnRefresh').onclick = () => {
  loadLive()
  loadStats()
  loadArchive({ reset: true })
}
el('statusFilter').onchange = (event) => {
  state.status = event.target.value
  loadArchive({ reset: true })
}
el('btnMore').onclick = () => loadArchive()

// 有 ?match= 的旧链接直接进观战页，保持兼容。
const legacyMatch = new URLSearchParams(location.search).get('match')
if (legacyMatch) {
  const query = new URLSearchParams(location.search)
  location.replace(`watch.html?${query.toString()}`)
}

async function boot() {
  renderArchive()
  await loadGames()
  await Promise.all([loadLive(), loadStats()])
  await loadArchive({ reset: true })
}

boot()

// 直播卡片每 20 秒自动刷新（页面隐藏时暂停，避免后台空转）。
setInterval(() => {
  if (!document.hidden) loadLive()
}, 20000)
