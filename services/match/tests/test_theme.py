"""主题门禁：亮色默认 + 深色可切 + 颜色只存在于令牌文件。

这三件事在人工看截图时都"看起来没问题"，但任何一种破了都会让产品
在半数机器上变成半亮半暗。用测试锁住：

1. 每个页面都必须在解析样式前同步确定主题，否则首帧会闪一次错误底色。
2. 每个页面都必须能切到深色，且切换后颜色真的变（不是只改了个属性）。
3. 除令牌文件外，前端源码里不允许出现写死的颜色值——写死一处，
   切换主题就破一处，而这类破法在浅色底上往往还是低对比度的可读性缺陷。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[3] / "apps" / "web"
PAGES = sorted(WEB.glob("*.html"))

# 允许写死颜色的地方：令牌定义本身、以及少数与主题无关的固定色
# （文字压在图上的白/黑、视频播放器的纯黑底）。
ALLOWED_LITERAL_FILES = {"theme.css"}
ALLOWED_LITERALS = {"#000", "#fff", "#FFFFFF", "#04121A"}

COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")


def test_every_page_boots_theme_before_styles_are_used():
    """主题脚本必须在 <head> 里、且是同步脚本。

    若写成 type="module"，浏览器会延迟到解析完成才执行，用户会先看到
    一帧亮色再被刷成深色——那一下闪烁比没有深色模式更显廉价。
    """
    for page in PAGES:
        html = page.read_text(encoding="utf-8")
        head = html.split("</head>", 1)[0]
        assert "src/theme-boot.js" in head, f"{page.name} 没有在 <head> 引入 theme-boot.js"
        assert 'type="module"' not in head.split("theme-boot.js")[0].split("<script")[-1], (
            f"{page.name} 的 theme-boot 是 module 脚本，会延迟执行导致首帧闪烁"
        )
        # 必须在主样式表之前就位，否则解析样式时 data-theme 还不存在
        assert html.index("theme-boot.js") < html.index("theme.css"), f"{page.name} 主题脚本晚于样式表"


def test_theme_css_defines_both_palettes():
    css = (WEB / "src" / "theme.css").read_text(encoding="utf-8")
    assert ":root{" in css
    assert '[data-theme="dark"]' in css
    # 亮色必须是默认：:root 里不能出现深色底
    light_block = css.split(':root{', 1)[1].split('}', 1)[0]
    assert "--bg:#F4F6FB" in light_block, "默认（无 data-theme）不是亮色"
    dark_block = css.split('[data-theme="dark"]{', 1)[1].split("\n}", 1)[0]
    assert "--bg:#07090D" in dark_block, "深色主题缺少底色令牌"


def test_pages_have_a_theme_switch():
    """每个页面都要有可见的切换入口，不能只靠 URL 参数。"""
    nav = (WEB / "src" / "nav.js").read_text(encoding="utf-8")
    assert "mountThemeSwitch" in nav, "顶栏没有挂主题切换按钮"
    watch = (WEB / "src" / "main.js").read_text(encoding="utf-8")
    assert "mountThemeSwitch" in watch, "观战页（静态顶栏）没有挂主题切换按钮"


@pytest.mark.parametrize("path", sorted((WEB / "src").rglob("*.js")) + PAGES)
def test_no_hardcoded_colors_outside_tokens(path: Path):
    text = path.read_text(encoding="utf-8")
    hits = [literal for literal in COLOR_PATTERN.findall(text) if literal not in ALLOWED_LITERALS]
    assert not hits, f"{path.name} 写死了颜色 {hits[:8]}；请改用 theme.css 的令牌"


def test_watch_css_uses_tokens_only():
    css = (WEB / "src" / "watch.css").read_text(encoding="utf-8")
    hits = [literal for literal in COLOR_PATTERN.findall(css) if literal not in ALLOWED_LITERALS]
    assert not hits, f"watch.css 写死了颜色 {hits[:8]}"
