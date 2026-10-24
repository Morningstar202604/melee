"""离线跑一整局，把事件打印成可读时间线。

用法：
    python -m services.match.demo            # 默认种子 7，双贪心选手
    python -m services.match.demo --seed 42 --db /tmp/x.db
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agents.scripted import GreedyAgent
from .engine.twenty_questions import CATALOG, TwentyQuestions
from .eventlog import EventLog, replay
from .narrator import RuleNarrator
from .runner import Runner


def run(seed: int, db_path: str) -> dict:
    game = TwentyQuestions()
    players = ["A", "B"]
    state = game.init(seed=seed, players=players, config={"maxQuestions": 21})

    log = EventLog.open(db_path, f"m_{seed}")
    log.create_match(game=game.game_id, seed=seed, players=players)

    agents = {
        "A": GreedyAgent("A", CATALOG),
        "B": GreedyAgent("B", CATALOG),
    }
    runner = Runner(game, log, agents, narrator=RuleNarrator())
    result = runner.play(state)

    log.assert_contiguous()
    events = log.read()
    log.close()
    return {"result": result, "events": events, "view": replay(events)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--db", default="data/match.db")
    args = parser.parse_args()

    run_result = run(args.seed, args.db)
    result = run_result["result"]
    events = run_result["events"]

    print(f"种子 {args.seed} · 共 {len(events)} 条事件 · {result.turns} 回合")
    print("-" * 72)
    for event in events:
        text = _line_of(event)
        if text:
            print(text)

    print()
    print("视图（由事件折叠得出）：", json.dumps(
        {k: v for k, v in run_result["view"].items() if k != "turns"},
        ensure_ascii=False,
    ))
    print(f"落库：{Path(args.db).resolve()}")
    return 0


def _line_of(event: dict) -> str:
    kind = event["type"]
    payload = event["payload"]
    who = event.get("player", "")
    if kind == "action.applied":
        action = payload["action"]
        effects = "; ".join(_effect_text(eff) for eff in payload["effects"] if eff["kind"] in ("answer", "guess"))
        return f"[{event['seq']:>2}] {who} {action['kind']:<5} {effects}"
    if kind == "narration.line":
        return f"     └ 解说：{payload['text']}"
    if kind == "match.ended":
        return f"{'-' * 72}\n结束：胜者 {payload['winner']} · {payload['reason']}"
    return ""


def _effect_text(eff: dict) -> str:
    if eff["kind"] == "answer":
        mark = "是" if eff["answer"] == "yes" else "否"
        return f"{eff['aspect']}={eff['value']} → {mark}（排除 {eff['narrowed']}）"
    return f"报答案 {eff['value']} → {'中' if eff['correct'] else '错'}"


if __name__ == "__main__":
    sys.exit(main())
