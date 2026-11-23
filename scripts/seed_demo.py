#!/usr/bin/env python
"""生成一套可对外演示的数据。

用法：
    MELEE_DB=/tmp/demo.db python scripts/serve.py --port 8099   # 终端 1
    python scripts/seed_demo.py --api http://127.0.0.1:8099     # 终端 2

产出：
- 全部注册赛制各一场，全部跑到终局（用于赛事库与战报截图）；
- 一个 4 人赛季，先跑 3 场，留 3 场待开；
- 一场慢节奏直播（每步 5.2 秒），用于直播/导播台截图。

脚本会等棋类对局真的终局才返回；超时会如实打印"未在超时内结束"。
"""

from __future__ import annotations

import argparse

from _demo import ROSTERS, SEASON_PLAYERS  # noqa: E402
from _harness import api_post as post  # noqa: E402
from _harness import wait_finished  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--season", default="demo-2026")
    parser.add_argument("--skip-live", action="store_true", help="不新建直播场")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="演示数据的选手名与赛季文案语言")
    args = parser.parse_args()
    roster = ROSTERS["en"] if args.lang == "en" else ROSTERS["zh"]
    season_name = "Autumn 2026" if args.lang == "en" else "2026 秋季赛"
    season_players = SEASON_PLAYERS["en" if args.lang == "en" else "zh"]

    created: dict[str, str] = {}

    # 顺序按观赏性排，截图与演示视频都按这个顺序取第一屏
    order = (
        "codenames",
        "tic-tac-toe",
        "nim",
        "connect-four",
        "othello",
        "reverse-tic-tac-toe",
        "kuhn-poker",
        "game-of-pure-strategy",
        "simple-tak",
        "wild-tic-tac-toe",
        "crusade",
        "twenty-questions",
    )
    for seed, game in enumerate(order):
        names = roster[game]
        match = post(
            args.api,
            "/matches",
            {
                "game": game,
                "seed": 2027 + seed,
                "players": [{"id": name, "delayMs": 60} for name in names],
                "maxQuestions": 21,
            },
            lang=args.lang,
        )
        created[game] = match["matchId"]
        finished = wait_finished(args.api, match["matchId"], raise_on_timeout=False)
        print(f"{game:16s} {match['matchId']} {'已终局' if finished else '未在超时内结束'}")

    try:
        season = post(
            args.api,
            "/seasons",
            {
                "seasonId": args.season,
                "name": season_name,
                "game": "twenty-questions",
                "players": [{"id": name} for name in season_players],
            },
            lang=args.lang,
        )
        run = post(args.api, f"/seasons/{args.season}/run?maxFixtures=3", {}, lang=args.lang)
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
                "players": [{"id": name, "delayMs": 5200} for name in roster["twenty-questions"]],
            },
            lang=args.lang,
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
