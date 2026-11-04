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

    信息来源必须与 LLM 选手对等：优先用观察里的 `candidates`（公开规则），
    构造参数里的 catalog 只是兼容旧调用的兜底。它不接触 target，
    所以可以当作"合法选手"的基准。
    """

    name = "greedy"

    def __init__(self, name: str, catalog: list[dict[str, str]]) -> None:
        self.name = name
        self._catalog = catalog

    def _candidates(self, observation: dict[str, Any]) -> list[dict[str, str]]:
        # 公平性：候选名单从观察里拿，与 LLM 选手看到的信息完全一致。
        # 旧实现直接用构造时注入的 CATALOG——那是选手侧不该有的特权信息。
        names = observation.get("candidates")
        rows = (
            [row for row in self._catalog if row["name"] in set(names)]
            if names
            else self._catalog
        )
        kept = []
        for row in rows:
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
        # 以引擎给的 legal 为准：它是权威动作集（含 [pass] 这类特殊动作），
        # 自己再从观察文本里解析是重复劳动，而且会漏掉引擎新加的动作。
        moves = [str(item) for item in (legal or [])]
        if not moves:
            moves = extract_moves(observations)
        if not moves and self.game_id == "connect-four":
            moves = columns_from_board(observations)
        if not moves and self.game_id == "nim":
            moves = nim_moves(observations)
        # 无子可下：上游会在 step 里自动跳过，选手这一手唯一正确的动作就是 pass。
        # 只认上游自己的措辞，不做语义猜测。
        if not moves and _says_skip(observations):
            return AgentOutput(move=Move(kind="guess", value="[pass]"), raw="choose:[pass]")
        if not moves:
            return AgentOutput(move=Move(kind="ask", value=""), raw="[no-available-moves]")

        index = _stable_index(observation.get("you", ""), moves)
        choice = moves[index]
        if on_thought is not None:
            on_thought(f"可用动作 {len(moves)} 个，选第 {index + 1} 个。")
        return AgentOutput(move=Move(kind="guess", value=choice), raw=f"choose:{choice}")


# 上游各环境的写法不统一：有的带引号（`'[4]'`），有的不带（`[0], [1]`），
# 还有带字母的坐标（`[a2 b4]`、`[X 4]`）。只抓方括号本体，引号可有可无。
_MOVE_PATTERN = __import__("re").compile(r"\[[^\[\]']+\]")
_MOVE_HINT = __import__("re").compile(r"(?:Available Moves|Valid moves|Valid targets|Available|available actions are|available actions|Valid actions are|Valid actions):\s*(.+)")


_SKIP_PHRASES = __import__("re").compile(
    r"(no valid moves|no legal moves|may have to skip|had to skip|must skip)", __import__("re").I
)


def _says_skip(observations: list[str]) -> bool:
    """观察里是否明确说了"你没有可下的子、需要跳过"。

    只认上游自己写的那几句话，不做语义猜测——猜错会让选手白白弃权。
    与 textarena_adapter._says_no_moves 同源，但选手层不能 import 适配器
    （会形成反向依赖），所以各留一份小的。
    """
    return any(_SKIP_PHRASES.search(str(text)) for text in observations[-2:])


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
            for value in _MOVE_PATTERN.findall(match.group(1)):
                if value not in found:
                    found.append(value)
    if found:
        return found

    for text in latest:
        for value in _MOVE_PATTERN.findall(text):
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
    """代号赛制的默认选手（确定性、零模型依赖）。

    队长：从「还没翻开的己方词」里挑一个当线索目标，线索不能是场上的词。
    队员：按词面与线索的字符重合度挑最像的未翻开词。

    修复过一个真实缺陷：旧实现固定取 `mine[0]`，那个词被翻开后
    整局仍反复给同一个线索「XX相关 1」，队员于是反复猜同一个词，
    15 次线索只翻出 5 个词——观感上就是"AI 卡住了"且对局推进极慢。
    现在必须跳过已翻开的词，并保证同一线索不重复出现。
    """

    name = "codenames"

    def __init__(self, name: str) -> None:
        self.name = name
        self._used_clues: set[str] = set()

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        if observation.get("phase") == "hint":
            return self._act_captain(observation, on_thought)
        return self._act_operative(observation, on_thought)

    def _act_captain(
        self, observation: dict[str, Any], on_thought: ThoughtSink | None
    ) -> AgentOutput:
        colors = observation.get("secretColors") or []
        words = observation.get("words") or []
        revealed = observation.get("revealed") or []
        my_team = observation.get("team")
        board_words = set(words)
        # 只挑还没翻开的己方词：已翻开的目标不需要再提示。
        targets = [
            word
            for index, word in enumerate(words)
            if index < len(colors)
            and colors[index] == my_team
            and index < len(revealed)
            and not revealed[index]
        ]
        base = targets[0] if targets else "提示"
        # 线索不能是场上的词；同一目标可能被反复问到（队员猜错换手），
        # 加一个稳定后缀保证这条线索在整个对局里唯一，避免"重复同一句"。
        clue = f"{base}相关"
        suffix = 2
        while clue in board_words or clue in self._used_clues:
            clue = f"{base}相关{suffix}"
            suffix += 1
        self._used_clues.add(clue)
        if on_thought is not None:
            on_thought(f"队里还有 {len(targets)} 个词要猜，先提示「{base}」。")
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


class MinimaxAgent:
    """只吃 `observe()` 的极小极大选手：用引擎的合法动作 + 公开局面做搜索。

    为什么需要它：`BoardAgent` 每步固定挑同一个相对索引，A/B 因此永远走
    两条固定路线，镜像对局会出现"某一方全胜"。那不是平台偏袒，而是选手
    太弱（无策略）造成的假象。要证明平台公平，必须用**会思考**的选手。

    它的信息来源严格等于 LLM 选手：
    - 合法动作来自 `legal` 参数（引擎给的权威列表）；
    - 局面来自 `observation["gameState"]`（已按 visibility 裁剪过）；
    - 不使用 target / secretColors / 任何私有字段。

    `depth` 是搜索深度。为保持确定性不使用随机数，平局按动作文本排序取第一个。
    """

    name = "minimax"

    def __init__(self, name: str, game_id: str = "", depth: int = 2, board_fn=None) -> None:
        self.name = name
        self.game_id = game_id
        self.depth = max(1, depth)
        self._board_fn = board_fn

    def act(
        self,
        observation: dict[str, Any],
        legal: list[str],
        on_thought: ThoughtSink | None = None,
    ) -> AgentOutput:
        moves = [str(m) for m in (legal or [])]
        if not moves:
            return AgentOutput(move=Move(kind="ask", value=""), raw="[no-legal-moves]")
        # 井字棋/反井字棋这类 3x3 才能做完整搜索；其它棋盘按"能立刻赢就赢"启发。
        choice = self._search(observation, moves)
        if on_thought is not None:
            on_thought(f"在 {len(moves)} 个合法动作里搜索 {self.depth} 层，选择 {choice}。")
        return AgentOutput(move=Move(kind="guess", value=choice), raw=f"minimax:{choice}")

    # ---- 搜索 ----

    def _search(self, observation: dict[str, Any], moves: list[str]) -> str:
        board = _board_of(observation)
        if board is None or len(board) != 3 or any(len(row) != 3 for row in board):
            return sorted(moves)[0]
        mark_mine, mark_theirs = _marks_for(observation)
        best_score = None
        best_move = sorted(moves)[0]
        for move in sorted(moves):
            index = _cell_index(move)
            if index is None:
                continue
            row, col = divmod(index, 3)
            if board[row][col]:
                continue
            board[row][col] = mark_mine
            score = self._minimax(board, self.depth - 1, False, mark_mine, mark_theirs)
            board[row][col] = ""
            if best_score is None or score > best_score:
                best_score, best_move = score, move
        return best_move

    def _minimax(self, board: list[list[str]], depth: int, maximizing: bool, mine: str, theirs: str) -> int:
        winner = _winner_of(board, mine, theirs)
        if winner == "mine":
            return 100 + depth
        if winner == "theirs":
            return -100 - depth
        if depth <= 0 or all(cell for row in board for cell in row):
            return 0
        cells = [(r, c) for r in range(3) for c in range(3) if not board[r][c]]
        if maximizing:
            best = -10_000
            for row, col in cells:
                board[row][col] = mine
                best = max(best, self._minimax(board, depth - 1, False, mine, theirs))
                board[row][col] = ""
            return best
        best = 10_000
        for row, col in cells:
            board[row][col] = theirs
            best = min(best, self._minimax(board, depth - 1, True, mine, theirs))
            board[row][col] = ""
        return best


def _board_of(observation: dict[str, Any]) -> list[list[str]] | None:
    """从公开 gameState 里取 3x3 棋盘；取不到就返回 None（不做搜索）。"""
    gs = observation.get("gameState") or {}
    board = gs.get("board") if isinstance(gs, dict) else None
    if not isinstance(board, list):
        return None
    return [[str(cell or "") for cell in row] if isinstance(row, list) else [] for row in board]


def _marks_for(observation: dict[str, Any]) -> tuple[str, str]:
    """确定自己与对手在棋盘上的符号。

    井字棋先手恒为 X（上游规则），所以按"我是先手吗"决定符号；
    拿不到先手信息时退回 X/O 假定。
    """
    you = str(observation.get("you", ""))
    players = observation.get("players") or []
    first = players[0] if players else ""
    if you and first:
        return ("X", "O") if you == first else ("O", "X")
    return "X", "O"


def _cell_index(move: str) -> int | None:
    import re as _re

    match = _re.fullmatch(r"\[\s*(\d+)\s*\]", move.strip())
    return int(match.group(1)) if match else None


def _winner_of(board: list[list[str]], mine: str, theirs: str) -> str | None:
    lines = [list(row) for row in board]
    lines += [[board[r][c] for r in range(3)] for c in range(3)]
    lines.append([board[i][i] for i in range(3)])
    lines.append([board[i][2 - i] for i in range(3)])
    for line in lines:
        if line[0] and line[0] == line[1] == line[2]:
            if line[0] == mine:
                return "mine"
            if line[0] == theirs:
                return "theirs"
    return None
