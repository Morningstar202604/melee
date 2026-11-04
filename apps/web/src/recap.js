// 战报页：一句话结论 + 可对账的数字 + 转折点 + 选手数据 + 高光视频。
import { html } from '../vendor/lit-html.js'
import { paint as litRender } from './paint.js'
import { api, clipUrl, configure, el, gameMeta, propagateQuery, shareUrl, toast, winnerLabel } from './api.js'
import { mountTopbar, mountFooter } from './nav.js'
import { applyI18n, resolveLang, setLang, t } from './i18n.js'

setLang(resolveLang())
configure()
propagateQuery()
mountTopbar({ active: 'recap' })
mountFooter()
document.title = t('recap.title')
applyI18n()

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
    el('winner').textContent = t('recap.missing')
    el('oneLine').textContent = t('recap.usage')
    return
  }
  el('matchTag').textContent = matchId
  el('btnWatch').href = `watch.html?match=${encodeURIComponent(matchId)}`
  el('btnSharePage').href = shareUrl(matchId)
  try {
    const data = await api.recap(matchId)
    const meta = gameMeta(data.game)
    el('gameTag').textContent = meta.label
    el('kick').textContent = t('recap.ended', { game: meta.label })
    const winner = winnerLabel(data.winner)
    el('winner').textContent = winner ? t('recap.win', { winner }) : t('recap.noWinner')
    el('oneLine').textContent = data.oneLine

    const isQuiz = data.game === 'twenty-questions'
    const stats = isQuiz
      ? [
          [t('recap.turns'), data.turns],
          [t('recap.asks'), data.asks],
          [t('recap.guesses'), data.guesses],
          [t('recap.fouls'), data.rejected],
        ]
      : [
          [t('recap.moves'), data.moves],
          [t('recap.fouls'), data.rejected],
          [t('recap.highlights'), data.highlights.length],
          [t('recap.players'), Object.keys(data.perPlayer || {}).length],
        ]
    litRender(
      html`${stats.map(([label, value]) => html`<div class="stat"><b>${value}</b><span>${label}</span></div>`)}`,
      el('stats'),
    )

    // 逐手耗时：只在真的量到数据时出现。脚本选手没有耗时，
    // 显示一排 0 比不显示更误导。
    const timing = data.timing || {}
    const timingBox = el('timing')
    if (timing.elapsedMsTotal > 0) {
      const slow = timing.slowestTurn
      litRender(
        html`<span><b>${(timing.elapsedMsTotal / 1000).toFixed(1)}s</b>${t('recap.thinkingTotal')}</span>
          <span><b>${Math.round(timing.elapsedMsMax)}ms</b>${t('recap.slowestTurn')}</span>
          ${timing.tokensTotal > 0 ? html`<span><b>${timing.tokensTotal}</b>${t('recap.tokens')}</span>` : html``}
          ${slow
            ? html`<span class="slow">${t('recap.slowest', {
                player: slow.player || '—',
                action: slow.action?.kind || '',
                value: slow.action?.value || '',
                seq: slow.seq,
              })}</span>`
            : html``}`,
        timingBox,
      )
      timingBox.hidden = false
    } else {
      timingBox.hidden = true
    }

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
        : html`<div class="muted">${t('recap.noHighlights')}</div>`,
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
        : html`<div class="muted">${t('recap.noPlayerData')}</div>`,
      el('players'),
    )

    const box = el('verify')
    box.hidden = false
    if (data.problems && data.problems.length) {
      box.className = 'verify bad'
      box.textContent = t('recap.verifyBad', { problems: data.problems.join('; ') })
    } else {
      box.className = 'verify'
      box.textContent = t('recap.verifyOk')
    }
    showError('')
  } catch (error) {
    showError(t('recap.loadFailed', { error: String(error.message || error) }))
    el('winner').textContent = t('watch.conn.failed')
  }
}

// 键由后端按赛制决定：二十问有 asks/guesses，代号与棋类只有 moves。
// 这里只做翻译，不补不存在的行——否则代号局会显示"提问 0 / 报答案 10"。
const PER_PLAYER_KEYS = {
  asks: 'recap.asks',
  guesses: 'recap.guesses',
  rejected: 'recap.fouls',
  thoughts: 'recap.thoughts',
  moves: 'recap.moves',
}

function labelOf(key) {
  const dictKey = PER_PLAYER_KEYS[key]
  return dictKey ? t(dictKey) : key
}

async function renderClips() {
  if (!matchId) return
  const button = el('render')
  button.disabled = true
  button.textContent = t('recap.rendering')
  litRender(html`<div class="muted">${t('recap.rendering')}</div>`, el('clips'))
  try {
    const template = el('template')?.value || 'broadcast'
    const result = await api.renderClips(matchId, { limit: 3, perEventMs: 900, template })
    let ticket = ''
    try {
      ticket = (await api.mediaTicket(matchId)).ticket || ''
    } catch {
      ticket = ''
    }
    litRender(
      result.clips.length
        ? html`${result.clips.map(
            (clip) => html`<div class="clip">
              <video controls preload="none" src="${clipUrl(matchId, clip.file, ticket)}"></video>
              <div class="m">${clip.title} · ${(clip.durationMs / 1000).toFixed(1)}s · ${clip.template || template}</div>
            </div>`,
          )}`
        : html`<div class="muted">${t('recap.clipsEmpty')}</div>`,
      el('clips'),
    )
  } catch (error) {
    litRender(html`<div class="notice warn">${String(error.message || error)}</div>`, el('clips'))
  } finally {
    button.disabled = false
    button.textContent = t('recap.render')
  }
}

async function loadClipPreview() {
  // 渲染前先告诉用户"会得到什么"：拿剪辑清单（不渲染）画成卡片。
  // 旧实现这里只有一句"点渲染"，用户不知道有几段、多长。
  if (!matchId) return
  const box = el('clips')
  try {
    const manifest = await api.clips(matchId)
    if (!manifest.clipCount) {
      litRender(html`<div class="muted">${t('recap.clipsEmpty')}</div>`, box)
      el('render').disabled = true
      return
    }
    litRender(
      html`${manifest.clips.map(
        (clip) => html`<div class="clip-preview">
          <div class="cp-k">${clip.kind}</div>
          <div class="cp-t">${clip.title}</div>
          <div class="cp-d">${clip.detail}</div>
          <div class="cp-m">seq ${clip.startSeq}–${clip.endSeq} · ${t('recap.clipMeta', {
            count: clip.eventCount,
            seconds: (clip.durationMs / 1000).toFixed(1),
          })}</div>
        </div>`,
      )}`,
      box,
    )
    el('renderHint').textContent = t('recap.clipPending', {
      count: manifest.clipCount,
      seconds: (manifest.totalMs / 1000).toFixed(1),
    })
  } catch (error) {
    litRender(html`<div class="muted">${t('recap.clipManifestFailed', { error: String(error.message || error) })}</div>`, box)
  }
}

el('render').onclick = renderClips
loadClipPreview()
el('btnShare').onclick = async () => {
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

load()
