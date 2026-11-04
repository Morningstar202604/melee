#!/usr/bin/env python
"""重拍全部产品截图（真实服务 + 真实比赛），英文与中文各一套。

前置：
    python scripts/serve.py                      # 单端口 8099，前端也在里面
    python scripts/seed_demo.py --api http://127.0.0.1:8099
    python scripts/capture_gallery.py            # 写入 docs/screenshots/{en,zh}/*.png

要点：
- 所有截图都在真实数据上拍：没有静态占位、没有假数字；
- 每张截图前先清 endcard 遮罩，拍的是"内容"而不是"结果弹层"；
- 桌面 1600×1000、移动 390×844，两档都拍；
- 两种语言各拍一遍：README 的英文版和中文版各自引用自己语言的那一套。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "screenshots"

# 截图里的选手名跟随语言：英文版出现中文名会显得像没做完的本地化。
PLAYERS = {
    "en": {
        "codenames": ["Red Spymaster", "Blue Spymaster", "Red Operative", "Blue Operative"],
        "duo": ["Grey Falcon", "Inkstone"],
        "director": ["Observer", "Auditor"],
    },
    "zh": {
        "codenames": ["红队队长", "蓝队队长", "红队队员", "蓝队队员"],
        "duo": ["灰隼", "砚台"],
        "director": ["观测者", "审核员"],
    },
}


def launch_browser(playwright):
    """Use bundled Chromium, falling back to the installed Edge on Windows."""
    try:
        return playwright.chromium.launch()
    except Exception:
        if sys.platform != "win32":
            raise
        return playwright.chromium.launch(channel="msedge")


def get(api: str, path: str, lang: str = "") -> dict:
    headers = {"Accept-Language": lang} if lang else {}
    request = urllib.request.Request(f"{api}{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def post(api: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def wait_match(api: str, match_id: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = get(api, f"/matches/{match_id}/events")["events"]
        if any(event["type"] == "match.ended" for event in events):
            return
        time.sleep(0.5)
    raise TimeoutError(match_id)


def pick(api: str, limit: int = 100) -> dict[str, str]:
    """每种赛制挑"事件最多的一场已结束比赛"——截图里内容最丰富。"""
    best: dict[str, tuple[int, str]] = {}
    for item in get(api, f"/archive?limit={limit}")["items"]:
        if item["status"] != "ended":
            continue
        current = best.get(item["game"])
        if current is None or item["events"] > current[0]:
            best[item["game"]] = (item["events"], item["matchId"])
    return {game: match_id for game, (_count, match_id) in best.items()}


def ensure_month_of_data(api: str, lang: str = "en") -> None:
    """赛事库空的时候补一批比赛，避免截到空白页。已有数据就完全不动。"""
    existing = get(api, "/archive?limit=100")["count"]
    if existing >= 6:
        return
    names = PLAYERS.get(lang, PLAYERS["en"])
    games = [
        ("codenames", names["codenames"]),
        ("tic-tac-toe", names["duo"]),
        ("nim", names["duo"]),
        ("connect-four", names["duo"]),
        ("othello", names["duo"]),
        ("twenty-questions", names["duo"]),
    ]
    for game, players in games:
        result = post(
            api,
            "/matches",
            {"game": game, "seed": 2026, "players": [{"id": p, "delayMs": 80} for p in players]},
        )
        try:
            wait_match(api, result["matchId"])
        except TimeoutError:
            continue


def dismiss(page) -> None:
    page.evaluate("document.getElementById('endcard')?.classList.remove('on')")


def capture(lang: str, api: str, season: str, skip_live: bool) -> list[str]:
    """拍一种语言的全套截图，返回页面错误列表。"""
    out_dir = OUT / (lang if lang in {"en", "zh"} else "en")
    out_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    def shot(page, name: str, *, full: bool = True) -> None:
        page.screenshot(path=str(out_dir / name), full_page=full)

    def url(path: str) -> str:
        joiner = "&" if "?" in path else "?"
        return f"{api}/{path}{joiner}lang={lang}"

    names = PLAYERS.get(lang, PLAYERS["en"])
    live_id = ""
    if not skip_live:
        # 已有直播就复用，避免每跑一次截图就多一场"正在直播"。
        existing = get(api, "/archive?limit=5&status=live")["items"]
        if existing:
            live_id = existing[0]["matchId"]
        else:
            live = post(
                api,
                "/matches",
                {
                    "game": "twenty-questions",
                    "seed": 2027,
                    "maxQuestions": 21,
                    "players": [{"id": n, "delayMs": 5200} for n in names["duo"]],
                },
            )
            live_id = live["matchId"]

    ensure_month_of_data(api, lang)
    picked = pick(api)

    with sync_playwright() as p:
        browser = launch_browser(p)
        # 2x 只用于 1 屏内的舞台截图；整页长图用 1x，否则单张 1.2MB、README 首屏会卡。
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda exc: errors.append(f"{lang} pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: errors.append(f"{lang} console: {msg.text}") if msg.type == "error" else None,
        )

        # 01 首页
        page.goto(url(""), wait_until="networkidle")
        page.wait_for_timeout(1400)
        shot(page, "01-home.png")

        # 02 直播 / 03 二十问回放
        if live_id:
            page.goto(url(f"watch.html?match={live_id}"), wait_until="networkidle")
            page.wait_for_timeout(4200)
            dismiss(page)
            shot(page, "02-live.png", full=False)

        qid = picked.get("twenty-questions")
        if qid:
            page.goto(url(f"watch.html?match={qid}"), wait_until="networkidle")
            page.wait_for_timeout(1800)
            dismiss(page)
            page.evaluate(
                """() => { const s = document.getElementById('scrub');
                s.value = String(Math.floor(Number(s.max) * 0.62));
                s.dispatchEvent(new Event('input')); }"""
            )
            page.wait_for_timeout(900)
            shot(page, "03-twenty-questions.png", full=False)

        # 04-07 四种棋盘/团队赛制
        for game, name in [
            ("codenames", "04-codenames.png"),
            ("tic-tac-toe", "05-tic-tac-toe.png"),
            ("nim", "06-nim.png"),
            ("othello", "07-othello.png"),
        ]:
            match_id = picked.get(game)
            if not match_id:
                continue
            page.goto(url(f"watch.html?match={match_id}"), wait_until="networkidle")
            page.wait_for_timeout(1500)
            dismiss(page)
            page.evaluate(
                """() => { const s = document.getElementById('scrub');
                s.value = String(Math.max(1, Math.floor(Number(s.max) * 0.7)));
                s.dispatchEvent(new Event('input')); }"""
            )
            page.wait_for_timeout(700)
            shot(page, name, full=False)

        # 08 战报
        if picked.get("codenames"):
            page.goto(url(f"recap.html?match={picked['codenames']}"), wait_until="networkidle")
            page.wait_for_timeout(1200)
            shot(page, "08-recap.png")

        # 09 赛季
        page.goto(url(f"season.html?season={season}"), wait_until="networkidle")
        page.wait_for_timeout(1500)
        shot(page, "09-season.png")

        # 10 导播台：现开一场慢速直播，保证台上真有东西可切。
        director_live = post(
            api,
            "/matches",
            {
                "game": "twenty-questions",
                "seed": 411,
                "maxQuestions": 21,
                "players": [{"id": n, "delayMs": 9000} for n in names["director"]],
            },
        )
        page.goto(url(f"director.html?match={director_live['matchId']}"), wait_until="networkidle")
        page.wait_for_timeout(3500)
        page.click(".shot[data-shot='close']")
        page.fill("#note", "demo: close-up" if lang == "en" else "演示：切特写")
        page.wait_for_timeout(400)
        page.click("#push")
        page.wait_for_timeout(1200)
        shot(page, "10-director.png", full=False)

        # 11 移动端
        mobile = browser.new_page(
            viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True, has_touch=True
        )
        mobile.goto(url(""), wait_until="networkidle")
        mobile.wait_for_timeout(1400)
        shot(mobile, "11-mobile-home.png")

        target = live_id or picked.get("twenty-questions")
        if target:
            mobile.goto(url(f"watch.html?match={target}"), wait_until="networkidle")
            mobile.wait_for_timeout(1800)
            dismiss(mobile)
            shot(mobile, "12-mobile-watch.png")

        browser.close()

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--season", default="demo-2026")
    parser.add_argument("--skip-live", action="store_true", help="不新建直播场（拍摄机快速重拍）")
    parser.add_argument("--langs", default="en,zh", help="要拍的语言，逗号分隔（默认 en,zh）")
    args = parser.parse_args()

    errors: list[str] = []
    for lang in [item.strip() for item in args.langs.split(",") if item.strip()]:
        errors += capture(lang, args.api.rstrip("/"), args.season, args.skip_live)

    if errors:
        print("页面错误：")
        for item in errors[:10]:
            print(" -", item)
        return 1
    print(f"截图写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
