#!/usr/bin/env python
"""全页面截图：桌面 + 移动，五个页面各一张，另外拍关键状态。

用法：
    python scripts/capture_audit_shots.py --api http://127.0.0.1:8210 --out .tmp/shots
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

PAGES = ["index.html", "watch.html", "season.html", "recap.html", "director.html"]


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8210")
    parser.add_argument("--out", default=".tmp/shots")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    archive = get_json(f"{args.api}/archive?limit=6")["items"]
    ended = next((m for m in archive if m["status"] == "ended"), None)
    live = next((m for m in archive if m["status"] == "live"), None)
    seasons = get_json(f"{args.api}/seasons")["seasons"]
    season_id = seasons[0]["seasonId"] if seasons else ""

    solved = {
        "index.html": "",
        "watch.html": f"?match={ended['matchId']}" if ended else "",
        "season.html": f"?season={season_id}" if season_id else "",
        "recap.html": f"?match={ended['matchId']}" if ended else "",
        "director.html": f"?match={live['matchId']}" if live else "",
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, viewport in (("desktop", {"width": 1440, "height": 900}), ("mobile", {"width": 390, "height": 844})):
            page = browser.new_page(viewport=viewport, device_scale_factor=1)
            errors: list[str] = []
            page.on("pageerror", lambda exc, sink=errors: sink.append(str(exc)))
            page.on(
                "console",
                lambda msg, sink=errors: sink.append(f"console:{msg.text}") if msg.type == "error" else None,
            )
            for name in PAGES:
                page.goto(f"{args.api}/{name}{solved[name]}", wait_until="networkidle")
                page.wait_for_timeout(1200)
                page.screenshot(path=str(out / f"{label}-{name.replace('.html', '')}.png"), full_page=name != "watch.html")
            if errors:
                print(f"{label} 页面错误：", errors[:10])
            page.close()
        browser.close()

    print(f"截图写入 {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
