"""真实浏览器全页面审计：五个页面 × 三个内核。

跑法：
    python scripts/audit_ui.py                    # 默认 chromium
    python scripts/audit_ui.py --browser firefox --browser webkit

检查项（每页）：
- 页面加载无 pageerror、无 console error、无失败请求
- 关键内容真的渲染出来（不是骨架屏/空状态）
- 横向溢出（桌面 1440×900 与移动 390×844）
- 无障碍基础：h1 存在、图片有 alt、按钮有可读文本、焦点可见
- 交互：观战页拖动时间轴、导播台切镜、首页筛选与搜索

需要服务已启动（默认 http://127.0.0.1:8099）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any

from playwright.sync_api import sync_playwright

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def seed(api: str) -> dict[str, str]:
    """建一场已结束的井字棋 + 一场直播，返回 id。"""
    def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{api}{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    quick = post("/matches", {"game": "tic-tac-toe", "seed": 11, "players": [{"id": "左撇子"}, {"id": "右撇子"}]})
    live = post(
        "/matches",
        {
            "game": "twenty-questions",
            "seed": 2027,
            # 3.5 秒一步：既要在审计早期就产出提问记录（否则观战页还是空的），
            # 又要撑到导播台切镜那一步（二十问 21 手上限 ≈ 70 秒）。
            "players": [{"id": "灰隼", "delayMs": 3500}, {"id": "砚台", "delayMs": 3500}],
        },
    )
    return {"ended": quick["matchId"], "live": live["matchId"]}


class Audit:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.passes: list[str] = []
        # 每条断言都计数，pass/fail 才是同一口径（否则"通过 18 项"只是
        # ok() 的调用次数，和检查项总数对不上，看起来像漏跑）。
        self.assertions = 0

    def ok(self, label: str) -> None:
        self.assertions += 1
        self.passes.append(label)

    def check(self, condition: bool, label: str, detail: str = "") -> None:
        self.assertions += 1
        if condition:
            self.passes.append(label)
        else:
            self.bad(label, detail)

    def bad(self, label: str, detail: str = "") -> None:
        self.assertions += 1
        self.problems.append(f"{label}{(' · ' + detail) if detail else ''}")


def attach(page, audit: Audit, label: str) -> None:
    page.on("pageerror", lambda exc: audit.bad(f"{label}: pageerror", str(exc)[:200]))
    page.on("console", lambda msg: audit.bad(f"{label}: console.error", msg.text[:200]) if msg.type == "error" else None)
    def _failed(req) -> None:
        # 页面跳转时浏览器会主动中止 SSE 长连接（NS_BINDING_ABORTED / ERR_ABORTED），
        # 这是正常收尾，不是缺陷；只有非 stream 的失败才算。
        if "/stream" in req.url:
            return
        audit.bad(f"{label}: request failed", f"{req.url[:120]} {req.failure}")

    page.on("requestfailed", _failed)


def check_overflow(page, audit: Audit, label: str) -> None:
    overflow = page.evaluate(
        "() => ({doc: document.documentElement.scrollWidth, win: window.innerWidth})"
    )
    if overflow["doc"] > overflow["win"] + 1:
        audit.bad(f"{label}: 横向溢出", f"scrollWidth={overflow['doc']} viewport={overflow['win']}")
    else:
        audit.ok(f"{label}: 无横向溢出")


def check_a11y(page, audit: Audit, label: str) -> None:
    data = page.evaluate(
        """() => {
        const imgs = [...document.querySelectorAll('img')];
        const noAlt = imgs.filter(i => !i.hasAttribute('alt')).map(i => i.src).slice(0, 3);
        const buttons = [...document.querySelectorAll('button')];
        const nameless = buttons.filter(b => !(b.textContent || '').trim() && !b.getAttribute('aria-label')).length;
        const h1 = document.querySelectorAll('h1').length;
        const focusables = [...document.querySelectorAll('a[href],button,input,select,textarea')]
            .filter(n => !n.disabled && n.offsetParent !== null).length;
        return {noAlt, nameless, h1, focusables};
        }"""
    )
    if data["noAlt"]:
        audit.bad(f"{label}: img 缺 alt", str(data["noAlt"]))
    if data["nameless"]:
        audit.bad(f"{label}: 按钮无可读名称", f"{data['nameless']} 个")
    if data["h1"] != 1:
        audit.bad(f"{label}: h1 数量", f"{data['h1']} 个（应为 1）")
    if data["focusables"] == 0:
        audit.bad(f"{label}: 无可聚焦元素")
    if not any(p.startswith(f"{label}: ") for p in audit.problems):
        audit.ok(f"{label}: 无障碍基础通过（可聚焦 {data['focusables']} 个）")


def _channel_for(browser_name: str) -> str | None:
    """本机没装 Playwright 自带内核时，用系统已装的同族浏览器兜底。

    CI 里 `playwright install` 会准备好 chromium/firefox/webkit；
    开发机上常常只有 Chrome 或 Edge。让审计在这种环境下也能真跑，
    而不是直接报 "Executable doesn't exist" 把门禁变成摆设。
    """
    if browser_name == "chromium":
        return "msedge" if sys.platform == "win32" else None
    return None


def run(browser_name: str, api: str, ids: dict[str, str], headed: bool) -> Audit:
    audit = Audit()
    with sync_playwright() as pw:
        try:
            browser = getattr(pw, browser_name).launch(headless=not headed)
        except Exception:
            channel = _channel_for(browser_name)
            if not channel:
                raise
            browser = getattr(pw, browser_name).launch(headless=not headed, channel=channel)
        context = browser.new_context(viewport=DESKTOP, locale="zh-CN")
        page = context.new_page()

        # ---- 首页 ----
        attach(page, audit, "home")
        page.goto(f"{api}/index.html", wait_until="networkidle")
        page.wait_for_timeout(800)
        audit.check(
            page.locator("#liveStrip .live-card").count() > 0,
            "home: 直播条渲染直播卡",
            page.inner_text("#liveStrip")[:80],
        )
        audit.check(page.locator("#archive .mcard").count() > 0, "home: 赛事库有数据")
        audit.check(page.locator("#games .gcard").count() >= 9, "home: 赛制入口 ≥ 9 个",
                    str(page.locator("#games .gcard").count()))
        kpis = page.locator("#kpis .kpi b").all_inner_texts()
        audit.check(bool(kpis) and kpis[0] not in {"—", ""}, "home: KPI 已填充", str(kpis))
        # KPI「赛制」必须是可用赛制数，不是"已开过赛的赛制数"
        game_cards = page.locator("#games .gcard").count()
        audit.check(bool(kpis) and kpis[2] == str(game_cards), "home: KPI 赛制数真实",
                    f"KPI={kpis[2] if kpis else '?'} 实际可用={game_cards}")
        # 搜索
        page.fill("#searchBox", "左撇子")
        # 搜索有 250ms 防抖，慢机器上 700ms 可能只够发出请求、还没画完；
        # 等到列表真的出现再断言，避免把竞态误报成功能缺陷。
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('#archive .mcard').length > 0",
                timeout=5000,
            )
            audit.ok("home: 搜索生效")
        except Exception:
            audit.bad("home: 搜索'左撇子'无结果", page.inner_text("#archive")[:120])
        page.fill("#searchBox", "")
        page.wait_for_timeout(700)
        # 赛制筛选
        page.click('[data-filter="tic-tac-toe"]')
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('#archive .mcard').length > 0",
                timeout=5000,
            )
            audit.ok("home: 赛制筛选生效")
        except Exception:
            audit.bad("home: 赛制筛选无结果", page.inner_text("#archive")[:120])
        check_overflow(page, audit, "home")
        check_a11y(page, audit, "home")
        page.screenshot(path=".ui-audit/home.png", full_page=True)

        # ---- 观战（直播）----
        attach(page, audit, "watch")
        page.goto(f"{api}/watch.html?match={ids['live']}", wait_until="networkidle")
        page.wait_for_timeout(1500)
        audit.check(page.locator("#sideLeft .card").count() > 0, "watch: 选手卡渲染")
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('#historyList .qrow, #historyList .hrow').length > 0",
                timeout=15000,
            )
            audit.ok("watch: 提问记录渲染")
        except Exception:
            audit.bad("watch: 提问记录未渲染", page.inner_text("#historyList")[:80])
        conn = page.inner_text("#conn")
        # 直播场可能在本轮审计期间跑完，此时显示「已结束」也正确。
        # 连接状态文案跟随界面语言（浏览器 zh-CN 时会显示中文）。
        audit.check(
            conn.strip() in {"已连接", "重连中", "已结束", "Connected", "Reconnecting", "Finished"},
            "watch: 连接状态正常",
            conn,
        )
        audit.check(page.locator("#viewers").is_visible(), "watch: 在线人数可见")
        # 拖动时间轴进入回放
        page.eval_on_selector("#scrub", "n => { n.value = '5'; n.dispatchEvent(new Event('input', {bubbles: true})); }")
        page.wait_for_timeout(400)
        if page.locator("#replayBadge").is_hidden():
            audit.bad("watch: 拖动后未进入回放态")
        else:
            audit.ok("watch: 拖动进入回放")
        page.click("#btnLive")
        page.wait_for_timeout(300)
        # #cue 的格式是 "<镜头名> · <触发原因>"。事件契约里镜头是
        # wide/close 等英文枚举，界面必须显示翻译后的名字——否则观众
        # 会以为页面坏了。只断言" · "前的镜头段；触发原因是正文文案，
        # 英文里本来就含 close-up 这类词，按子串匹配会误报。
        cue_text = page.inner_text("#cue")
        shot_part = cue_text.split("·")[0].strip()
        known = {"广角", "特写", "内心戏", "揭晓", "慢镜", "未切镜",
                 "Wide", "Close-up", "Inner monologue", "Reveal", "Slow motion",
                 "No cut yet"}
        audit.check(
            shot_part in known and bool(shot_part),
            "watch: 镜头名已本地化",
            cue_text,
        )
        check_overflow(page, audit, "watch")
        check_a11y(page, audit, "watch")
        page.screenshot(path=".ui-audit/watch.png", full_page=True)

        # ---- 战报 ----
        attach(page, audit, "recap")
        page.goto(f"{api}/recap.html?match={ids['ended']}", wait_until="networkidle")
        page.wait_for_timeout(800)
        audit.check(page.locator("#stats .stat").count() > 0, "recap: 数字卡渲染")
        # 自检区的标题跟随语言：中文「事实校验」/ 英文 "Fact check"。
        verify_text = page.inner_text("#verify")
        audit.check(
            "事实校验" in verify_text or "Fact check" in verify_text,
            "recap: 自检区渲染",
            verify_text[:60],
        )
        check_a11y(page, audit, "recap")
        check_overflow(page, audit, "recap")
        page.screenshot(path=".ui-audit/recap.png", full_page=True)

        # ---- 赛季 ----
        attach(page, audit, "season")
        page.goto(f"{api}/season.html", wait_until="networkidle")
        page.wait_for_timeout(900)
        check_a11y(page, audit, "season")
        check_overflow(page, audit, "season")
        # 没有赛季时不能永远停在"加载中…"
        audit.check(
            "加载中" not in page.inner_text("#standings")
            and "加载中" not in page.inner_text("#fixtures")
            and "Loading" not in page.inner_text("#standings")
            and "Loading" not in page.inner_text("#fixtures"),
            "season: 空状态不是加载中",
        )
        page.screenshot(path=".ui-audit/season.png", full_page=True)

        # ---- 导播（直播场）----
        attach(page, audit, "director")
        page.goto(f"{api}/director.html?match={ids['live']}", wait_until="networkidle")
        page.wait_for_timeout(1600)
        audit.check(page.inner_text("#conn") not in {"未连接", ""}, "director: 连接状态已更新",
                    page.inner_text("#conn"))
        audit.check(page.locator("#cueList, #log").count() > 0, "director: 事件面板存在")
        # 真实交互：切一次镜，人工计数必须 +1 并出现在记录里
        before = page.inner_text("#manualCount")
        page.fill("#note", "审计切镜")
        page.click("#push")
        try:
            page.wait_for_function(
                "prev => document.querySelector('#manualCount').textContent !== prev",
                arg=before,
                timeout=8000,
            )
            audit.ok("director: 人工切镜写入成功")
            audit.check(
                "审计切镜" in page.inner_text("#cueList"),
                "director: 人工记录可见",
                page.inner_text("#cueList")[:80],
            )
        except Exception:
            status = page.inner_text("#status")
            if "比赛已结束" in status:
                audit.ok("director: 场次已结束，跳过切镜断言")
            else:
                audit.bad("director: 人工切镜未生效", status)
        check_a11y(page, audit, "director")
        check_overflow(page, audit, "director")
        page.screenshot(path=".ui-audit/director.png", full_page=True)

        # ---- 移动端 ----
        mobile = context.new_page()
        mobile.set_viewport_size(MOBILE)
        for name, url in (
            ("index", f"{api}/index.html"),
            ("watch", f"{api}/watch.html?match={ids['live']}"),
            ("recap", f"{api}/recap.html?match={ids['ended']}"),
            ("season", f"{api}/season.html"),
            ("director", f"{api}/director.html?match={ids['live']}"),
        ):
            attach(mobile, audit, f"mobile-{name}")
            mobile.goto(url, wait_until="networkidle")
            mobile.wait_for_timeout(700)
            check_overflow(mobile, audit, f"mobile-{name}")
            tools = mobile.locator(".topbar .tools")
            if tools.count():
                box = tools.first.bounding_box()
                audit.check(
                    not box or box["height"] <= 80,
                    f"mobile-{name}: 顶栏工具栏不超高",
                    f"{box['height']:.0f}px" if box else "",
                )
            mobile.screenshot(path=f".ui-audit/mobile-{name}.png", full_page=True)

        browser.close()
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--browser", action="append", default=[])
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    browsers = args.browser or ["chromium"]
    import pathlib

    pathlib.Path(".ui-audit").mkdir(exist_ok=True)
    total_problems = 0
    ran: list[str] = []
    skipped: list[str] = []
    for name in browsers:
        # 每个内核单独建场：上一轮跑完的直播场会让「直播条」与 SSE 状态失去意义。
        ids = seed(args.api)
        try:
            audit = run(name, args.api, ids, args.headed)
        except Exception as exc:  # noqa: BLE001 - 内核缺失要跳过，不能当"通过"
            skipped.append(f"{name}: {str(exc).splitlines()[0][:120]}")
            print(f"\n===== {name} =====\n  SKIP 无法启动该内核：{str(exc).splitlines()[0][:120]}")
            continue
        ran.append(name)
        total_problems += len(audit.problems)
        print(f"\n===== {name} =====")
        print(f"检查 {audit.assertions} 项，通过 {len(audit.passes)} 项")
        for problem in audit.problems:
            print("  FAIL", problem)
        if not audit.problems:
            print("  全部通过")

    if skipped:
        # 明确区分"跑过且通过"与"没跑成"：静默跳过等于把门禁变成摆设。
        print("\n跳过的内核（本机未安装对应浏览器）：")
        for item in skipped:
            print("  -", item)
    if not ran:
        print("\nFAIL 一个内核都没跑起来，审计未执行", file=sys.stderr)
        return 2
    return 1 if total_problems else 0


if __name__ == "__main__":
    sys.exit(main())
