from __future__ import annotations

from typing import Any

from ..engine.base import Move
from .base import AgentOutput, ThoughtSink


class ScriptedAgent:
    """按预设脚本出招的假选手。用于离线跑通整条链路。"""

    def __init__(self, name: str, script: list[Move]) -> None:
        self.name = name
        self._script = list(script)

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        if not self._script:
            move = Move(kind="guess", value="不存在的东西")
            return AgentOutput(move=move, raw=f"{move.kind}:{move.value}")
        move = self._script.pop(0)
        return AgentOutput(move=move, raw=f"{move.kind}:{move.value}")


def asked_pairs(observation: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (item["aspect"], item["value"])
        for item in observation["history"]
        if item["kind"] == "ask"
    }


def _guess_of(row: dict[str, Any]) -> AgentOutput:
    move = Move(kind="guess", value=row["name"])
    return AgentOutput(move=move, raw=f"guess:{move.value}")


class GreedyAgent:
    """确定性贪心选手：每次挑一个能把候选集切得最均匀的问题。

    它只看 observe() 给的公开信息，不接触 target，所以可以当作"合法选手"的基准。
    """

    name = "greedy"

    def __init__(self, name: str, catalog: list[dict[str, str]]) -> None:
        self.name = name
        self._catalog = catalog

    def _candidates(self, observation: dict[str, Any]) -> list[dict[str, str]]:
        kept = []
        for row in self._catalog:
            ok = True
            for item in observation["history"]:
                if item["kind"] != "ask":
                    continue
                hit = row.get(item["aspect"]) == item["value"]
                if item["answer"] == "yes" and not hit:
                    ok = False
                    break
                if item["answer"] == "no" and hit:
                    ok = False
                    break
            if ok:
                kept.append(row)
        return kept

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        remaining = self._candidates(observation)
        if len(remaining) == 1:
            return _guess_of(remaining[0])

        best = self._best_question(remaining, asked_pairs(observation), observation["aspects"])
        if best is None:
            return _guess_of(remaining[0])

        if on_thought is not None:
            on_thought(f"还剩 {len(remaining)} 个候选，挑一个切得最均匀的问题。")
        return AgentOutput(move=Move(kind="ask", value=best), raw=f"ask:{best}")

    @staticmethod
    def _best_question(
        remaining: list[dict[str, Any]],
        used: set[tuple[str, str]],
        aspects: dict[str, list[str]],
    ) -> str | None:
        best: tuple[float, str] | None = None
        for aspect, values in aspects.items():
            for value in values:
                if (aspect, value) in used:
                    continue
                hits = sum(1 for row in remaining if row.get(aspect) == value)
                if hits in (0, len(remaining)):
                    continue
                score = abs(len(remaining) / 2 - hits)
                if best is None or score < best[0]:
                    best = (score, f"{aspect}={value}")
        return best[1] if best else None


class BoardAgent:
    """棋盘类赛制的默认选手：从观察文本里找可用动作，确定性地挑一个。

    为什么不用 GreedyAgent：那个是按二十问的属性表写的，依赖 history/aspects，
    对棋类会直接 KeyError（实测）。

    "确定性"指同一份观察永远给出同一个动作——用动作文本的哈希决定索引，
    不依赖随机数与时间，保证重放可复现。
    """

    name = "board"

    def __init__(self, name: str, game_id: str = "") -> None:
        self.name = name
        self.game_id = game_id

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        observations = observation.get("observations") or []
        moves = extract_moves(observations)
        if not moves and self.game_id == "connect-four":
            moves = columns_from_board(observations)
        if not moves and self.game_id == "nim":
            moves = nim_moves(observations)
        if not moves:
            return AgentOutput(move=Move(kind="ask", value=""), raw="[no-available-moves]")

        index = _stable_index(observation.get("you", ""), moves)
        choice = moves[index]
        if on_thought is not None:
            on_thought(f"可用动作 {len(moves)} 个，选第 {index + 1} 个。")
        return AgentOutput(move=Move(kind="guess", value=choice), raw=f"choose:{choice}")


_MOVE_PATTERN = __import__("re").compile(r"'(\[[^\[\]']+\])'")
_MOVE_HINT = __import__("re").compile(r"(?:Available Moves|Valid moves|Valid targets|Available):\s*(.+)")


def extract_moves(observations: list[str]) -> list[str]:
    """从观察文本里抓可用动作。

    实测各赛制给动作的方式不同：
    - 井字棋 / 黑白棋 / 取石子：观察里带 `Available Moves: '[1]', '[2]'` 或 `Valid moves:`
    - 四子棋：只给棋盘（列号 0-6 就是可下位置），没有列表

    所以先找带 "Available Moves"/"Valid moves" 的那一行，找不到再返回空，
    由调用方按赛制兜底（四子棋走 `columns_from_board`）。
    """
    # 只看最后一条：观察是累积的，历史里的动作已经不可用。
    # （这个坑实测踩过：黑白棋从第 2 步起会拿到上一步的坐标，直接判非法。）
    latest = [str(observations[-1])] if observations else []

    found: list[str] = []
    for text in latest:
        for match in _MOVE_HINT.finditer(text):
            segment = match.group(1)
            for item in _MOVE_PATTERN.finditer(segment):
                value = item.group(1)
                if value not in found:
                    found.append(value)
    if found:
        return found

    for text in latest:
        for match in _MOVE_PATTERN.finditer(text):
            value = match.group(1)
            if value not in found:
                found.append(value)
    return found


def nim_moves(observations: list[str]) -> list[str]:
    """取石子：从 "pile 0: 3" 这样的行推出可行动作。

    合法动作是 `[堆号 数量]`；这里只生成"从每堆取 1 个"的保守方案，
    保证一定合法，不追求最优解（对手是演示用的）。
    """
    import re

    latest = str(observations[-1]) if observations else ""
    piles = [(int(idx), int(count)) for idx, count in re.findall(r"pile\s+(\d+):\s*(\d+)", latest)]
    return [f"[{idx} 1]" for idx, count in piles if count > 0]


def columns_from_board(observations: list[str], columns: int = 7) -> list[str]:
    """四子棋专用：从棋盘**最上面一行**推出还能下的列。

    实测棋盘长这样（空格分隔）：
        0 1 2 3 4 5 6
        -------------
        . . . . . . .
        ...
        . X . . O . .
    第一行是列号，之后是 6 行棋盘；最上面那行棋盘的空位就是可落子列。
    """
    import re

    rows: list[list[str]] = []
    for text in observations:
        for line in str(text).splitlines():
            cells = re.findall(r"[.XO]", line)
            if len(cells) == columns:
                rows.append(cells)
    if not rows:
        return []
    # rows[0] 是棋盘顶行（列号那行的数字不会被 [.XO] 匹配到）
    top = rows[0]
    return [f"[col {i}]" for i, cell in enumerate(top) if cell == "."]


def _stable_index(seed: str, moves: list[str]) -> int:
    if not moves:
        return 0
    total = 0
    for ch in seed:
        total = (total * 31 + ord(ch)) % 1_000_003
    return total % len(moves)


class CodenamesAgent:
    """代号赛制的默认选手。

    - 队长：从自己队的词里挑一个当线索（不选场上的词），数量报 1。
    - 队员：如果当前有线索，优先选「词面与线索沾边」的己方词——
      这里用最朴素的做法：按字符串相似度（共同字符数）挑，够用即可。
    """

    name = "codenames"

    def __init__(self, name: str) -> None:
        self.name = name

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        if observation.get("phase") == "hint":
            return self._act_captain(observation, on_thought)
        return self._act_operative(observation, on_thought)

    @staticmethod
    def _act_captain(
        observation: dict[str, Any], on_thought: ThoughtSink | None
    ) -> AgentOutput:
        colors = observation.get("secretColors") or []
        words = observation.get("words") or []
        my_team = observation.get("team")
        mine = [w for i, w in enumerate(words) if i < len(colors) and colors[i] == my_team]
        # 线索不能是场上的词：用「目标词 + 一个后缀」造一个关联词。
        base = mine[0] if mine else "提示"
        clue = f"{base}相关"
        if on_thought is not None:
            on_thought(f"队里有 {len(mine)} 个词要猜，先给一个线索。")
        return AgentOutput(move=Move(kind="guess", value=f"hint:{clue} 1"), raw=f"hint:{clue} 1")

    @staticmethod
    def _act_operative(
        observation: dict[str, Any], on_thought: ThoughtSink | None
    ) -> AgentOutput:
        # 队员：拿不到颜色，只能按线索相似度猜
        hint = observation.get("hint") or {}
        clue = str(hint.get("word", ""))
        board = observation.get("publicBoard") or []
        candidates = [row["word"] for row in board if not row.get("revealed")]
        if not candidates:
            return AgentOutput(move=Move(kind="ask", value=""), raw="[no-candidates]")

        best = max(candidates, key=lambda word: sum(1 for ch in set(clue) if ch in word))
        if on_thought is not None:
            on_thought(f"线索是「{clue}」，从 {len(candidates)} 个未翻开的词里挑最像的。")
        return AgentOutput(move=Move(kind="guess", value=f"pick:{best}"), raw=f"pick:{best}")
