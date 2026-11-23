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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


import _demo  # noqa: E402
from _harness import api_get as get  # noqa: E402
from _harness import api_post as post  # noqa: E402
from _harness import launch_browser, wait_finished  # noqa: E402

OUT = ROOT / "docs" / "video"


def pick_codenames(api: str, lang: str = "zh") -> str:
    for item in get(api, "/archive?limit=100")["items"]:
        if item["game"] == "codenames" and item["status"] == "ended":
            return item["matchId"]
    created = post(
        api,
        "/matches",
        {
            "game": "codenames",
            "seed": 2026,
            "players": [{"id": p, "delayMs": 60} for p in _demo.names(lang, "codenames")],
        },
        lang=lang,
    )
    wait_finished(api, created["matchId"])
    return created["matchId"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8123")
    parser.add_argument("--season", default="demo-2026")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="界面语言与选手名")
    parser.add_argument("--out", default=str(OUT / "walkthrough.mp4"))
    args = parser.parse_args()
    names = {
        "duo": tuple(_demo.names(args.lang, "twenty-questions")),
        "director": tuple(_demo.directors(args.lang)),
    }
    note = "demo: close-up" if args.lang == "en" else "演示：切特写"

    OUT.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    codenames_id = pick_codenames(args.api, args.lang)

    # 录一条节奏舒服的直播：每步 2.4s，旁白、镜头和拖时间轴都能看清；
    # 太快会在录到一半时终局，片子里就只剩结束卡了。
    live = post(
        args.api,
        "/matches",
        {
            "game": "twenty-questions",
            "seed": 2027,
            "maxQuestions": 21,
            "players": [{"id": names["duo"][0], "delayMs": 2400}, {"id": names["duo"][1], "delayMs": 2400}],
        },
        lang=args.lang,
    )
    live_id = live["matchId"]

    video_dir = Path(tempfile.mkdtemp(prefix="melee-video-"))
    try:
        with sync_playwright() as p:
            browser = launch_browser(p)
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

            page.goto(f"{args.api}/watch.html?match={live_id}&lang={args.lang}", wait_until="networkidle")
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
                page.goto(f"{args.api}/watch.html?match={codenames_id}&lang={args.lang}", wait_until="networkidle")
                hold(2600)
                page.evaluate("document.getElementById('endcard')?.classList.remove('on')")
                page.evaluate(
                    """() => { const s = document.getElementById('scrub');
                    s.value = String(Math.floor(Number(s.max) * 0.7));
                    s.dispatchEvent(new Event('input')); }"""
                )
                hold(3800)

            page.goto(f"{args.api}/recap.html?match={codenames_id}&lang={args.lang}", wait_until="networkidle")
            hold(5000)

            page.goto(f"{args.api}/season.html?season={args.season}&lang={args.lang}", wait_until="networkidle")
            hold(4600)

            director_live = post(
                args.api,
                "/matches",
                {
                    "game": "twenty-questions",
                    "seed": 411,
                    "maxQuestions": 21,
                    "players": [
                        {"id": names["director"][0], "delayMs": 3000},
                        {"id": names["director"][1], "delayMs": 3000},
                    ],
                },
            )
            page.goto(
                f"{args.api}/director.html?match={director_live['matchId']}&lang={args.lang}", wait_until="networkidle"
            )
            hold(3600)
            page.click(".shot[data-shot='close']")
            page.fill("#note", note)
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
