#!/usr/bin/env python
"""生成一套可对外演示的数据。

用法：
    MELEE_DB=/tmp/demo.db python scripts/serve.py --port 8099   # 终端 1
    python scripts/seed_demo.py --api http://127.0.0.1:8099     # 终端 2

产出：
- 六种赛制各一场，全部跑到终局（用于赛事库与战报截图）；
- 一个 4 人赛季，先跑 3 场，留 3 场待开；
- 一场慢节奏直播（每步 5.2 秒），用于直播/导播台截图。

脚本会等棋类对局真的终局才返回；超时会如实打印"未在超时内结束"。
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from typing import Any

# 每个赛制用一套贴题的名字，截图里读起来才像节目而不是测试数据。
ROSTERS = {
    "twenty-questions": ["灰隼", "砚台"],
    "codenames": ["红队队长", "蓝队队长", "红队队员", "蓝队队员"],
    "tic-tac-toe": ["左撇子", "右撇子"],
    "connect-four": ["直线", "斜线"],
    "othello": ["黑子", "白子"],
    "nim": ["取一", "取二"],
}


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
        time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--season", default="demo-2026")
    parser.add_argument("--skip-live", action="store_true", help="不新建直播场")
    args = parser.parse_args()

    created: dict[str, str] = {}

    order = ("codenames", "tic-tac-toe", "nim", "connect-four", "othello", "twenty-questions")
    for seed, game in enumerate(order):
        names = ROSTERS[game]
        match = post(
            args.api,
            "/matches",
            {
                "game": game,
                "seed": 2027 + seed,
                "players": [{"id": name, "delayMs": 60} for name in names],
                "maxQuestions": 21,
            },
        )
        created[game] = match["matchId"]
        finished = wait_finished(args.api, match["matchId"])
        print(f"{game:16s} {match['matchId']} {'已终局' if finished else '未在超时内结束'}")

    try:
        season = post(
            args.api,
            "/seasons",
            {
                "seasonId": args.season,
                "name": "2026 秋季赛",
                "game": "twenty-questions",
                "players": [{"id": name} for name in ("灰隼", "砚台", "观测者", "审核员")],
            },
        )
        run = post(args.api, f"/seasons/{args.season}/run?maxFixtures=3", {})
        print(f"\n赛季 {season['seasonId']}：{len(season['fixtures'])} 场排期，本次跑完 {run['played']} 场")
    # aqg: top-level boundary
    except Exception as exc:  # noqa: BLE001 - 重复灌数据时赛季已存在  # aqg: top-level boundary
        print(f"\n赛季跳过：{exc}")

    if not args.skip_live:
        live = post(
            args.api,
            "/matches",
            {
                "game": "twenty-questions",
                "seed": 2027,
                "maxQuestions": 21,
                "players": [{"id": "灰隼", "delayMs": 5200}, {"id": "砚台", "delayMs": 5200}],
            },
        )
        created["live"] = live["matchId"]
        print(f"直播中           {live['matchId']}（每步 5.2 秒）")

    print("\nmatchId 汇总：")
    for game, match_id in created.items():
        print(f"  {game:16s} {match_id}")
    print(f"  {'seasonId':16s} {args.season}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
