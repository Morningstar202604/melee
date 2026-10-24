// 赛季页：积分榜、赛程、宿敌、推进赛程、新建赛季。所有数据来自 /seasons 与 /seasons/{id}。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, configure, el, propagateQuery, toast, gameMeta, formatTime } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'

configure()
propagateQuery()
mountTopbar({ active: 'season' })
mountFooter()

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
      litRender(html`<div class="muted">还没有赛季。点「新建赛季」创建第一个。</div>`, el('seasonList'))
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
            <div class="m">${gameMeta(item.game).label} · ${item.fixtures} 场 · 已跑 ${item.played}</div>
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
    showError(`赛季列表加载失败：${String(error.message || error)}`)
  }
}

async function loadSeason() {
  if (!seasonId) {
    el('subtitle').textContent = '还没有赛季'
    return
  }
  try {
    const data = await api.season(seasonId)
    el('title').textContent = data.name || seasonId
    const played = data.fixtures.filter((f) => f.played).length
    el('subtitle').textContent = `${gameMeta(data.game).label} · ${data.standings.length} 位选手 · ${data.fixtures.length} 场赛程 · 已完成 ${played}`
    el('btnRun').disabled = played >= data.fixtures.length

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
              <span class="score">${r.aWins}-${r.bWins} · ${r.games} 场</span></div>`,
          )}`
        : html`<div class="muted">暂无（需交手 2 场以上）</div>`,
      el('rivalries'),
    )

    litRender(
      data.fixtures.length
        ? html`${data.fixtures.map(
            (f) => html`<div class="fixture ${f.played ? 'played' : ''}">
              <span class="rd">R${f.round}</span>
              <span class="vs">${f.a} vs ${f.b}</span>
              <span class="when">${f.played ? '已结束' : f.scheduledAt ? formatTime(new Date(f.scheduledAt * 1000).toISOString()) : '待开赛'}</span>
            </div>`,
          )}`
        : html`<div class="muted">没有赛程</div>`,
      el('fixtures'),
    )
    renderNextUp(data.fixtures)
    showError('')
  } catch (error) {
    if (String(error.message || '').startsWith('404')) {
      seasonId = ''
      el('subtitle').textContent = '赛季不存在，已切换到列表'
      return
    }
    showError(`加载失败：${String(error.message || error)}`)
  }
}

function countdownText(unixSeconds) {
  const diff = Math.round(unixSeconds - Date.now() / 1000)
  if (diff <= 0) return '已到开赛时间'
  const hours = Math.floor(diff / 3600)
  const minutes = Math.ceil((diff % 3600) / 60)
  return hours > 0 ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`
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
      when === '已到开赛时间' ? when : '还有 ' + when
    }`
  }
  paint()
  timer = setInterval(paint, 1000)
}

async function runSeason() {
  if (!seasonId) return
  const button = el('btnRun')
  button.disabled = true
  button.textContent = '推进中…'
  try {
    const result = await api.runSeason(seasonId)
    toast(result.played ? `完成了 ${result.played} 场` : '没有到点或未完成的场次')
    await loadSeasonList()
  } catch (error) {
    showError(`推进失败：${String(error.message || error)}`)
  } finally {
    button.textContent = '推进赛程'
    button.disabled = false
  }
}

// ---- 新建赛季 ----
async function openSeasonModal() {
  try {
    const data = await api.games()
    const playable = (data.games || []).filter((game) => game.players[0] === 2 && game.players[1] === 2)
    litRender(
      html`${playable.map((game) => html`<option value="${game.gameId}">${game.label}</option>`)}`,
      el('sGame'),
    )
  } catch {
    litRender(html`<option value="twenty-questions">二十问</option>`, el('sGame'))
  }
  const now = new Date()
  el('sName').value = `${now.getFullYear()} 赛季`
  el('sId').value = `season-${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
  el('sPlayers').value = ['甲', '乙', '丙', '丁'].join('\n')
  el('seasonModal').classList.add('on')
  el('sName').focus()
}

async function createSeason() {
  const name = el('sName').value.trim()
  const id = el('sId').value.trim()
  const game = el('sGame').value
  const players = el('sPlayers').value.split('\n').map((line) => line.trim()).filter(Boolean)
  if (!name || !id) return toast('名称与 ID 不能为空', 'bad')
  if (players.length < 2) return toast('至少两位选手', 'bad')
  if (new Set(players).size !== players.length) return toast('选手名必须唯一', 'bad')

  const body = {
    seasonId: id,
    name,
    game,
    players: players.map((player) => ({ id: player })),
    intervalMinutes: Number(el('sInterval').value || 30),
  }
  const start = el('sStart').value
  if (start) body.startAt = new Date(start).toISOString()

  const button = el('seasonCreate')
  button.disabled = true
  try {
    await api.createSeason(body)
    el('seasonModal').classList.remove('on')
    seasonId = id
    const url = new URL(location.href)
    url.searchParams.set('season', id)
    history.replaceState({}, '', url)
    await loadSeasonList()
    toast('赛季已创建')
  } catch (error) {
    toast(String(error.message || error), 'bad')
  } finally {
    button.disabled = false
  }
}

el('btnRun').onclick = runSeason
el('btnNewSeason').onclick = openSeasonModal
el('seasonClose').onclick = () => el('seasonModal').classList.remove('on')
el('seasonCancel').onclick = () => el('seasonModal').classList.remove('on')
el('seasonCreate').onclick = createSeason
el('seasonModal').addEventListener('click', (event) => {
  if (event.target === el('seasonModal')) el('seasonModal').classList.remove('on')
})
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') el('seasonModal').classList.remove('on')
})

loadSeasonList()
setInterval(() => {
  if (!document.hidden && seasonId) loadSeason()
}, 15000)
