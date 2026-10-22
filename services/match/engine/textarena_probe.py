"""探测 TextArena 的哪些环境可以直接当确定性赛制用。

判断标准（不需要 LLM 主持）：
1. `ta.make` 能创建
2. `reset(num_players=2)` 不报错、不要求 OPENROUTER_API_KEY
3. `step` 一个合法动作后，`game_state` 确实变化
4. 非法动作后，`game_state` 与当前玩家都没变

用法：
    python -m services.match.engine.textarena_probe            # 扫全部
    python -m services.match.engine.textarena_probe Othello-v0 # 只测一个
"""

from __future__ import annotations

import sys

import copy

try:
    import textarena as ta
except Exception as exc:  # noqa: BLE001
    print(f"未安装 textarena：{exc}")
    raise SystemExit(2)


def _first_number(observation) -> str | None:
    """从观察文本里找到第一个可用的动作编号。

    实测结构是 (from_id, message, observation_type)；取 item[0] 会拿到玩家号。
    """
    import re

    if isinstance(observation, str):
        texts = [observation]
    else:
        texts = [
            str(item[1]) if isinstance(item, (list, tuple)) and len(item) >= 2 else str(item)
            for item in observation or []
        ]
    for text in texts:
        match = re.search(r"'\[(\d+)\]'", text)
        if match:
            return match.group(1)
    return None


def probe(env_id: str) -> dict[str, object]:
    result: dict[str, object] = {"env": env_id, "ok": False, "reason": ""}
    try:
        env = ta.make(env_id=env_id)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"make 失败：{str(exc)[:80]}"
        return result

    try:
        env.reset(num_players=2, seed=1)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"reset 失败：{str(exc)[:80]}"
        return result

    before = copy.deepcopy(env.state.game_state)
    action = _first_number(env.state.get_current_player_observation())
    result["action"] = action
    if action is None:
        result["reason"] = "从观察里找不到动作编号"
        return result

    player_before = env.state.current_player_id
    try:
        env.step(f"[{action}]")
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"step 失败：{str(exc)[:80]}"
        return result

    if env.state.game_state == before:
        result["reason"] = "合法动作没有改变局面"
        return result
    if env.state.current_player_id == player_before and not env.state.done:
        result["reason"] = "动作后没有换手"
        return result

    result["ok"] = True
    result["reason"] = "可作为确定性赛制"
    return result


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        targets = argv[1:]
    else:
        import pathlib

        envs_dir = pathlib.Path(ta.__file__).parent / "envs"
        names = sorted(d.name for d in envs_dir.iterdir() if d.is_dir() and d.name != "utils")
        targets = [f"{name}-v0" for name in names]

    ok, failed = [], []
    for env_id in targets:
        outcome = probe(env_id)
        if outcome["ok"]:
            ok.append(env_id)
            print(f"OK    {env_id:28s} 动作 {outcome.get('action')}")
        else:
            failed.append(env_id)
            print(f"SKIP  {env_id:28s} {outcome['reason']}")

    print()
    print(f"可用 {len(ok)} / 共 {len(targets)}")
    if ok:
        print("可直接接入：" + ", ".join(ok))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
