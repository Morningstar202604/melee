#!/usr/bin/env python
"""Externally-observable acceptance checks: every promise verified from outside.

Design rule: this script only talks HTTP to a running service. If a claim can
only be verified by reading internals, it is not a shipped feature yet.

Usage:  python scripts/acceptance.py --api http://127.0.0.1:8099
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request

PASS: list = []
FAIL: list = []
HAN = re.compile(r"[一-鿿]")


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append((name, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" | " + str(detail)[:140]) if detail else ""))
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    args = parser.parse_args()
    base = args.api.rstrip("/")

    def get(path, lang=None):
        headers = {"Accept-Language": lang} if lang else {}
        request = urllib.request.Request(base + path, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())

    def post(path, body=None, lang=None, _retries: int = 3):
        """POST with 429-aware retry.

        建场有 10/minute 限流。验收脚本连着建几场必然撞上，这不是缺陷，
        是产品在保护自己——脚本按 Retry-After 等待后重试，而不是报红。
        """
        import urllib.error

        headers = {"Content-Type": "application/json"}
        if lang:
            headers["Accept-Language"] = lang
        data = json.dumps(body).encode() if body is not None else b""
        for attempt in range(_retries):
            request = urllib.request.Request(
                base + path, data=data, headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.loads(response.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == _retries - 1:
                    raise
                wait = int(exc.headers.get("Retry-After") or 30)
                print(f"    (rate limited, waiting {wait}s)")
                time.sleep(min(wait, 65))
        raise RuntimeError("unreachable")

    def wait_ended(match_id, timeout=180):
        deadline = time.time() + timeout
        while time.time() < deadline:
            events = get("/matches/" + match_id + "/events")["events"]
            if any(e["type"] == "match.ended" for e in events):
                return events
            time.sleep(1)
        return []

    print("== service and metadata ==")
    check("health ok", get("/health").get("status") == "ok")
    games = get("/games")["games"]
    check("12 game types registered", len(games) == 12, len(games))
    check("dynamic engine present (othello)", any(g["gameId"] == "othello" for g in games))
    check("API bilingual",
          get("/games", "en")["games"][0]["label"] != get("/games", "zh")["games"][0]["label"])

    print()
    print("== seed confidentiality ==")
    secure = post("/matches", {"game": "twenty-questions", "players": [{"id": "A"}, {"id": "B"}]})
    check("secure mode hides seed", secure.get("seed") is None, secure.get("seed"))
    check("secure mode not reproducible", secure.get("reproducible") is False)
    replay = post("/matches", {"game": "twenty-questions", "seed": 42,
                               "players": [{"id": "C"}, {"id": "D"}]})
    check("replay mode publishes seed", replay.get("seed") == 42)
    events = wait_ended(secure["matchId"], 120)
    created = next((e for e in events if e["type"] == "match.created"), None)
    check("event stream has no seed", created is not None and "seed" not in created["payload"])

    print()
    print("== match pipeline ==")
    match_id = post("/matches", {"game": "tic-tac-toe",
                                 "players": [{"id": "X1"}, {"id": "X2"}]})["matchId"]
    events = wait_ended(match_id, 120)
    kinds = {e["type"] for e in events}
    check("match reaches an end", "match.ended" in kinds)
    check("narration present", "narration.line" in kinds)
    check("director cues present", "director.cue" in kinds)
    recap = get("/matches/" + match_id + "/recap")
    check("recap fact-check clean", recap["problems"] == [], recap["problems"])
    check("recap has highlights", len(recap["highlights"]) > 0, len(recap["highlights"]))
    check("clip manifest lists clips", get("/matches/" + match_id + "/clips")["clipCount"] > 0)

    print()
    print("== bilingual content ==")
    en = [e["payload"]["text"] for e in get("/matches/" + match_id + "/events", "en")["events"]
          if e["type"] == "narration.line"]
    zh = [e["payload"]["text"] for e in get("/matches/" + match_id + "/events", "zh")["events"]
          if e["type"] == "narration.line"]
    check("narration differs by language", en != zh)
    check("English narration has no CJK", not any(HAN.search(t) for t in en), en[:1])

    print()
    print("== portable export / import ==")
    document = get("/matches/" + match_id + "/export")
    check("export has events", len(document["events"]) > 0, len(document["events"]))
    check("export has fingerprint", len(document["fingerprint"]) == 16)
    imported = post("/matches/import", document)
    check("import returns a new id", imported["matchId"] != match_id)
    first = get("/matches/" + match_id + "/recap")
    second = get("/matches/" + imported["matchId"] + "/recap")
    check("imported replay reproduces the result",
          first["winner"] == second["winner"] and first["turns"] == second["turns"])

    print()
    print("== season ==")
    season = post("/seasons", {"seasonId": "acceptance-" + str(int(time.time())),
                               "name": "Acceptance", "game": "tic-tac-toe",
                               "players": [{"id": p} for p in ("P1", "P2", "P3", "P4")],
                               "doubleRoundRobin": True})
    check("double round-robin schedules 12 fixtures", len(season["fixtures"]) == 12,
          len(season["fixtures"]))
    post("/seasons/" + season["seasonId"] + "/run", {})
    detail = get("/seasons/" + season["seasonId"])
    played = sum(1 for f in detail["fixtures"] if f["played"])
    check("all 12 fixtures played", played == 12, played)
    check("rivalries populated", len(detail["rivalries"]) == 6, len(detail["rivalries"]))

    print()
    print("== codenames fairness and content ==")
    # 建场是 10/minute 限流：6 场连发会触发 429（实测）。按真实节奏分批。
    firsts = []
    for index in range(6):
        if index and index % 4 == 0:
            time.sleep(62)
        created_match = post("/matches", {
            "game": "codenames", "seed": 100 + index,
            "players": [{"id": n} for n in ("r1", "b1", "r2", "b2")],
        })
        # 事件是异步写的：建场后需等首条 state.changed 出现，不能立刻查。
        state = None
        deadline = time.time() + 30
        while time.time() < deadline and state is None:
            state = next((e for e in get("/matches/" + created_match["matchId"] + "/events")["events"]
                          if e["type"] == "state.changed"), None)
            if state is None:
                time.sleep(1)
        if state:
            firsts.append(state["payload"]["publicState"].get("turn"))
    check("first team rotates across seeds", len(set(f for f in firsts if f)) > 1, firsts)

    time.sleep(8)
    codenames = post("/matches", {
        "game": "codenames", "seed": 2027,
        "players": [{"id": n} for n in ("Red Spymaster", "Blue Spymaster",
                                        "Red Operative", "Blue Operative")],
    }, "en")
    codenames_events = wait_ended(codenames["matchId"], 180)
    board = next((e for e in codenames_events
                  if e["type"] == "state.changed"
                  and "board" in (e["payload"].get("publicState") or {})), None)
    words = [c["word"] for c in board["payload"]["publicState"]["board"]] if board else []
    check("English board has no CJK", bool(words) and not any(HAN.search(w) for w in words), words[:3])
    commentary = [e["payload"]["text"] for e in codenames_events if e["type"] == "narration.line"]
    check("English commentary has no CJK",
          bool(commentary) and not any(HAN.search(t) for t in commentary), commentary[:1])
    end = next((e for e in codenames_events if e["type"] == "match.ended"), None)
    check("codenames never declares the neutral side the winner",
          end is not None and end["payload"]["winner"] != "neutral",
          end["payload"]["winner"] if end else None)

    print()
    print("== agent harness ==")
    card = get("/.well-known/agent-card.json")
    check("A2A card present", card["name"] == "melee")
    check("A2A card lists skills", len(card["skills"]) >= 4, len(card["skills"]))
    check("ops snapshot ok", get("/ops/health")["status"] == "ok")

    print()
    print(f"RESULT {len(PASS)} pass / {len(FAIL)} fail")
    for name, detail in FAIL:
        print("  FAILED:", name, "|", detail)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
