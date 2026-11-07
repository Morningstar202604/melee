// 观战页：把"取数 / 播放 / 渲染 / 交互"四块接起来。
// 具体实现分别在 api.js、sse.js、watch/player.js、watch/render.js，
// 这里只保留页面级状态与事件接线。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import {
  api, apiBase, clipUrl, configure, el, gameMeta, propagateQuery, shareUrl, shotLabel, teamLabel, toast,
} from './api.js'
import { connectEvents } from './sse.js'
import { createView, reduce, replayView } from './view.js'
import {
  renderArena, renderSides, renderStatus, renderTimeline, currentCue, onSeek, resetTimelineCache,
} from './watch/render.js'
import { createPlayer } from './watch/player.js'
import { buildTimeline, cursorAt, elapsedAt, formatClock, marksOf, ratioAt } from './watch/timeline.js'
import { trapFocus } from './a11y.js'
import { applyI18n, getLang, resolveLang, setLang, t } from './i18n.js'
import { mountLangSwitch } from './nav.js'
import { mountThemeSwitch } from './theme.js'

setLang(resolveLang())
document.title = t('watch.title')
applyI18n()
// 观战页顶栏是静态 HTML（首帧就要有舞台），语言与主题按钮手动挂进去。
if (el('themeSwitch')) mountThemeSwitch(el('themeSwitch'))
if (el('langSwitch')) mountLangSwitch(el('langSwitch'))

const params = new URLSearchParams(location.search)
configure()
propagateQuery()
const API = apiBase()
const MATCH_KEY = 'melee.currentMatch'

let view = createView()
let events = []
// 时间轴索引只在事件流变化时重建：每帧都算一次会白白扫一遍全量事件。
let timeline = buildTimeline([])
let currentMatchId = null
let presenceTimer = null
let endTimer = null
let closeStream = null
let attachToken = 0
let releaseEndFocus = null

const player = createPlayer({
  getEvents: () => events,
  render: () => render(),
})

const shownState = () => (player.state.mode === 'live' ? view : replayView(events, player.state.cursor))

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
  const shown = shownState()
  const lastLine = shown.narration.length ? shown.narration[shown.narration.length - 1].text : ''
  el('narration').textContent = lastLine || t('watch.waitingFirst')
  el('headline').textContent = lastLine || t('watch.waitingFirst')
  const total = Math.max(shown.maxQuestions, 1)
  const remain = shown.remaining ?? total
  el('bar').style.width = `${Math.max(0, Math.min(100, (remain / total) * 100))}%`

  const cue = currentCue(shown)
  el('cue').textContent = cue ? `${shotLabel(cue.shot)} · ${cue.trigger}` : t('watch.noCue')

  const idle = !shown.players || !shown.players.length
  el('stage').classList.toggle('idle', idle)
  renderSides(shown)
  if (!idle) renderArena(shown)
  renderTimeline(events, player.state.mode, player.state.cursor, timeline)
  renderStatus(shown)

  const hasMatch = Boolean(currentMatchId)
  el('liveLabel').textContent = !hasMatch
    ? t('watch.conn.idle')
    : shown.status === 'ended'
      ? t('watch.conn.ended')
      : player.state.mode === 'replay'
        ? t('watch.replayBadge')
        : 'LIVE'
  el('liveLabel').className = !hasMatch ? 'ended' : shown.status === 'ended' ? 'ended' : player.state.mode === 'replay' ? 'replay' : ''
  el('liveDot').style.display = hasMatch && shown.status !== 'ended' ? '' : 'none'

  if (shown.status === 'ended' && !player.state.dismissedEnd && player.state.mode === 'live') {
    if (!endTimer) {
      endTimer = setTimeout(() => {
        el('winner').textContent = teamLabel(shown.winner) || t('watch.ended.noWinner')
        el('endreason').textContent = String(shown.endReason || '')
        el('endcard').classList.add('on')
        releaseEndFocus = trapFocus(el('endcard'))
      }, 1200)
    }
  } else if (shown.status !== 'ended' || player.state.dismissedEnd || player.state.mode !== 'live') {
    clearTimeout(endTimer)
    endTimer = null
    if (el('endcard').classList.contains('on')) {
      el('endcard').classList.remove('on')
      releaseEndFocus?.()
      releaseEndFocus = null
    }
  }
  el('btnPause').textContent = player.state.playing ? t('watch.pause') : t('watch.resume')
}

function appendEvent(event) {
  // 快照已经拉过一遍全量，SSE 又从 seq 0 推一遍；不按 seq 去重就会翻倍。
  if (events.length && event.seq <= events[events.length - 1].seq) return

  events = events.concat(event)
  timeline = buildTimeline(events)
  if (player.state.mode === 'live') {
    view = reduce(view, event)
    player.state.cursor = events.length
    render()
  }
}

async function attach(matchId) {
  // 切台可能并发：后发起的请求必须先完成才算数，否则慢的旧请求会覆盖新画面。
  const token = ++attachToken
  localStorage.setItem(MATCH_KEY, matchId)
  currentMatchId = matchId
  el('matchId').textContent = matchId
  el('btnRecapEnd').href = `recap.html?match=${encodeURIComponent(matchId)}`
  closeStream?.()
  closeStream = null

  let snapshot
  try {
    snapshot = await api.events(matchId, -1)
  } catch (error) {
    if (token === attachToken) {
      el('conn').textContent = t('watch.conn.failed')
      el('conn').className = 'conn bad'
      currentMatchId = null
      events = []
      view = createView()
      player.attach(0)
      el('stage').classList.add('idle')
      el('matchId').textContent = '—'
      el('connectError').textContent = t('watch.loadFailed.detail', { error: String(error.message || error) })
      litRender(
        html`<div class="card" style="--tone:var(--bad);align-items:center;justify-content:center;text-align:center">
          <div class="pname" style="font-size:15px;color:var(--bad)">${t('watch.loadFailed.title')}</div>
          <div class="muted" style="margin-top:8px;line-height:1.7">${String(error.message || error)}</div>
          <div style="display:flex;gap:8px;margin-top:14px">
            <a class="btn" href="index.html">${t('watch.idle.cta')}</a>
            <button class="btn" type="button" id="retryLoad">${t('watch.loadFailed.retry')}</button>
          </div>
        </div>`,
        el('sideLeft'),
      )
      litRender(html``, el('sideRight'))
      el('retryLoad').onclick = () => attach(matchId).catch(() => {})
      el('liveLabel').textContent = t('watch.conn.failed')
      el('liveLabel').className = 'ended'
    }
    throw error
  }
  if (token !== attachToken) return
  events = snapshot.events
  timeline = buildTimeline(events)
  resetTimelineCache()
  view = replayView(events)
  player.attach(events.length)
  // 切台必须回到直播态：上一场的"暂停/回放"状态带过来会让新场永远停在回放。
  el('btnPause').textContent = t('watch.pause')
  render()

  if (view.status === 'ended') {
    // 已结束的比赛不需要长连接：接上 SSE 只会立刻被服务端关闭，然后无限重连。
    el('conn').textContent = t('watch.conn.ended')
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
        el('conn').textContent = t('watch.conn.ended')
        el('conn').className = 'conn'
      }
    },
    onError: () => {
      if (view.status === 'ended') return
      el('conn').textContent = t('watch.conn.reconnecting')
      el('conn').className = 'conn warn'
    },
    onOpen: () => {
      el('conn').textContent = t('watch.conn.connected')
      el('conn').className = 'conn ok'
    },
  })
  startPresence(matchId)
}

function startPresence(matchId) {
  clearInterval(presenceTimer)
  // 空状态曾把整块人数藏起来，接入比赛后没有恢复，导致人数永远不可见。
  el('viewers').style.display = ''
  const poll = async () => {
    try {
      const data = await api.presence(matchId)
      el('viewerCount').textContent = String(data.viewers ?? 0)
      el('viewers').title = data.scope === 'global'
        ? t('watch.viewersGlobal')
        : t('watch.viewersLocal')
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
  el('conn').textContent = t('watch.conn.idle')
  el('conn').className = 'conn'
  el('viewers').style.display = 'none'
  render()
}

// --- 交互 ---

// 时间轴上的橙点：点一下跳到那一刻。render.js 不碰播放状态，
// 这里把 seek 注入回去，保持"谁持有状态谁改状态"。
onSeek((cursor) => player.seek(cursor))

el('scrub').addEventListener('input', (e) => {
  // 拖动条按时间比例给值，先换算回最近的游标再交给播放器。
  const ratio = Number(e.target.value) / Math.max(1, Number(e.target.max))
  player.seek(cursorAt(timeline, ratio))
})
el('btnPause').addEventListener('click', () => player.pause())
el('btnLive').addEventListener('click', () => {
  player.backToLive()
  view = replayView(events)
})
el('endcard').addEventListener('click', (event) => {
  if (event.target === el('endcard')) closeEndCard()
})
// 键盘与读屏用户必须有明确的退出路径：之前只能点遮罩，
// 焦点被 trapFocus 关在弹层里，Tab 也走不出去——等于锁死。
el('btnEndClose').addEventListener('click', closeEndCard)

function closeEndCard() {
  player.dismissEnd()
  el('endcard').classList.remove('on')
  releaseEndFocus?.()
  releaseEndFocus = null
}
el('btnReplay').addEventListener('click', () => {
  player.replayFromStart()
  el('endcard').classList.remove('on')
})
el('btnReplayEnd').addEventListener('click', () => {
  player.replayFromStart()
  el('endcard').classList.remove('on')
})
el('speed').addEventListener('change', (e) => player.setSpeed(e.target.value))
el('btnRender').addEventListener('click', exportHighlights)
el('btnRenderEnd').addEventListener('click', exportHighlights)
el('clipClose').addEventListener('click', () => el('clipPanel').classList.remove('on'))
el('btnTheater').addEventListener('click', () => toggleTheater())
el('theaterExit').addEventListener('click', () => toggleTheater(false))

document.addEventListener('keydown', (event) => {
  const target = event.target
  const tag = (target && target.tagName) || ''

  // Escape 必须最先处理，且不受"焦点在按钮/输入框"影响：
  // 模态弹层把焦点关在里面，用户按 Esc 时焦点几乎总在按钮上，
  // 放在后面的分支里就永远走不到——键盘用户会被锁死在弹窗内。
  if (event.key === 'Escape') {
    if (el('endcard').classList.contains('on')) {
      closeEndCard()
      return
    }
    if (el('clipPanel').classList.contains('on')) {
      el('clipPanel').classList.remove('on')
      return
    }
    if (document.body.classList.contains('theater')) {
      toggleTheater(false)
      return
    }
  }

  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return
  // 空格在按钮/链接上是浏览器原生的"激活"，不能被播放器抢走。
  if (target && (target.tagName === 'BUTTON' || target.tagName === 'A' || target.isContentEditable)) return
  if (event.code === 'Space') {
    event.preventDefault()
    el('btnPause').click()
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    player.step(-1)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    player.step(1)
  } else if (event.key === 'l' || event.key === 'L') {
    el('btnLive').click()
  } else if (event.key === 'f' || event.key === 'F') {
    toggleTheater()
  }
})

function toggleTheater(force) {
  const next = typeof force === 'boolean' ? force : !document.body.classList.contains('theater')
  document.body.classList.toggle('theater', next)
  el('theaterExit').style.display = next ? 'flex' : 'none'
  el('btnTheater').textContent = next ? t('watch.exitTheater') : t('watch.theater')
  if (next) el('theaterExit').focus()
  requestAnimationFrame(fitStage)
}

async function shareMatch() {
  if (!currentMatchId) return
  const url = shareUrl(currentMatchId)
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

el('btnShare').addEventListener('click', shareMatch)

async function exportHighlights() {
  const matchId = currentMatchId
  if (!matchId) return

  const panel = el('clipPanel')
  const body = el('clipBody')
  panel.classList.add('on')
  litRender(html`<div class="cp-empty">${t('watch.clip.rendering')}</div>`, body)

  try {
    const result = await api.renderClips(matchId, { limit: 3, perEventMs: 900 })
    if (!result.clips.length) {
      litRender(html`<div class="cp-empty">${t('watch.clip.none')}</div>`, body)
      return
    }
    let ticket = ''
    try {
      ticket = (await api.mediaTicket(matchId)).ticket || ''
    } catch {
      ticket = ''
    }
    litRender(
      html`${result.clips.map(
        (clip) => html`<div class="clip">
          <div class="c-title">${clip.title}</div>
          <div class="c-meta">${t('watch.clip.meta', { kind: clip.kind, seconds: (clip.durationMs / 1000).toFixed(1), frames: clip.frames })}</div>
          <video controls preload="none" src="${clipUrl(matchId, clip.file, ticket)}"></video>
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
      toast(t('watch.next.none'))
      return
    }
    await attach(next.matchId)
  } catch (error) {
    toast(String(error.message || error), 'bad')
  }
}
el('btnNext').addEventListener('click', loadNextMatch)

// 赛制清单从服务端拉，避免前端写死。
let GAMES = [{ gameId: 'twenty-questions', players: [2, 2] }]

function syncRosterControls() {
  // 编队参数只对代号有意义：其他赛制是固定人数，藏起来避免误导。
  const isCodenames = (el('gamePick')?.value || 'twenty-questions') === 'codenames'
  const count = el('teamCount')
  const size = el('teamSize')
  if (count) count.hidden = !isCodenames
  if (size) size.hidden = !isCodenames
}

/** 队数/每队人数下拉：选项文案跟随语言，值保持数字。 */
function fillRosterOptions() {
  const count = el('teamCount')
  const size = el('teamSize')
  if (count) {
    const current = count.value || '2'
    count.replaceChildren(
      ...[2, 3, 4, 5, 6].map((n) => {
        const option = document.createElement('option')
        option.value = String(n)
        option.textContent = t('watch.teamsN', { n })
        return option
      }),
    )
    count.value = current
  }
  if (size) {
    const current = size.value || '2'
    size.replaceChildren(
      ...[2, 3, 4].map((n) => {
        const option = document.createElement('option')
        option.value = String(n)
        option.textContent = t('watch.teamSizeN', { n })
        return option
      }),
    )
    size.value = current
  }
}

async function loadGames() {
  try {
    const data = await api.games()
    if (Array.isArray(data.games) && data.games.length) {
      GAMES = data.games
      const picker = el('gamePick')
      litRender(
        html`${GAMES.map((g) => html`<option value="${g.gameId}">${gameMeta(g.gameId, g.label).label}</option>`)}`,
        picker,
      )
    }
  } catch {
    /* 拉不到就用内置默认 */
  } finally {
    fillRosterOptions()
    syncRosterControls()
  }
}

function playerIdsFor(gameId, teams = 2, teamSize = 2) {
  if (gameId === 'codenames') {
    // 编队决定人数：每队 1 队长 + (teamSize-1) 队员；预置色板与后端一致。
    // 名字跟随界面语言：英文界面下不该出现「红队队长」这种硬编码。
    const palettes = {
      zh: { colors: ['红', '蓝', '绿', '黄', '紫', '橙', '青', '粉'], spymaster: '队队长', operative: '队队员' },
      en: {
        colors: ['Red', 'Blue', 'Green', 'Yellow', 'Purple', 'Orange', 'Cyan', 'Pink'],
        spymaster: ' Spymaster',
        operative: ' Operative',
      },
    }
    const p = palettes[getLang() === 'zh' ? 'zh' : 'en']
    const ids = []
    for (let t = 0; t < teams; t += 1) ids.push(`${p.colors[t % p.colors.length]}${p.spymaster}`)
    for (let member = 1; member < teamSize; member += 1) {
      for (let t = 0; t < teams; t += 1) ids.push(`${p.colors[t % p.colors.length]}${p.operative}${member}`)
    }
    return ids
  }
  const spec = GAMES.find((g) => g.gameId === gameId)
  const count = spec ? spec.players[0] : 2
  return ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P'].slice(0, count)
}

el('btnNew').addEventListener('click', async () => {
  el('connectError').textContent = ''
  const gameId = el('gamePick').value || 'twenty-questions'
  // 高级编队：仅代号提供队数/每队人数（与其他赛制的固定人数不冲突）。
  const isCodenames = gameId === 'codenames'
  const teams = isCodenames ? Number(el('teamCount')?.value || 2) : 2
  const teamSize = isCodenames ? Number(el('teamSize')?.value || 2) : 2
  const ids = playerIdsFor(gameId, teams, teamSize)
  const body = {
    game: gameId,
    seed: Math.floor(Math.random() * 1_000_000),
    players: ids.map((id) => ({ id, delayMs: 1200 })),
  }
  if (gameId === 'twenty-questions') body.maxQuestions = 21
  if (isCodenames && (teams !== 2 || teamSize !== 2)) body.config = { teams, teamSize }

  try {
    const result = await api.createMatch(body)
    await attach(result.matchId)
  } catch (error) {
    el('connectError').textContent = String(error.message || error)
  }
})

el('gamePick').addEventListener('change', syncRosterControls)

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
player.start()
render()
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
