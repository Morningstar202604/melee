// 模态焦点管理：打开时把焦点移进去、Tab 只在弹层内循环、关闭后还给触发按钮。
// 无障碍的常见缺口不是"没有 role=dialog"，而是键盘用户 Tab 两下就跑到背后的页面，
// 关掉弹层后焦点掉回 body、读屏软件重新从页首开始。

const FOCUSABLE = [
  'a[href]', 'button:not([disabled])', 'input:not([disabled])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(
    (node) => node.offsetParent !== null || node === document.activeElement,
  )
}

/**
 * 把焦点关进 container。
 * 返回的 dispose() 会撤掉监听并把焦点还给打开前的元素。
 */
export function trapFocus(container) {
  const previous = document.activeElement
  let disposed = false

  const onKeydown = (event) => {
    if (event.key !== 'Tab') return
    const items = focusables(container)
    if (!items.length) {
      event.preventDefault()
      container.focus()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    // 焦点在容器本身或已跑出容器时，Tab 必须回到边界，不能落到背后页面。
    if (!container.contains(active)) {
      event.preventDefault()
      first.focus()
      return
    }
    if (event.shiftKey && active === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  document.addEventListener('keydown', onKeydown, true)

  // 初始焦点优先给显式标记的元素，其次表单控件，最后才是第一个可聚焦项。
  // 直接把焦点丢在右上角「关闭」上，键盘用户第一下 Tab 会先跳过整个表单。
  const items = focusables(container)
  const initial = container.querySelector('[data-autofocus]')
    || items.find((node) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(node.tagName))
    || items[0]
  if (initial) initial.focus()
  else container.focus()

  return () => {
    if (disposed) return
    disposed = true
    document.removeEventListener('keydown', onKeydown, true)
    // 焦点回到打开弹层的按钮；元素已被移除就退回 body，不让焦点悬空。
    if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
      previous.focus()
    }
  }
}

/**
 * 给按钮类元素补键盘语义：Enter / Space 触发 click。
 * div[role=button] 不会自动响应键盘，这是最常见的"看起来能点、键盘点不了"。
 */
export function activateOnKey(nodes, handler) {
  for (const node of nodes) {
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        handler(node, event)
      }
    })
  }
}
