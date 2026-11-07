// 首页：赛事库 + 快速开赛。所有计数来自 /stats 与 /archive，没有占位数字。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import {
  api, configure, el, gameMeta, propagateQuery, relativeTime, formatDuration, winnerLabel, shareUrl, toast,
} from './api.js'
import { mountTopbar, mountFooter } from './nav.js'
import { trapFocus } from './a11y.js'
import { applyI18n, getLang, resolveLang, setLang, t } from './i18n.js'

setLang(resolveLang())
configure()
propagateQuery()
mountTopbar({ active: 'home' })
mountFooter()
document.title = t('home.title')
applyI18n()

const state = {
  games: [],
  filter: '',
  status: '',
  q: '',
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

function numberOrNull(id) {
  const raw = el(id).value.trim()
  if (!raw) return null
  const value = Number(raw)
  return Number.isFinite(value) ? value : null
}

function skeleton(count = 6) {
  return Array.from({ length: count }, () => html`<div class="skeleton"></div>`)
}

/** 赛制展示名：优先走本地字典，未登记的新赛制回退服务端 label。 */
function labelOf(game) {
  const meta = gameMeta(game.gameId, game.label)
  return meta.label
}

function renderLive() {
  const strip = el('liveStrip')
  if (!state.live.length) {
    litRender(
      html`<div class="empty-state" style="grid-column:1/-1">
        ${t('home.live.empty')}<button class="btn sm" id="emptyQuick" type="button" style="margin-left:8px">${t('home.live.start')}</button>
      </div>`,
      strip,
    )
    const button = document.getElementById('emptyQuick')
    if (button) button.onclick = openQuick
    el('liveCount').textContent = ''
    return
  }
  el('liveCount').textContent = t('home.live.count', { count: state.live.length })
  litRender(
    html`${state.live.map((item) => {
      const meta = gameMeta(item.game)
      return html`<a class="live-card" style=${toneStyle(meta)} href="watch.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>
        <div class="row">
          <span class="badge-live"><span class="dot-live"></span>LIVE</span>
          <span class="game">${labelOf({ gameId: item.game })}</span>
          <span class="muted mono" style="margin-left:auto">${t('home.live.events', { count: item.events })}</span>
        </div>
        <div class="players">${item.players.join(' vs ')}</div>
        <div class="meta">
          <span>${t('home.live.started', { time: relativeTime(item.createdAt) })}</span>
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
      <span class="game-label">${labelOf({ gameId: item.game })}</span>
      <span class="spacer" style="margin-left:auto"></span>
      ${item.status === 'live'
        ? html`<span class="badge-live"><span class="dot-live"></span>LIVE</span>`
        : html`<span class="tag">${t('home.library.ended')}</span>`}
    </div>
    <div class="title">
      ${winner ? html`<span class="winner">${winner}</span><span class="vs">${t('home.library.win')}</span>` : html`<span>${item.players.join(' vs ')}</span>`}
    </div>
    ${winner ? html`<div class="muted" style="font-size:12px">${item.players.join(' vs ')}</div>` : null}
    <div class="mid">
      <span class="mono">${t('home.live.events', { count: item.events })} · ${formatDuration(item.firstAt, item.lastAt) || '—'}</span>
      <span>${relativeTime(item.createdAt)}</span>
    </div>
    <div class="meter" aria-hidden="true" title="${t('home.library.meterTitle')}"><i style="width:${share}%"></i></div>
    <div class="footer">
      <a class="btn sm" href="watch.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>${t('home.library.watch')}</a>
      <a class="btn sm ghost" href="recap.html?match=${encodeURIComponent(item.matchId)}" data-keep-query>${t('home.library.recap')}</a>
      <span class="spacer" style="margin-left:auto"></span>
      <button class="btn sm ghost" type="button" data-copy="${item.matchId}" title="${t('common.copyLink')}">${t('common.share')}</button>
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
    // 区分"没有比赛"与"筛选/搜索没有命中"：后者要提示清空条件，
    // 否则用户会以为整个赛事库空了。
    const filtered = state.filter || state.status || state.q
    litRender(
      html`<div class="empty-state" style="grid-column:1/-1">${
        filtered
          ? html`${t('home.library.emptyFiltered')}${state.q ? html`<button class="btn sm ghost" type="button" id="clearSearch">${t('home.library.clearSearch')}</button>` : ''}`
          : html`${t('home.library.empty')}`
      }</div>`,
      box,
    )
    const clear = document.getElementById('clearSearch')
    if (clear) {
      clear.onclick = () => {
        state.q = ''
        el('searchBox').value = ''
        loadArchive({ reset: true })
      }
    }
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
  const url = shareUrl(matchId)
  try {
    if (navigator.share) {
      await navigator.share({ title: t('common.shareTitle'), url })
      return
    }
    await navigator.clipboard.writeText(url)
    toast(t('common.copied'))
  } catch {
    toast(t('common.copyFailed', { url }), 'bad')
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
      q: state.q,
    })
    if (token !== archiveToken) return
    state.total = data.count
    state.archive = state.offset === 0 ? data.items : state.archive.concat(data.items)
    state.offset = state.archive.length
  } catch (error) {
    if (token !== archiveToken) return
    litRender(html`<div class="empty-state" style="grid-column:1/-1">${t('home.library.loadFailed', { error: String(error.message || error) })}</div>`, el('archive'))
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
    // KPI 要显示"可用赛制数"，不是"已开过赛的赛制数"：
    // 只跑过两种赛制时，旧实现显示 2，与实际 9 种不符。
    kpis[2].textContent = String(state.games.length || (data.byGame || []).length)
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
        (day) => html`<i class="${day.matches ? '' : 'zero'}"
          style="height:${day.matches ? Math.max(6, (day.matches / max) * 100) : 3}%"
          title="${day.day} · ${day.matches}" aria-label="${day.day} ${day.matches}"></i>`,
      )}`,
      el('activity'),
    )
    // 首/中/末三个日期刻度：没有它，14 根柱子只是装饰。
    const shortDay = (iso) => iso.slice(5).replace('-', '/')
    el('activityAxis').textContent = slots.length
      ? `${shortDay(slots[0].day)}　·　${shortDay(slots[6].day)}　·　${shortDay(slots[13].day)}`
      : ''
    el('activityMeta').textContent = days.length
      ? t('home.activity.summary', {
          matches: days.reduce((sum, d) => sum + d.matches, 0),
          peak: max,
        })
      : t('home.activity.none')

    litRender(
      data.topWinners && data.topWinners.length
        ? html`${data.topWinners.map(
            (row, index) => html`<div class="leader"><span class="rk">${index + 1}</span>
              <span class="nm">${winnerLabel(row.player)}</span><span class="w">${t(row.wins === 1 ? 'home.leaders.win' : 'home.leaders.wins', { count: row.wins })}</span></div>`,
          )}`
        : html`<span class="muted">${t('home.leaders.empty')}</span>`,
      el('leaders'),
    )
  } catch {
    /* 统计失败不影响主内容 */
  }
}

function renderGames() {
  litRender(
    html`${state.games.map((game) => {
      const meta = gameMeta(game.gameId, game.label)
      return html`<button class="gcard" type="button" data-game="${game.gameId}" style=${toneStyle(meta)}>
        <div class="g">
          <span class="glyph">${meta.glyph}</span>
          <b>${meta.label}</b>
        </div>
        <p>${game.source === 'textarena' ? t('home.games.source.textarena') : t('home.games.source.builtin')} · ${t('home.games.players', { count: game.players[0] })}</p>
        <div class="pill-row"><span class="tag info">${t('home.games.deterministic')}</span><span class="tag">${t('home.games.narration')}</span></div>
      </button>`
    })}`,
    el('games'),
  )
  for (const card of el('games').querySelectorAll('[data-game]')) {
    card.onclick = () => openQuick(card.dataset.game)
  }
}

function renderFilters() {
  const filters = [{ id: '', label: t('home.filter.all') }, ...state.games.map((game) => ({
    id: game.gameId,
    label: gameMeta(game.gameId, game.label).label,
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
// 选手默认名跟随语言：英文界面下不该出现「甲/乙」，否则一眼看出是硬编码。
const PLAYER_PRESETS = {
  zh: ['甲', '乙', '丙', '丁', '戊', '己', '庚', '辛'],
  en: ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta'],
}
const CODENAMES_PRESETS = {
  zh: ['红队队长', '蓝队队长', '红队队员', '蓝队队员'],
  en: ['Red Spymaster', 'Blue Spymaster', 'Red Operative', 'Blue Operative'],
}

function defaultPlayers(gameId) {
  const game = state.games.find((item) => item.gameId === gameId)
  const count = game ? game.players[0] : 2
  const lang = getLang() === 'zh' ? 'zh' : 'en'
  if (gameId === 'codenames') return CODENAMES_PRESETS[lang].slice(0, count)
  return PLAYER_PRESETS[lang].slice(0, count)
}

function renderQuickPlayers(gameId) {
  const players = defaultPlayers(gameId)
  const box = el('qPlayers')
  litRender(
    html`${players.map(
      (name, index) => html`<div class="field" style="margin-bottom:0">
        <label>${t('quick.player', { index: index + 1 })}</label>
        <input class="input" data-player="${index}" value="${name}" maxlength="32">
      </div>`,
    )}`,
    box,
  )
  const game = state.games.find((item) => item.gameId === gameId)
  el('qGameHint').textContent = game
    ? `${gameMeta(game.gameId, game.label).label} · ${t('home.games.players', { count: game.players[0] })} · ${game.source === 'textarena' ? t('home.games.source.textarena') : t('home.games.source.builtin')}`
    : ''
}

/** 首次开赛的默认赛制：挑最容易上手的一个，而不是字母序第一个。
 *
 * 字母序第一个是 Codenames（4 人、隐藏信息、队长/队员两阶段），
 * 对第一次点"Start a match"的人门槛过高——他要先理解规则才能开始。
 * 井字棋这种一眼看懂的先跑通一遍，再去看复杂的。
 */
function preferredGame() {
  const order = ['tic-tac-toe', 'nim', 'twenty-questions', 'othello']
  for (const id of order) {
    if (state.games.some((game) => game.gameId === id)) return id
  }
  return state.games[0]?.gameId || ''
}

let releaseQuickFocus = null

function openQuick(gameId = '') {
  if (!state.games.length) {
    toast(t('quick.err.gamesLoading'), 'bad')
    return
  }
  const pick = el('qGame')
  // 每次都重画：保证 option 与当前 state.games 一致，且 value 一定落在合法项上。
  litRender(
    html`${state.games.map((game) => html`<option value="${game.gameId}">${gameMeta(game.gameId, game.label).label}</option>`)}`,
    pick,
  )
  // 重新渲染 <option> 后，浏览器的 value 会回到第一项，所以这里必须
  // 显式决定"打开时选中哪个赛制"，不能依赖 pick.value 的旧值。
  const wanted = gameId || preferredGame()
  pick.value = state.games.some((game) => game.gameId === wanted) ? wanted : preferredGame()
  renderQuickPlayers(pick.value)
  el('quickModal').classList.add('on')
  // 焦点关进弹层：Tab 不会跑到背后的赛事库，关掉后回到「立即开一局」。
  releaseQuickFocus = trapFocus(el('quickModal').querySelector('.sheet'))
}

function closeQuick() {
  el('quickModal').classList.remove('on')
  releaseQuickFocus?.()
  releaseQuickFocus = null
}

async function startQuick() {
  const gameId = el('qGame').value
  const names = [...el('qPlayers').querySelectorAll('[data-player]')].map((input) => input.value.trim())
  if (names.some((name) => !name)) {
    toast(t('quick.err.emptyName'), 'bad')
    return
  }
  if (new Set(names).size !== names.length) {
    toast(t('quick.err.duplicateName'), 'bad')
    return
  }
  const seedText = el('qSeed').value.trim()
  // 默认给一个可观看的节奏：0 延迟会让一局井字棋在两秒内跑完，
  // 用户点"Start and watch"后看到的是一个已经结束的页面——观战页失去意义。
  // 900ms/步是"能跟上、又不拖沓"的经验值；BYOK 真实模型本身有延迟，不再叠加。
  const body = {
    game: gameId,
    players: names.map((id) => ({ id })),
  }
  // 内置选手（没有 BYOK）才有"节奏"这个概念；真实模型自带网络与推理延迟，
  // 再叠 900ms 只会让一局变慢得没必要。
  const useDelay = !el('qBase').value.trim() && !el('qModel').value.trim() && !el('qKey').value
  if (useDelay) {
    for (const spec of body.players) spec.delayMs = 900
  }
  if (seedText) {
    const seed = Number(seedText)
    if (!Number.isFinite(seed)) {
      toast(t('quick.err.seedNumber'), 'bad')
      return
    }
    body.seed = Math.trunc(seed)
  }

  const base = el('qBase').value.trim()
  const model = el('qModel').value.trim()
  const key = el('qKey').value
  if (base || model || key) {
    if (!(base && model && key)) {
      toast(t('quick.err.byokPartial'), 'bad')
      return
    }
    const spec = { baseUrl: base, model, apiKey: key }
    // 高级参数：只有填了才下发，留空=服务端默认值（与升级前完全一致）。
    const protocol = el('qProtocol').value
    if (protocol && protocol !== 'openai') spec.protocol = protocol
    const maxTokens = numberOrNull('qMaxTokens')
    if (maxTokens !== null) spec.maxTokens = maxTokens
    const temperature = numberOrNull('qTemperature')
    if (temperature !== null) spec.temperature = temperature
    const topP = numberOrNull('qTopP')
    if (topP !== null) spec.topP = topP
    const systemPrompt = el('qSystemPrompt').value.trim()
    if (systemPrompt) spec.systemPrompt = systemPrompt
    if (el('qIncludeHistory').checked) spec.includeHistory = true
    body.players[0].model = spec
    el('qKey').value = ''
  }

  const turnTimeout = numberOrNull('qTurnTimeout')
  if (turnTimeout !== null) {
    if (!Number.isInteger(turnTimeout) || turnTimeout < 1000 || turnTimeout > 600000) {
      toast(t('quick.err.timeoutRange'), 'bad')
      return
    }
    body.config = { turnTimeoutMs: turnTimeout }
  }

  const button = el('quickStart')
  button.disabled = true
  button.textContent = t('quick.starting')
  try {
    const result = await api.createMatch(body)
    location.href = `watch.html?match=${encodeURIComponent(result.matchId)}`
  } catch (error) {
    toast(String(error.message || error), 'bad')
    button.disabled = false
    button.textContent = t('quick.start')
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
let searchTimer = null
el('searchBox').addEventListener('input', (event) => {
  // 输入停顿 250ms 再查：每敲一个字就请求会把列表抖成幻灯片。
  clearTimeout(searchTimer)
  const value = event.target.value.trim()
  searchTimer = setTimeout(() => {
    state.q = value
    loadArchive({ reset: true })
  }, 250)
})
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
