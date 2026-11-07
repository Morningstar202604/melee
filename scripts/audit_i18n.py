"""双语界面巡检：把每个页面在 en / zh 两种语言下各截一张，并抓出漏翻与运行时报错。

为什么单独写一个脚本而不复用 audit_ui.py：
- audit_ui.py 是门禁脚本，断言的是"交互是否可用"，语言是它的手段而非目的；
- 这里要的是"界面在两种语言下都不崩、不混排、不留漏翻"，是另一个验收面。

输出到 --out 目录，文件名 `<page>-<lang>.png`；任一页有 console error、
未捕获异常、或界面上出现「键名」形态的漏翻，进程以 1 退出。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}

PAGES = [
    ("home", "index.html"),
    ("watch", "watch.html"),
    ("recap", "recap.html"),
    ("season", "season.html"),
    ("director", "director.html"),
    ("admin", "admin.html"),
]

# 漏翻的显形特征：i18n.t() 找不到键时返回键名本身，形如 `home.hero.title`。
MISSING_KEY = re.compile(r"\b(?:home|watch|recap|season|director|quick|common|nav|shot|team|game|time)\.[a-zA-Z][\w.]*")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checked = 0

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        self.checked += 1
        if not ok:
            self.failures.append(f"{label}: {detail}")


def attach_error_capture(page, report: Report, label: str) -> None:
    def on_console(message) -> None:
        if message.type == "error":
            report.check(False, f"{label} console.error", message.text[:180])

    page.on("console", on_console)
    page.on("pageerror", lambda error: report.check(False, f"{label} pageerror", str(error)[:180]))


def visible_text(page) -> str:
    return page.inner_text("body")


# 界面 chrome 的选择器：这些区域只该有界面文案，不该有对局内容。
# 对局内容（选手名、提问词、棋盘词）本来就是中文，是数据不是文案，
# 所以按容器断言而不是整页扫——否则中文内容会一直误报。
CHROME_SELECTORS = [
    "header.topbar",
    ".nav",
    ".section-head",
    ".filter-bar",
    ".sheet-head",
    ".sheet-foot",
    ".ctrl .btn",
    ".tlmeta",
    ".footer",
    "#kpis",
    "#liveCount",
    "#activityMeta",
    ".render-bar .render-label",
]


def chrome_text(page) -> str:
    """只取界面 chrome 的文本；对局数据区不参与语言断言。"""
    parts = []
    for selector in CHROME_SELECTORS:
        locator = page.locator(selector)
        for index in range(locator.count()):
            parts.append(locator.nth(index).inner_text())
    return "\n".join(parts)


def check_language(page, report: Report, label: str, lang: str) -> None:
    """语言断言：英文页的界面文案不该有中文；两种语言都不该漏键。

    只查界面 chrome：比赛内容（选手名、提问词、棋盘词）是数据，
    中文数据出现在英文界面是正确行为，不该被当成漏翻。
    """
    if lang == "en":
        cleaned = chrome_text(page).replace("中文", "")
        han = re.findall(r"[\u4e00-\u9fff]+", cleaned)
        report.check(not han, f"{label} 英文界面残留中文", " / ".join(han[:6]))

    text = visible_text(page)
    missing = MISSING_KEY.findall(text)
    # `game.xxx` 这类键名不会出现在正文；出现即说明 t() 命中失败。
    real_missing = [m for m in missing if m not in {"game.twenty-questions"}]
    report.check(not real_missing, f"{label} 漏翻键名", " / ".join(sorted(set(real_missing))[:6]))


def shoot(page, out: Path, name: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / name), full_page=True)


def run(api: str, out: Path, match_id: str, season_id: str) -> int:
    report = Report()
    with sync_playwright() as pw:
        # 本机自带 chromium 未安装时退回系统 Edge，与 audit_ui.py 同策略。
        try:
            browser = pw.chromium.launch()
        except Exception:
            browser = pw.chromium.launch(channel="msedge")

        for lang in ("en", "zh"):
            for name, path in PAGES:
                query = f"?lang={lang}&api={api}"
                if name in {"watch", "recap"} and match_id:
                    query += f"&match={match_id}"
                if name == "season" and season_id:
                    query += f"&season={season_id}"
                context = browser.new_context(viewport=DESKTOP, locale="en-US" if lang == "en" else "zh-CN")
                page = context.new_page()
                label = f"{name}-{lang}"
                attach_error_capture(page, report, label)
                page.goto(f"{api}/{path}{query}", wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(1500)
                check_language(page, report, label, lang)
                shoot(page, out, f"{label}.png")
                context.close()

        # 移动端：英文首页与观战，验证语言切换不破坏 390px 布局。
        for name, path in (("home", "index.html"), ("watch", "watch.html")):
            context = browser.new_context(viewport=MOBILE, locale="en-US", is_mobile=True, has_touch=True)
            page = context.new_page()
            label = f"{name}-en-mobile"
            attach_error_capture(page, report, label)
            query = f"?lang=en&api={api}"
            if name == "watch" and match_id:
                query += f"&match={match_id}"
            page.goto(f"{api}/{path}{query}", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(1200)
            # 横向溢出是移动端最常见的问题：必须没有横向滚动。
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            report.check(overflow <= 0, f"{label} 横向溢出", f"{overflow}px")
            shoot(page, out, f"{label}.png")
            context.close()

        browser.close()

    print(f"检查 {report.checked} 项，失败 {len(report.failures)} 项")
    for failure in report.failures:
        print("  FAIL", failure)
    return 1 if report.failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--out", default=".tmp/i18n-shots")
    parser.add_argument("--match", default="")
    parser.add_argument("--season", default="")
    args = parser.parse_args()
    return run(args.api.rstrip("/"), Path(args.out), args.match, args.season)


if __name__ == "__main__":
    sys.exit(main())
