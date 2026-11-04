// lit-html 的 render 只替换自己渲染过的部分，不会清掉容器里原有的静态占位文本。
// 统一在首次绘制前清空一次，之后保留 part 缓存（避免每次重绘丢焦点）。
// 参数顺序与 lit-html 的 render 保持一致：(template, container)。
import { render as litRender } from '../vendor/lit-html.js'

export function paint(template, node) {
  if (!node) return
  if (!node.dataset.painted) {
    node.textContent = ''
    node.dataset.painted = '1'
  }
  litRender(template, node)
}
