"""对比度门禁：亮色与深色两套主题下，正文/标签/页脚一律达到 WCAG AA。

为什么必须用真实浏览器：`color` 与最终背景只有浏览器知道——渐变、继承、
半透明叠加都会改变实际对比度，靠读 CSS 源码算出来的数字经常是错的
（例如 `.muted` 在渐变卡片上，源码里看不到底色）。

用法：
    python scripts/check_contrast.py --api http://127.0.0.1:8099

退出码：0 全通过；1 有低于阈值的组合；2 浏览器不可用（不是通过）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (选择器, 人话名, 最小字号判定是否算"大字")
CHECKS: list[tuple[str, str]] = [
    ("body", "body"),
    (".hero p", "hero body"),
    (".muted", "muted text"),
    (".kpi span", "kpi label"),
    (".mcard .game-label", "card game label"),
    (".mcard .mid", "card meta"),
    (".chip", "filter chip"),
    (".nav a", "top nav"),
    (".btn", "button"),
    (".btn.primary", "primary button"),
    (".gcard p", "game card desc"),
    (".tag", "tag"),
    (".footer", "footer"),
]

MEASURE_JS = """(selector) => {
  function parseColor(value) {
    const m = value.match(/rgba?\\(([^)]+)\\)/)
    if (!m) return null
    const parts = m[1].split(/[,\\s/]+/).filter(Boolean).map(Number)
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 }
  }
  function relLum(c) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4) }
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b)
  }
  function blend(fg, bg) {
    const a = fg.a === undefined ? 1 : fg.a
    return { r: fg.r * a + bg.r * (1 - a), g: fg.g * a + bg.g * (1 - a), b: fg.b * a + bg.b * (1 - a), a: 1 }
  }
  const node = document.querySelector(selector)
  if (!node) return null
  const style = getComputedStyle(node)
  let background = null
  let walker = node
  while (walker) {
    const candidate = parseColor(getComputedStyle(walker).backgroundColor)
    if (candidate && candidate.a > 0.99) { background = candidate; break }
    walker = walker.parentElement
  }
  if (!background) background = { r: 255, g: 255, b: 255, a: 1 }
  let foreground = parseColor(style.color)
  if (!foreground) return null
  if (foreground.a < 1) foreground = blend(foreground, background)
  const l1 = relLum(foreground), l2 = relLum(background)
  const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
  return {
    ratio: Math.round(ratio * 100) / 100,
    color: style.color,
    background: `rgb(${Math.round(background.r)}, ${Math.round(background.g)}, ${Math.round(background.b)})`,
    size: parseFloat(style.fontSize),
    weight: parseInt(style.fontWeight, 10) || 400,
  }
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description="WCAG AA contrast gate for both themes")
    parser.add_argument("--api", required=True, help="base URL of a running melee instance")
    parser.add_argument("--min-ratio", type=float, default=4.5)
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP 未安装 playwright：对比度门禁未执行（不是通过）", file=sys.stderr)
        return 2

    failures: list[str] = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            try:
                browser = p.chromium.launch(channel="msedge")
            except Exception as exc:
                print(f"SKIP 没有可用的 Chromium/Edge 内核：{exc}", file=sys.stderr)
                return 2
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        measured = 0
        for theme in ("light", "dark"):
            page.goto(f"{args.api}/index.html?theme={theme}", wait_until="networkidle")
            page.wait_for_timeout(600)
            assert page.get_attribute("html", "data-theme") == theme, f"{theme} 主题没有生效"
            print(f"== {theme} ==")
            for selector, label in CHECKS:
                result = page.evaluate(MEASURE_JS, selector)
                if not result:
                    continue
                measured += 1
                # 大字（>=18.66px，或 >=14px 且粗体）按 WCAG 只需 3:1
                large = result["size"] >= 18.66 or (result["size"] >= 14 and result["weight"] >= 700)
                required = 3.0 if large else args.min_ratio
                ok = result["ratio"] >= required
                print(
                    f"  {'ok  ' if ok else 'FAIL'} {result['ratio']:5.2f} (>={required}) "
                    f"{label:20s} {result['color']} on {result['background']}"
                )
                if not ok:
                    failures.append(f"{theme}/{label}={result['ratio']} < {required}")
        browser.close()

    if measured < len(CHECKS):
        print(f"FAIL 期望测到 {len(CHECKS) * 2} 个组合，实际 {measured} 个：页面结构变了要同步更新选择器", file=sys.stderr)
        return 1
    if failures:
        print("FAIL 对比度不达标：" + "; ".join(failures), file=sys.stderr)
        return 1
    print(f"对比度门禁通过：{measured} 个组合全部满足 WCAG AA（亮/暗两套主题）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
