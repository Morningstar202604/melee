// 页面级小交互的唯一实现：分享、错误条。三个页面各抄一遍的代码收在这里。
import { configure, el, propagateQuery, shareUrl, toast } from './api.js'
import { applyI18n, resolveLang, setLang, t } from './i18n.js'
import { mountFooter, mountTopbar } from './nav.js'

export async function shareLink(matchId) {
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

export function showError(message) {
  const box = el('error')
  if (!message) {
    box.style.display = 'none'
    return
  }
  box.textContent = message
  box.style.display = ''
}

export function bootstrapPage({ active, titleKey, footer = true, beforeFooter = null }) {
  setLang(resolveLang())
  configure()
  propagateQuery()
  mountTopbar({ active })
  if (beforeFooter) beforeFooter()
  if (footer) mountFooter()
  document.title = t(titleKey)
  applyI18n()
}
