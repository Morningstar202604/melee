// 赛季页：积分榜、赛程、宿敌、推进赛程、新建赛季。所有数据来自 /seasons 与 /seasons/{id}。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, configure, el, propagateQuery, toast, gameMeta, formatTime } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'
import { trapFocus } from './a11y.js'
import { applyI18n, getLang, resolveLang, setLang, t } from './i18n.js'

setLang(resolveLang())
configure()
propagateQuery()
mountTopbar({ active: 'season' })
mountFooter()
document.title = t('season.title')
applyI18n()

const params = new URLSearchParams(location.search)
let seasonId = params.get('season') || ''
let timer = null

function showError(message) {
  const box = el('error')
  if (!message) {
    box.style.display = 'none'
    return
  }
  box.textContent = message
  box.style.display = ''
}

async function loadSeasonList() {
  try {
    const data = await api.seasons()
    const items = data.seasons || []
    if (!items.length) {
      litRender(html`<div class="muted">${t('season.empty')}</div>`, el('seasonList'))
      await loadSeason()
      return
    }
    if (!seasonId) seasonId = items[0].seasonId
    litRender(
      html`${items.map((item) => {
        const pct = item.fixtures ? Math.round((item.played / item.fixtures) * 100) : 0
        return html`<button class="season-item ${item.seasonId === seasonId ? 'on' : ''}" type="button" data-season="${item.seasonId}">
          <div>
            <div class="n">${item.name}</div>
            <div class="m">${t('season.list.meta', { game: gameMeta(item.game).label, fixtures: item.fixtures, played: item.played })}</div>
            <div class="progress"><i style="width:${pct}%"></i></div>
          </div>
          <span class="p">${pct}%</span>
        </button>`
      })}`,
      el('seasonList'),
    )
    for (const button of el('seasonList').querySelectorAll('[data-season]')) {
      button.onclick = () => {
        seasonId = button.dataset.season
        const url = new URL(location.href)
        url.searchParams.set('season', seasonId)
        history.replaceState({}, '', url)
        loadSeasonList()
      }
    }
    await loadSeason()
  } catch (error) {
    showError(t('season.loadFailed', { error: String(error.message || error) }))
  }
}

function renderNoSeason() {
  // 没有赛季时必须把两块面板置成明确的空状态：
  // 旧实现直接 return，页面永远停在 HTML 里的「加载中…」。
  el('subtitle').textContent = t('season.noSeasonTitle')
  el('title').textContent = t('season.heading')
  el('btnRun').disabled = true
  litRender(html`<div class="muted">${t('season.standings.empty')}</div>`, el('standings'))
  litRender(html`<div class="muted">${t('season.fixtures.empty')}</div>`, el('fixtures'))
  litRender(html`<div class="muted">${t('season.rivalries.empty')}</div>`, el('rivalries'))
  el('nextUp').style.display = 'none'
  el('kpiGame').textContent = '—'
  el('kpiPlayers').textContent = '0'
  el('kpiPlayed').textContent = '0 / 0'
  el('kpiLeader').textContent = '—'
}

async function loadSeason() {
  if (!seasonId) {
    renderNoSeason()
    return
  }
  try {
    const data = await api.season(seasonId)
    el('title').textContent = data.name || seasonId
    const played = data.fixtures.filter((f) => f.played).length
    el('subtitle').textContent = t('season.subtitle', {
      game: gameMeta(data.game).label,
      players: data.standings.length,
      fixtures: data.fixtures.length,
      played,
    })
    el('btnRun').disabled = played >= data.fixtures.length

    const leader = data.standings[0]
    el('kpiGame').textContent = gameMeta(data.game).label
    el('kpiPlayers').textContent = String(data.standings.length)
    el('kpiPlayed').textContent = `${played} / ${data.fixtures.length}`
    el('kpiLeader').textContent = leader ? (leader.display || leader.playerId) : '—'
    el('kpiLeader').title = leader ? t('season.ratingTitle', { rating: Number(leader.rating).toFixed(1) }) : ''

    const maxRating = Math.max(1, ...data.standings.map((row) => row.rating))
    litRender(
      html`${data.standings.map(
        (row, index) => html`<div class="rank">
          <span class="no ${index === 0 ? 'top' : ''}">${index + 1}</span>
          <span class="nm" title="${row.playerId}">${row.display || row.playerId}</span>
          <span class="bar"><i style="width:${Math.max(4, (row.rating / maxRating) * 100)}%"></i></span>
          <span class="w">${row.wins}</span>
          <span class="l">${row.losses}</span>
          <span class="r">${Number(row.rating).toFixed(1)}</span>
        </div>`,
      )}`,
      el('standings'),
    )

    litRender(
      data.rivalries.length
        ? html`${data.rivalries.map(
            (r) => html`<div class="rival"><span>${r.a} vs ${r.b}</span>
              <span class="score">${t('season.rivalry.meta', { aWins: r.aWins, bWins: r.bWins, games: r.games })}</span></div>`,
          )}`
        : html`<div class="muted">${t('season.rivalries.empty')}</div>`,
      el('rivalries'),
    )

    // 已结束的场次把胜者标出来：光看"已结束"读不出谁赢了。
    const winners = new Map()
    for (const row of data.results || []) {
      winners.set(`${row.playerA}|${row.playerB}`, row.winner)
      winners.set(`${row.playerB}|${row.playerA}`, row.winner)
    }
    litRender(
      data.fixtures.length
        ? html`${data.fixtures.map((f) => {
            const winner = f.played ? winners.get(`${f.a}|${f.b}`) : null
            return html`<div class="fcard ${f.played ? 'played' : 'todo'}">
              <span class="rd">${t('season.round', { n: f.round })}</span>
              <span class="vs">${winner === f.a ? html`<b>${f.a}</b>` : f.a} vs ${winner === f.b ? html`<b>${f.b}</b>` : f.b}</span>
              <span class="when">${f.played
                ? (winner ? t('season.fixture.win', { winner }) : t('season.fixture.draw'))
                : f.scheduledAt ? formatTime(new Date(f.scheduledAt * 1000).toISOString()) : t('season.fixture.pending')}</span>
            </div>`
          })}`
        : html`<div class="muted">${t('season.fixtures.none')}</div>`,
      el('fixtures'),
    )
    renderNextUp(data.fixtures)
    showError('')
  } catch (error) {
    if (String(error.message || '').startsWith('404')) {
      seasonId = ''
      renderNoSeason()
      el('subtitle').textContent = t('season.notFound')
      return
    }
    showError(t('recap.loadFailed', { error: String(error.message || error) }))
  }
}

function countdownText(unixSeconds) {
  const diff = Math.round(unixSeconds - Date.now() / 1000)
  if (diff <= 0) return t('season.countdown.due')
  const hours = Math.floor(diff / 3600)
  const minutes = Math.ceil((diff % 3600) / 60)
  return hours > 0
    ? t('season.countdown.hours', { hours, minutes })
    : t('season.countdown.minutes', { minutes })
}

let nextUpAt = null
let nextUpSeq = 0

function renderNextUp(fixtures) {
  const upcoming = fixtures.filter((f) => f.scheduledAt && !f.played).sort((a, b) => a.scheduledAt - b.scheduledAt)
  const next = upcoming[0]
  clearInterval(timer)
  nextUpSeq += 1
  if (!next) {
    el('nextUp').style.display = 'none'
    return
  }
  const at = next.scheduledAt
  const seq = nextUpSeq
  nextUpAt = at
  el('nextUp').style.display = ''
  const paint = () => {
    // 快速切换赛季时，旧闭包可能在 clearInterval 前已经入队；用序号挡住。
    if (seq !== nextUpSeq) return
    const when = countdownText(nextUpAt)
    el('nextUpText').textContent = `${formatTime(new Date(at * 1000).toISOString())} · ${next.a} vs ${next.b} · ${
      when === t('season.countdown.due') ? when : t('season.countdown.left', { when })
    }`
  }
  paint()
  timer = setInterval(paint, 1000)
}

async function runSeason() {
  if (!seasonId) return
  const button = el('btnRun')
  button.disabled = true
  button.textContent = t('season.advancing')
  try {
    const result = await api.runSeason(seasonId)
    toast(result.played ? t('season.runDone', { count: result.played }) : t('season.runNothing'))
    await loadSeasonList()
  } catch (error) {
    showError(t('season.runFailed', { error: String(error.message || error) }))
  } finally {
    button.textContent = t('season.advance')
    button.disabled = false
  }
}

// ---- 新建赛季 ----
async function openSeasonModal() {
  try {
    const data = await api.games()
    const playable = (data.games || []).filter((game) => game.players[0] === 2 && game.players[1] === 2)
    litRender(
      html`${playable.map((game) => html`<option value="${game.gameId}">${gameMeta(game.gameId, game.label).label}</option>`)}`,
      el('sGame'),
    )
  } catch {
    litRender(html`<option value="twenty-questions">${gameMeta('twenty-questions').label}</option>`, el('sGame'))
  }
  const now = new Date()
  el('sName').value = t('season.defaultName', { year: now.getFullYear() })
  el('sId').value = `season-${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  el('sPlayers').value = (getLang() === 'zh'
    ? ['甲', '乙', '丙', '丁']
    : ['Alpha', 'Beta', 'Gamma', 'Delta']).join('\n')
  el('seasonModal').classList.add('on')
  releaseSeasonFocus = trapFocus(el('seasonModal').querySelector('.sheet'))
}

let releaseSeasonFocus = null

function closeSeasonModal() {
  el('seasonModal').classList.remove('on')
  releaseSeasonFocus?.()
  releaseSeasonFocus = null
}

async function createSeason() {
  const name = el('sName').value.trim()
  const id = el('sId').value.trim()
  const game = el('sGame').value
  const players = el('sPlayers').value.split('\n').map((line) => line.trim()).filter(Boolean)
  if (!name || !id) return toast(t('season.create.err.id'), 'bad')
  if (players.length < 2) return toast(t('season.create.err.players'), 'bad')
  if (new Set(players).size !== players.length) return toast(t('season.create.err.unique'), 'bad')

  const body = {
    seasonId: id,
    name,
    game,
    players: players.map((player) => ({ id: player })),
    intervalMinutes: Number(el('sInterval').value || 30),
    doubleRoundRobin: Boolean(el('sDouble')?.checked),
  }
  const start = el('sStart').value
  if (start) body.startAt = new Date(start).toISOString()

  const button = el('seasonCreate')
  button.disabled = true
  try {
    await api.createSeason(body)
    closeSeasonModal()
    seasonId = id
    const url = new URL(location.href)
    url.searchParams.set('season', id)
    history.replaceState({}, '', url)
    await loadSeasonList()
    toast(t('season.create.created'))
  } catch (error) {
    toast(String(error.message || error), 'bad')
  } finally {
    button.disabled = false
  }
}

el('btnRun').onclick = runSeason
el('btnNewSeason').onclick = openSeasonModal
el('seasonClose').onclick = closeSeasonModal
el('seasonCancel').onclick = closeSeasonModal
el('seasonCreate').onclick = createSeason
el('seasonModal').addEventListener('click', (event) => {
  if (event.target === el('seasonModal')) closeSeasonModal()
})
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeSeasonModal()
})

loadSeasonList()
setInterval(() => {
  if (!document.hidden && seasonId) loadSeason()
}, 15000)
