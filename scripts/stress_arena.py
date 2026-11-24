#!/usr/bin/env python
"""并发对抗压测：用全部注册赛制验证调度器不丢场、不卡死。

这不是"模型智商测试"：脚本选手的 `delayMs` 模拟真实 AI 的思考节奏，
重点验证队列、worker、SSE 写入与终态收尾在并发下仍然收敛。
真实 LLM 路径由提示词协议测试 + 单场 live 验收覆盖。

用法（服务必须开 MELEE_RATELIMIT=off，否则建场会被 10/minute 正常限流）：
    python scripts/stress_arena.py --api http://127.0.0.1:8910 \
        --copies 3 --timeout 90 --out artifacts/stress-report.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def request(base: str, path: str, *, method: str = "GET", body: dict | None = None, token: str = "") -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, {"detail": exc.read().decode()[:200]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--copies", type=int, default=3, help="每个赛制并发几场")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--token", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    base = args.api.rstrip("/")
    token = args.token

    status, games_payload = request(base, "/games", token=token)
    if status != 200:
        print(f"FAIL cannot read /games: {status} {games_payload}")
        return 1
    games = games_payload["games"]
    print(f"service: {base}")
    print(f"games: {len(games)}; copies per game: {args.copies}; total: {len(games) * args.copies}")

    started = time.monotonic()
    errors: list[dict] = []
    matches: list[dict] = []

    def create_one(game: dict[str, object], copy_index: int) -> None:
        bounds = game.get("players") or [2, 2]
        player_count = int(bounds[0]) if isinstance(bounds, list) else 2
        player_count = max(2, player_count)
        body = {
            "game": game["gameId"],
            "seed": 1000 + copy_index,
            "players": [{"id": f"P{i + 1}", "delayMs": 55} for i in range(player_count)],
        }
        if game["gameId"] == "twenty-questions":
            body["maxQuestions"] = 8
        code, payload = request(base, "/matches", method="POST", body=body, token=token)
        if code != 201:
            errors.append({"game": game["gameId"], "copy": copy_index, "stage": "create", "status": code, "detail": payload})
            return
        matches.append({"game": game["gameId"], "copy": copy_index, "matchId": payload["matchId"], "createdAt": time.monotonic()})

    # 建场并发但保留每个赛制的副本序号，便于按赛制统计。
    with ThreadPoolExecutor(max_workers=min(16, len(games) * args.copies)) as pool:
        futures = [pool.submit(create_one, game, copy) for game in games for copy in range(args.copies)]
        for future in futures:
            future.result()

    print(f"created: {len(matches)}; create errors: {len(errors)}")
    pending = {match["matchId"]: match for match in matches}
    finished: dict[str, dict] = {}
    state_errors = 0
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        for match_id, record in list(pending.items()):
            code, payload = request(base, f"/matches/{match_id}/state", token=token)
            if code != 200:
                state_errors += 1
                continue
            # pending 场也会返回 200；只有 finished=true 才算收尾。
            if payload.get("finished"):
                record["finishedAt"] = time.monotonic()
                record["events"] = payload.get("events", 0)
                record["winner"] = payload.get("winner")
                finished[match_id] = record
                pending.pop(match_id, None)
        if pending:
            time.sleep(0.25)

    for match_id, record in pending.items():
        errors.append({"game": record["game"], "copy": record["copy"], "stage": "timeout", "status": "stuck", "detail": match_id})
    for record in finished.values():
        record["durationMs"] = round((record["finishedAt"] - record["createdAt"]) * 1000)

    completed_games = sorted({record["game"] for record in finished.values()})
    missing_games = sorted({game["gameId"] for game in games} - set(completed_games))
    durations = [record["durationMs"] for record in finished.values()]
    p95 = round(statistics.quantiles(durations, n=20)[-1], 1) if len(durations) >= 2 else (durations[0] if durations else 0)
    report = {
        "api": base,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "registeredGames": len(games),
        "wallMs": round((time.monotonic() - started) * 1000),
        "copiesPerGame": args.copies,
        "created": len(matches),
        "completed": len(finished),
        "stuck": len(pending),
        "createErrors": len([e for e in errors if e["stage"] == "create"]),
        "stateErrors": state_errors,
        "scenarioCoverage": f"{len(completed_games)}/{len(games)}",
        "missingGames": missing_games,
        "durationMs": {"p50": round(statistics.median(durations), 1) if durations else 0, "p95": p95, "max": max(durations) if durations else 0},
        "events": sum(int(record.get("events") or 0) for record in finished.values()),
        "errors": errors,
    }
    print(json.dumps({k: report[k] for k in ("created", "completed", "stuck", "scenarioCoverage", "durationMs", "events")}, ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"report: {out}")
    ok = not errors and not pending and not missing_games
    print("STRESS PASS" if ok else "STRESS FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
