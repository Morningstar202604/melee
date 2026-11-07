// 主题：亮色为默认（演播室白光），可切深色（暗场）。
// 切换只改 <html data-theme>，所有颜色来自 theme.css 的令牌，组件不需要知道当前主题。
import { t } from './i18n.js'

const KEY = 'melee.theme'
export const THEMES = ['light', 'dark']

export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'
}

export function setTheme(theme, { persist = true } = {}) {
  const next = THEMES.includes(theme) ? theme : 'light'
  document.documentElement.setAttribute('data-theme', next)
  // 滚动条与 select/checkbox 等原生控件跟随主题，否则深色页面里会冒出白下拉框。
  document.documentElement.style.colorScheme = next
  if (persist) {
    try {
      localStorage.setItem(KEY, next)
    } catch (error) {
      /* 隐私模式下写不进去就算了，本次会话仍然生效 */
    }
  }
  document.dispatchEvent(new CustomEvent('melee:theme', { detail: { theme: next } }))
  return next
}

export function toggleTheme() {
  return setTheme(currentTheme() === 'dark' ? 'light' : 'dark')
}

/** 主题切换按钮：显示的是"点了会变成什么"，与语言切换同一规则。 */
export function mountThemeSwitch(parent) {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'btn sm ghost theme-toggle'
  const paint = () => {
    const next = currentTheme() === 'dark' ? 'light' : 'dark'
    button.textContent = next === 'dark' ? '☾' : '☀'
    button.title = t(next === 'dark' ? 'common.theme.dark' : 'common.theme.light')
    button.setAttribute('aria-label', button.title)
  }
  paint()
  button.onclick = () => {
    toggleTheme()
    paint()
  }
  document.addEventListener('melee:theme', paint)
  parent.appendChild(button)
  return button
}
