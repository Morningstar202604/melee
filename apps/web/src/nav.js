// 顶栏与页脚是五个页面唯一共享的结构：集中在这里生成，改一次全站一致。
// 用 DOM API 构建而不是 innerHTML：页面名来自常量，但仍然不给注入留口子。
import { applyI18n, getLang, resolveLang, setLang, t } from './i18n.js'
import { mountThemeSwitch } from './theme.js'

const PAGES = [
  { href: 'index.html', key: 'nav.home' },
  { href: 'watch.html', key: 'nav.watch' },
  { href: 'season.html', key: 'nav.season' },
  { href: 'recap.html', key: 'nav.recap' },
  { href: 'director.html', key: 'nav.director' },
  { href: 'admin.html', key: 'nav.admin' },
]

/** 语言切换按钮：英文↔中文，切换后整页重载以让所有静态与动态文案一起生效。
 *  观战页的顶栏是静态 HTML（为了首帧就画出舞台），没有走 mountTopbar，
 *  所以这里导出，让该页面把同一颗按钮挂到自己的容器里。 */
export function mountLangSwitch(parent) {
  const button = document.createElement('button')
  button.className = 'btn sm ghost lang-switch'
  button.type = 'button'
  // 按钮显示的是"切换到哪种语言"，不是当前语言——否则用户不知道点了会怎样。
  const target = getLang() === 'zh' ? 'en' : 'zh'
  button.textContent = target === 'en' ? 'EN' : '中文'
  button.setAttribute('aria-label', target === 'en' ? 'Switch to English' : '切换到中文')
  button.setAttribute('data-i18n-title', 'common.language')
  button.title = t('common.language')
  button.onclick = () => {
    setLang(target)
    const url = new URL(location.href)
    url.searchParams.set('lang', target)
    location.replace(url.toString())
  }
  parent.appendChild(button)
}

export function mountTopbar({ active = '', tools = null } = {}) {
  const bar = document.createElement('header')
  bar.className = 'topbar'

  const brand = document.createElement('a')
  brand.className = 'brand'
  brand.href = 'index.html'
  brand.setAttribute('data-keep-query', '')
  brand.innerHTML = `<span class="mark">M</span>${t('common.brand')} <span>/</span> ${t('common.brandSuffix')}`
  bar.appendChild(brand)

  const nav = document.createElement('nav')
  nav.className = 'nav'
  nav.setAttribute('aria-label', t('nav.main'))
  for (const page of PAGES) {
    const link = document.createElement('a')
    link.href = page.href
    link.textContent = t(page.key)
    link.setAttribute('data-keep-query', '')
    if (page.key === `nav.${active}`) {
      link.className = 'on'
      link.setAttribute('aria-current', 'page')
    }
    nav.appendChild(link)
  }
  bar.appendChild(nav)

  const spacer = document.createElement('div')
  spacer.className = 'spacer'
  bar.appendChild(spacer)

  if (tools) {
    const box = document.createElement('div')
    box.className = 'tools'
    box.id = 'topbarTools'
    bar.appendChild(box)
    if (typeof tools === 'function') tools(box)
  }

  const langBox = document.createElement('div')
  langBox.className = 'tools langbox'
  bar.appendChild(langBox)
  mountThemeSwitch(langBox)
  mountLangSwitch(langBox)

  // 已经手写了 skip-link 的页面（观战页）不再重复插入。
  if (!document.querySelector('.skip-link')) {
    const skip = document.createElement('a')
    skip.className = 'skip-link'
    skip.href = '#main'
    skip.textContent = t('nav.skip')
    document.body.prepend(skip)
  }
  if (!document.querySelector('.topbar')) document.body.prepend(bar)
  return bar
}

export function mountFooter() {
  // 每个页面只挂一次：重复调用会把页脚叠加两层。
  if (document.querySelector('footer.footer')) return
  const footer = document.createElement('footer')
  footer.className = 'footer'
  footer.innerHTML = `
    <span>melee ${t('common.brandSuffix')} · MIT License</span>
    <a href="https://github.com/badhope/melee" target="_blank" rel="noreferrer noopener">GitHub</a>
    <a href="https://gitee.com/badhope/melee" target="_blank" rel="noreferrer noopener">Gitee</a>
    <a href="https://gitcode.com/badhope/melee" target="_blank" rel="noreferrer noopener">GitCode</a>
    <span class="spacer"></span>
    <span data-i18n="footer.tagline"></span>`
  document.body.appendChild(footer)
  applyI18n(footer)
}

export { resolveLang }
