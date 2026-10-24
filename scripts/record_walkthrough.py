#!/usr/bin/env python
"""录制全流程演示视频（真实浏览器，真实比赛）。

做法：Playwright 以 1440×900 录制 WebM，然后 ffmpeg 转成 H.264 mp4。
不剪辑、不加字幕卡——画面里的每个状态都是当时服务真实返回的。

用法：
    MELEE_DB=/tmp/melee-demo.db python scripts/serve.py --port 8123 &
    python scripts/seed_demo.py --api http://127.0.0.1:8123
    python scripts/record_walkthrough.py --api http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "video"


def get(api: str, path: str) -> dict:
    with urllib.request.urlopen(f"{api}{path}", timeout=15) as response:
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


def wait_finished(api: str, match_id: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if any(e["type"] == "match.ended" for e in get(api, f"/matches/{match_id}/events")["events"]):
            return
        time.sleep(0.5)


def pick_codenames(api: str) -> str:
    for item in get(api, "/archive?limit=100")["items"]:
        if item["game"] == "codenames" and item["status"] == "ended":
            return item["matchId"]
    created = post(
        api,
        "/matches",
        {"game": "codenames", "seed": 2026, "players": [{"id": p, "delayMs": 60} for p in ("A", "B", "C", "D")]},
    )
    wait_finished(api, created["matchId"])
    return created["matchId"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8123")
    parser.add_argument("--season", default="demo-2026")
    parser.add_argument("--out", default=str(OUT / "walkthrough.mp4"))
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    codenames_id = pick_codenames(args.api)

    # 录一条节奏舒服的直播：每步 900ms，旁白/镜头都能看清。
    live = post(
        args.api,
        "/matches",
        {
            "game": "twenty-questions",
            "seed": 2027,
            "maxQuestions": 21,
            "players": [{"id": "灰隼", "delayMs": 900}, {"id": "砚台", "delayMs": 900}],
        },
    )
    live_id = live["matchId"]

    video_dir = Path(tempfile.mkdtemp(prefix="melee-video-"))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(video_dir),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()

            def hold(ms: int) -> None:
                page.wait_for_timeout(ms)

            page.goto(f"{args.api}/", wait_until="networkidle")
            hold(3800)

            page.goto(f"{args.api}/watch.html?match={live_id}", wait_until="networkidle")
            hold(6500)
            page.evaluate(
                """() => { const s = document.getElementById('scrub');
                s.value = String(Math.floor(Number(s.max) * 0.45));
                s.dispatchEvent(new Event('input')); }"""
            )
            hold(3200)
            page.click("#btnPause")
            hold(1200)
            page.click("#btnPause")
            hold(2000)
            page.click("#btnLive")
            hold(2600)

            if codenames_id:
                page.goto(f"{args.api}/watch.html?match={codenames_id}", wait_until="networkidle")
                hold(2600)
                page.evaluate("document.getElementById('endcard')?.classList.remove('on')")
                page.evaluate(
                    """() => { const s = document.getElementById('scrub');
                    s.value = String(Math.floor(Number(s.max) * 0.7));
                    s.dispatchEvent(new Event('input')); }"""
                )
                hold(3800)

            page.goto(f"{args.api}/recap.html?match={codenames_id}", wait_until="networkidle")
            hold(5000)

            page.goto(f"{args.api}/season.html?season={args.season}", wait_until="networkidle")
            hold(4600)

            director_live = post(
                args.api,
                "/matches",
                {
                    "game": "twenty-questions",
                    "seed": 411,
                    "maxQuestions": 21,
                    "players": [{"id": "观测者", "delayMs": 9000}, {"id": "审核员", "delayMs": 9000}],
                },
            )
            page.goto(f"{args.api}/director.html?match={director_live['matchId']}", wait_until="networkidle")
            hold(2600)
            page.click(".shot[data-shot='close']")
            page.fill("#note", "演示：切特写")
            hold(600)
            page.click("#push")
            hold(2600)

            context.close()
            browser.close()

        webm = sorted(video_dir.glob("*.webm"), key=lambda path: path.stat().st_mtime)[-1]
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            print("没有 ffmpeg，无法转码", file=sys.stderr)
            return 1
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(webm),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "22",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(out_path),
            ],
            check=True,
        )
    finally:
        shutil.rmtree(video_dir, ignore_errors=True)

    print(f"视频写入 {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
