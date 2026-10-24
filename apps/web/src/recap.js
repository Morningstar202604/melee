// 战报页：一句话结论 + 可对账的数字 + 转折点 + 选手数据 + 高光视频。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, apiBase, configure, el, propagateQuery, toast, gameMeta, winnerLabel } from './api.js'
import { clipUrl, fetchMediaTicket } from './sse.js'
import { mountTopbar, mountFooter } from './nav.js'

configure()
propagateQuery()
mountTopbar({ active: 'recap' })
mountFooter()

const params = new URLSearchParams(location.search)
const matchId = params.get('match') || ''

function showError(message) {
  const box = el('error')
  if (!message) {
    box.style.display = 'none'
    return
  }
  box.textContent = message
  box.style.display = ''
}

async function load() {
  if (!matchId) {
    el('winner').textContent = '缺少比赛'
    el('oneLine').textContent = '用法：recap.html?match=<matchId>'
    return
  }
  el('matchTag').textContent = matchId
  el('btnWatch').href = `watch.html?match=${encodeURIComponent(matchId)}`
  const shareOrigin = apiBase() ? new URL(apiBase(), location.href).origin : ''
  el('btnSharePage').href = `${shareOrigin}/share/${encodeURIComponent(matchId)}`
  try {
    const data = await api.recap(matchId)
    const meta = gameMeta(data.game)
    el('gameTag').textContent = meta.label
    el('kick').textContent = `${meta.label} · 已结束`
    const winner = winnerLabel(data.winner)
    el('winner').textContent = winner ? `${winner} 胜` : '无人获胜'
    el('oneLine').textContent = data.oneLine

    const isQuiz = data.game === 'twenty-questions'
    const stats = isQuiz
      ? [
          ['回合', data.turns],
          ['提问', data.asks],
          ['报答案', data.guesses],
          ['犯规', data.rejected],
        ]
      : [
          ['步数', data.moves],
          ['犯规', data.rejected],
          ['转折点', data.highlights.length],
          ['选手', Object.keys(data.perPlayer || {}).length],
        ]
    litRender(
      html`${stats.map(([label, value]) => html`<div class="stat"><b>${value}</b><span>${label}</span></div>`)}`,
      el('stats'),
    )

    litRender(
      data.highlights.length
        ? html`${data.highlights.map(
            (h) => html`<div class="hlc ${h.weight >= 80 ? 'hot' : ''}">
              <span class="w">${h.weight}</span>
              <div class="k">${h.kind} · seq ${h.seq}</div>
              <div class="t">${h.title}</div>
              <div class="d">${h.detail}</div>
            </div>`,
          )}`
        : html`<div class="muted">这场比赛没有明显转折点。</div>`,
      el('highlights'),
    )

    const perPlayer = data.perPlayer || {}
    litRender(
      Object.keys(perPlayer).length
        ? html`${Object.entries(perPlayer).map(
            ([player, row]) => html`<div class="pcard">
              <div class="n" style="margin-bottom:8px">${player}</div>
              ${Object.entries(row).map(
                ([key, value]) => html`<div class="row"><span>${labelOf(key)}</span><b>${value}</b></div>`,
              )}
            </div>`,
          )}`
        : html`<div class="muted">没有选手级数据</div>`,
      el('players'),
    )

    const box = el('verify')
    box.hidden = false
    if (data.problems && data.problems.length) {
      box.className = 'verify bad'
      box.textContent = `事实校验未通过：${data.problems.join('；')}`
    } else {
      box.className = 'verify'
      box.textContent = '事实校验通过：战报里的每个数字都能在事件流里数出来。'
    }
    showError('')
  } catch (error) {
    showError(`加载失败：${String(error.message || error)}`)
    el('winner').textContent = '加载失败'
  }
}

function labelOf(key) {
  return {
    asks: '提问',
    guesses: '报答案',
    rejected: '犯规',
    thoughts: '思考片段',
    moves: '步数',
  }[key] || key
}

async function renderClips() {
  if (!matchId) return
  const button = el('render')
  button.disabled = true
  button.textContent = '渲染中…'
  litRender(html`<div class="muted">渲染中…（每段约几秒）</div>`, el('clips'))
  try {
    const result = await api.renderClips(matchId, { limit: 3, perEventMs: 900 })
    const ticket = await fetchMediaTicket(matchId, { baseUrl: apiBase() })
    litRender(
      result.clips.length
        ? html`${result.clips.map(
            (clip) => html`<div class="clip">
              <video controls preload="none" src="${clipUrl(matchId, clip.file, apiBase(), ticket)}"></video>
              <div class="m">${clip.title} · ${(clip.durationMs / 1000).toFixed(1)}s · ${clip.frames} 帧</div>
            </div>`,
          )}`
        : html`<div class="muted">没有可剪的高光。</div>`,
      el('clips'),
    )
  } catch (error) {
    litRender(html`<div class="notice warn">${String(error.message || error)}</div>`, el('clips'))
  } finally {
    button.disabled = false
    button.textContent = '渲染'
  }
}

el('render').onclick = renderClips
el('btnShare').onclick = async () => {
  const origin = apiBase() ? new URL(apiBase(), location.href).origin : location.origin
  const url = `${origin}/share/${matchId}`
  try {
    if (navigator.share) {
      await navigator.share({ title: 'melee 战报', url })
      return
    }
    await navigator.clipboard.writeText(url)
    toast('分享链接已复制')
  } catch {
    toast('复制失败：' + url, 'bad')
  }
}

load()
