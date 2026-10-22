#!/usr/bin/env python
"""生成演示数据：六种赛制各一场 + 一个带排期的赛季。

用法：
    MELEE_DB=/tmp/demo.db python scripts/serve.py --port 8099   # 终端 1
    python scripts/seed_demo.py --api http://127.0.0.1:8099     # 终端 2

输出可直接用于截图与手工验收；脚本会等到棋类对局终局再返回。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any


def post(api: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def get(api: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{api}{path}", timeout=30) as response:
        return json.load(response)


def wait_finished(api: str, match_id: str, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = get(api, f"/matches/{match_id}/events")["events"]
        if any(event["type"] == "match.ended" for event in events):
            return True
        time.sleep(1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    args = parser.parse_args()

    created: dict[str, str] = {}

    # 直播中的一场：慢节奏，方便看
    live = post(args.api, "/matches", {
        "game": "twenty-questions", "seed": 2027,
        "players": [{"id": "灰隼", "delayMs": 6000}, {"id": "砚台", "delayMs": 6000}],
        "maxQuestions": 21,
    })
    created["twenty-questions"] = live["matchId"]

    quick = [
        ("codenames", [{"id": p, "delayMs": 200} for p in "ABCD"]),
        ("tic-tac-toe", [{"id": "X", "delayMs": 200}, {"id": "O", "delayMs": 200}]),
        ("nim", [{"id": "A", "delayMs": 200}, {"id": "B", "delayMs": 200}]),
        ("connect-four", [{"id": "A", "delayMs": 200}, {"id": "B", "delayMs": 200}]),
        ("othello", [{"id": "A", "delayMs": 200}, {"id": "B", "delayMs": 200}]),
    ]
    for game, players in quick:
        match = post(args.api, "/matches", {"game": game, "seed": 2027, "players": players})
        created[game] = match["matchId"]
        finished = wait_finished(args.api, match["matchId"])
        print(f"{game:16s} {match['matchId']} {'已终局' if finished else '未在超时内结束'}")

    season = post(args.api, "/seasons", {
        "seasonId": "demo-2026",
        "name": "2026 秋季赛",
        "players": [{"id": pid} for pid in "ABCD"],
        "startAt": time.time() + 3600,
        "intervalMinutes": 30,
    })
    print(f"\n赛季 {season['seasonId']}，{len(season['fixtures'])} 场排期")
    print("\nmatchId 汇总：")
    for game, match_id in created.items():
        print(f"  {game:16s} {match_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
