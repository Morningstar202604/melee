#!/usr/bin/env python
"""截图脚本：对运行中的服务抓取全部页面。

前置：
    MELEE_DB=/tmp/demo.db python scripts/serve.py --port 8099
    cd apps/web && python -m http.server 8088
    python scripts/seed_demo.py            # 生成演示数据并打印 seasonId

用法：
    python scripts/capture_screenshots.py --api http://127.0.0.1:8099 --web http://127.0.0.1:8088
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def pick_matches(api: str) -> dict[str, str]:
    """从服务里挑出各赛制的一场比赛：优先直播中，其次已结束。"""
    matches = fetch_json(f"{api}/matches")["matches"]
    picked: dict[str, str] = {}
    for match_id in reversed(matches):
        try:
            state = fetch_json(f"{api}/matches/{match_id}/state")
        except Exception:  # noqa: BLE001
            continue
        game = state.get("game")
        if game and game not in picked:
            picked[game] = match_id
        if len(picked) >= 5:
            break
    return picked


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--web", default="http://127.0.0.1:8088")
    parser.add_argument("--season", default="")
    args = parser.parse_args()

    picked = pick_matches(args.api)
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"{args.web}/index.html?api={args.api}", wait_until="networkidle")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUT / "01-lobby.png"), full_page=True)

        shots = [
            ("twenty-questions", "02-twenty-questions.png"),
            ("codenames", "03-codenames.png"),
            ("tic-tac-toe", "04-tic-tac-toe.png"),
            ("nim", "05-nim.png"),
        ]
        for game, name in shots:
            match_id = picked.get(game)
            if not match_id:
                print(f"跳过 {name}：没有 {game} 比赛")
                continue
            page.goto(f"{args.web}/index.html?api={args.api}&match={match_id}", wait_until="networkidle")
            page.wait_for_timeout(2500)
            page.evaluate("document.getElementById('btnLive').click()")
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / name), full_page=True)

        if picked.get("codenames"):
            page.goto(f"{args.web}/recap.html?api={args.api}&match={picked['codenames']}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "06-recap.png"), full_page=True)

        if args.season:
            page.goto(f"{args.web}/season.html?api={args.api}&season={args.season}", wait_until="networkidle")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(OUT / "07-season.png"), full_page=True)

        page.goto(f"{args.web}/director.html?api={args.api}", wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(OUT / "08-director.png"), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        if picked.get("twenty-questions"):
            mobile.goto(f"{args.web}/index.html?api={args.api}&match={picked['twenty-questions']}", wait_until="networkidle")
            mobile.wait_for_timeout(2000)
            mobile.screenshot(path=str(OUT / "09-mobile.png"), full_page=True)

        if picked.get("twenty-questions"):
            page.goto(f"{args.web}/index.html?api={args.api}&match={picked['twenty-questions']}", wait_until="networkidle")
            page.wait_for_timeout(1500)
            page.evaluate("const s=document.getElementById('scrub'); s.value='10'; s.dispatchEvent(new Event('input'))")
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / "10-replay.png"), full_page=True)

        browser.close()
        if errors:
            print("页面报错：", errors[:5])
            return 1
    print(f"截图写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
