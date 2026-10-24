// 顶栏是五个页面唯一共享的结构：集中在这里生成，改一次全站一致。
// 用 DOM API 构建而不是 innerHTML：页面名来自常量，但仍然不给注入留口子。

const PAGES = [
  { href: 'index.html', label: '首页', key: 'home' },
  { href: 'watch.html', label: '观战', key: 'watch' },
  { href: 'season.html', label: '赛季', key: 'season' },
  { href: 'recap.html', label: '战报', key: 'recap' },
  { href: 'director.html', label: '导播', key: 'director' },
]

export function mountTopbar({ active = '', tools = null } = {}) {
  const bar = document.createElement('header')
  bar.className = 'topbar'

  const brand = document.createElement('a')
  brand.className = 'brand'
  brand.href = 'index.html'
  brand.setAttribute('data-keep-query', '')
  brand.innerHTML = '<span class="mark">M</span>MELEE <span>/</span> 转播台'
  bar.appendChild(brand)

  const nav = document.createElement('nav')
  nav.className = 'nav'
  nav.setAttribute('aria-label', '主导航')
  for (const page of PAGES) {
    const link = document.createElement('a')
    link.href = page.href
    link.textContent = page.label
    link.setAttribute('data-keep-query', '')
    if (page.key === active) {
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

  // 已经手写了 skip-link 的页面（观战页）不再重复插入。
  if (!document.querySelector('.skip-link')) {
    const skip = document.createElement('a')
    skip.className = 'skip-link'
    skip.href = '#main'
    skip.textContent = '跳到主要内容'
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
    <span>melee 转播台 · MIT License</span>
    <a href="https://github.com/badhope/melee" target="_blank" rel="noreferrer noopener">GitHub</a>
    <a href="https://gitee.com/badhope/melee" target="_blank" rel="noreferrer noopener">Gitee</a>
    <a href="https://gitcode.com/badhope/melee" target="_blank" rel="noreferrer noopener">GitCode</a>
    <span class="spacer"></span>
    <span>事件日志是唯一真相 · 回放与直播共用同一个 reducer</span>`
  document.body.appendChild(footer)
}
