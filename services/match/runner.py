"""驱动一局比赛：取当前选手 → 问动作 → 引擎判定 → 写事件。

事实只在 engine 里产生，只在 runner 里落成事件；选手看不到事件日志。
`play_with_budget` 在 token 预算耗尽时正常收尾（写 match.ended），
把"预算用尽"与"选手犯规"区分开。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .engine.base import Game, Move, PlayerId

try:  # 避免 runner 反向依赖具体选手实现：测试里的轻量日志/选手不需要 llm 模块
    from .agents.llm import LLMBudgetExceeded
except Exception:  # noqa: BLE001 - httpx 缺失也不该让纯引擎测试失败
    class LLMBudgetExceeded(RuntimeError):  # type: ignore[no-redef]
        """兜底定义，语义与 llm.LLMBudgetExceeded 相同。"""


class EventLogLike(Protocol):
    def append(self, event_type: str, payload: dict[str, Any], *, player: str | None = None, at: str | None = None) -> dict[str, Any]: ...

    def mark_ended(self) -> None: ...


def _as_message_payload(text: str | dict[str, Any]) -> dict[str, Any]:
    """解说器返回结构化消息；自定义解说器可能只返回字符串，这里统一成载荷。

    兼容纯文本是为了不破坏第三方 narrator：它们不需要知道 msg/params 约定。
    """
    if isinstance(text, dict):
        return text
    return {"text": str(text)}


@dataclass
class _TurnOutcome:
    """一步的结果：正常走完 or 弃权结束。"""

    state: dict[str, Any]
    forfeited: bool = False
    reason: str = ""


@dataclass
class RunnerResult:
    winner: PlayerId | None
    endReason: str
    turns: int
    final_state: dict[str, Any]


class TurnTimeout(RuntimeError):
    """选手在一手内超出墙钟（config.turnTimeoutMs）。按弃权处理。"""


class _ActResult:
    __slots__ = ("output", "error")

    def __init__(self, output: Any = None, error: BaseException | None = None) -> None:
        self.output = output
        self.error = error


def _act_with_deadline(agent: Any, observation: Any, legal: list[str], on_thought: Any, deadline_ms: int | None) -> Any:
    """调 agent.act；deadline_ms 非空时用墙钟强制。

    用线程执行（agent 内部是同步网络调用，无法协作取消）；到点后返回
    TurnTimeout，孤儿线程随进程退出（daemon）。on_thought 的事件写入在
    超时后可能还会发生一次——事件日志是 append-only，多一条思考不破坏
    重放，但绝不会多出一条 action。
    """
    if deadline_ms is None:
        return agent.act(observation, legal, on_thought)

    result = _ActResult()
    finished = threading.Event()

    def run() -> None:
        try:
            result.output = agent.act(observation, legal, on_thought)
        except BaseException as exc:  # noqa: BLE001 - 原样带回主线程判定
            result.error = exc
        finally:
            finished.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    if not finished.wait(deadline_ms / 1000):
        raise TurnTimeout(f"回合超时（deadline {deadline_ms}ms）")
    if result.error is not None:
        raise result.error
    return result.output


class Runner:
    """驱动一局比赛：取当前选手 → 问动作 → 引擎判定 → 写事件。

    事实只在 engine 里产生，只在 runner 里落成事件；选手看不到事件日志。
    """

    def __init__(
        self,
        game: Game,
        log: EventLogLike,
        agents: dict[PlayerId, Any],
        *,
        narrator: Any | None = None,
        director: Any | None = None,
        max_attempts_per_turn: int = 2,
        max_turns: int = 200,
        deadline_ms: int = 30_000,
        max_thought_chars: int = 4000,
        resume: bool = False,
        enforce_deadline: bool = False,
        seed_mode: str = "",
    ) -> None:
        self.game = game
        self.log = log
        self.agents = agents
        self.narrator = narrator
        self.director = director
        self.max_attempts_per_turn = max_attempts_per_turn
        self.max_turns = max_turns
        self.deadline_ms = deadline_ms
        self.max_thought_chars = max_thought_chars
        # resume=True 表示日志里已有前半段：不再补写 match.created / match.started。
        self.resume = resume
        # seed_mode：secure 时不把种子写进公开事件（种子能重建隐藏信息）。
        self.seed_mode = seed_mode
        # enforce_deadline：config.turnTimeoutMs 设置后由墙钟强制。
        # 关闭时 deadline_ms 只写进 turn.started 事件（旧行为：告知，不执行）。
        self.enforce_deadline = enforce_deadline
        self._last_leader: str | None = None
        self._steps_since_situation = 0
        # 已完成的回合数：预算在回合中途触发时，RunnerResult.turns 要如实反映
        # 已经跑过的步数，而不是报 0。
        self._completed_turns = 0
        # 回合代际：每个回合开始时自增，上一代留下的 on_thought 回调随之失效。
        # 墙钟超时后 agent 线程仍在跑（同步网络调用无法取消），没有这道闸门
        # 它会在 match.ended 之后继续写 thought.delta，污染 append-only 日志。
        self._turn_generation = 0

    def play(self, state: dict[str, Any]) -> RunnerResult:
        players = list(state["players"])
        if not self.resume:
            self._record_start(state, players)

        turns = 0
        round_no = 1
        while not state["finished"]:
            if turns >= self.max_turns:
                return self._finish(state, "超过最大回合数，强制结束", None, turns)

            player = self.game.current_player(state)
            if player is None:
                break

            if turns % 2 == 0:
                self.log.append("round.started", {"round": round_no, "activePlayers": players})
            # 未配置时限时不写 deadlineMs：契约禁止 null，写默认值又会在
            # 事件流里撒谎说"这一回合有 30 秒时限"。
            turn_payload = {"deadlineMs": self.deadline_ms} if self.deadline_ms is not None else {}
            self.log.append("turn.started", turn_payload, player=player)
            self._announce_deadline(player)

            outcome = self._take_turn(state, player)
            if outcome.forfeited:
                return self._finish(outcome.state, outcome.reason, outcome.state["winner"], turns)

            state = outcome.state
            turns += 1
            self._completed_turns = turns
            if turns % 2 == 0:
                round_no += 1

            if state["finished"]:
                return self._finish(state, state["endReason"], state["winner"], turns)

        return self._finish(state, state.get("endReason") or "结束", state.get("winner"), turns)

    def play_with_budget(self, state: dict[str, Any]) -> RunnerResult:
        """play() 的预算感知版本：token 预算用尽时写 match.ended 并结束。

        预算终止判**该选手负**，不是平局：旧实现 `winner=None`，
        于是落后方可以主动烧光预算把必败局变成平局——这是"输不起就消局"。
        预算耗尽与超时、弃权属于同一类"这一方没能继续比赛"。
        """
        try:
            return self.play(state)
        except LLMBudgetExceeded as exc:
            loser = getattr(self, "_budget_exhausted_by", None)
            players = list(state.get("players") or [])
            winner = next((p for p in players if p != loser), None) if loser else None
            return self._finish(
                state,
                f"token 预算用尽（{loser}）：{exc}",
                winner,
                self._completed_turns,
            )

    # ---- 单步 ----

    def _announce_deadline(self, player: PlayerId) -> None:
        """把本手墙钟告知支持 deadline 的选手（HTTPAgent 会写进请求体）。

        只在 enforce_deadline 时下发：没设置 turnTimeoutMs 的比赛，
        payload 与旧行为完全一致（不带 deadlineMs 字段）。
        """
        if not self.enforce_deadline:
            return
        for agent in {id(agent): agent for agent in self.agents.values()}.values():
            setter = getattr(agent, "set_deadline", None)
            if setter is not None:
                setter(self.deadline_ms)

    def _record_start(self, state: dict[str, Any], players: list[PlayerId]) -> None:
        config = {
            key: state[key]
            for key in ("maxQuestions", "teams", "teamSize", "catalogSize", "lang")
            if key in state
        }
        if self.seed_mode:
            config["seedMode"] = self.seed_mode
        # 回合墙钟是平台层配置（不属于任何赛制状态），也必须落进
        # match.created：重放与战报要能回答"这一手为什么被判超时"。
        if self.enforce_deadline:
            config["turnTimeoutMs"] = self.deadline_ms
        # 事件流对所有观众可见，而 seed 能重建隐藏信息。
        # secure 模式不把种子写进事件：回放靠事件本身，不需要重新抽签。
        created_payload = {
            "game": self.game.game_id,
            "players": players,
            # 赛制配置必须完整落进 match.created：rehydrate 靠它重建同一局。
            # 漏记 teams/teamSize 会让重放退回默认编队，多队对局直接错乱。
            "config": config,
        }
        # 只有 replay 模式公开种子（那时公开是产品功能）；secure 模式不写。
        # 没有 seedMode 的是历史事件或直接构造引擎的测试：保留旧行为，
        # 否则老录像会因缺 seed 而无法重建。
        from .fairness import assert_seed_disclosure_allowed

        seed_mode = self.seed_mode
        if not seed_mode or assert_seed_disclosure_allowed(seed_mode):
            created_payload["seed"] = state["seed"]
        self.log.append("match.created", created_payload)
        first = self.game.current_player(state) or players[0]
        self.log.append("match.started", {"firstPlayer": first})

    def _thought_sink(
        self, player: PlayerId, state: dict[str, Any] | None = None, collect: list[str] | None = None
    ) -> tuple[Any, list[int]]:
        """返回（回调，计数器）。计数器是单元素列表，闭包里可变。

        `collect` 给定时同时把每条思考收进去，供 includeHistory 回放本手思考。

        回调绑定「回合代际」：回合结束后（含超时被判弃权）代际递增，晚到的
        回调一律丢弃。没有这道闸门，超时留下的孤儿线程会在 match.ended 之后
        继续往 append-only 日志里补 thought.delta，重放与战报都会看到"死人说话"。
        """
        seen = [0]
        generation = self._turn_generation

        def on_thought(text: str, *, _player: PlayerId = player) -> None:
            if not text:
                return
            if generation != self._turn_generation:
                return
            text = text[: self.max_thought_chars]
            if collect is not None:
                collect.append(text)
            self.log.append(
                "thought.delta",
                {"text": text},
                player=_player,
                visibility=_thought_visibility(self.game, state, _player),
            )
            seen[0] += 1
            if self.director is not None:
                cue = self.director.cue_for_thought(_player, text)
                if cue is not None:
                    self._append_cue(cue)

        return on_thought, seen

    def _ask_with_retry(self, state: dict[str, Any], player: PlayerId) -> tuple[Any, Move | None]:
        """最多尝试 N 次；返回（最后一次 MoveResult，最后一次动作）。

        每次尝试的真实耗时与用量会累加到 `self._turn_elapsed_ms` /
        `self._turn_usage`，由 `_take_turn` 写进 action.applied。
        没有这一层，战报与观战端无法回答"这一手为什么慢"。
        """
        observation = self.game.observe(state, player)
        legal = self.game.legal_moves(state, player)

        result = None
        move: Move | None = None
        self._turn_elapsed_ms = 0
        self._turn_usage = None
        # 预算耗尽的责任方：由 _take_turn 在抛异常前写入。
        self._budget_exhausted_by: PlayerId | None = None
        thoughts: list[str] = []
        on_thought, _seen = self._thought_sink(player, state, thoughts)
        try:
            for _attempt in range(self.max_attempts_per_turn):
                # 预算用尽不是"动作不合法"：换个动作也救不回来，必须终止整场，
                # 否则每次犯规重试都会继续向同一端点要 token。
                try:
                    started = time.monotonic()
                    output = _act_with_deadline(
                        self.agents[player], observation, legal, on_thought,
                        self.deadline_ms if self.enforce_deadline else None,
                    )
                    # 优先用选手自己报的耗时（含其内部重试）；没有就量外圈。
                    reported = getattr(output, "elapsed_ms", None)
                    self._turn_elapsed_ms += (
                        int(reported) if isinstance(reported, int) and reported >= 0
                        else int((time.monotonic() - started) * 1000)
                    )
                    usage = getattr(output, "usage", None)
                    if isinstance(usage, dict) and usage:
                        self._turn_usage = _merge_usage(self._turn_usage, usage)
                except LLMBudgetExceeded:
                    # 记下是谁烧穿的：判负要用，不能一律判平局。
                    self._budget_exhausted_by = player
                    raise
                except Exception as exc:  # noqa: BLE001 - 选手实现是外部代码
                    # 选手抛异常不能把比赛挂死：旧实现直接冒泡，比赛停在
                    # turn.started、没有 match.ended，任务重试 3 次后 failed，
                    # 观众看到一场永远"直播中"的比赛。
                    # 语义上这与弃权同类：这一方没能给出合法动作。
                    self.log.append(
                        "action.rejected",
                        {"reason": f"选手异常：{type(exc).__name__}: {exc}"[:300], "retryable": False},
                        player=player,
                    )
                    return None, None
                except TurnTimeout as exc:
                    # 墙钟到点：与非法动作同一条弃权路径，但原因要能对上事件。
                    self.log.append(
                        "action.rejected",
                        {"reason": str(exc), "retryable": False},
                        player=player,
                    )
                    return None, None
                move = output.move
                # 事件流对所有观众必须**同样长**：seq 是主键，私有事件被
                # 过滤后会出现空洞（实测 [0..5,7]），断线续传与回放都会错位。
                # 所以 `action.proposed` 保持公开、只带动作本身；
                # 模型原文可能夹带私有推理（代号队长原文里出现过整盘配色），
                # 单独写成一条私有事件 thought.raw，只对该作者可见。
                self.log.append(
                    "action.proposed",
                    # raw 是契约必填字段；公开事件里只记动作，原文另存私有事件。
                    {"action": {"kind": move.kind, "value": move.value}, "raw": ""},
                    player=player,
                )
                if output.raw:
                    # 复用与 on_thought 相同的可见性判定：代号队长的原文
                    # 含配色，必须私有；其他赛制的原文是公开的思考。
                    self.log.append(
                        "thought.delta",
                        {"text": output.raw[:self.max_thought_chars]},
                        player=player,
                        visibility=_thought_visibility(self.game, state, player),
                    )
                result = self.game.apply(state, player, move)
                if result.legal:
                    _record_own_history(self.agents, player, move, thoughts)
                    break
                self.log.append(
                    "action.rejected",
                    {"reason": result.reason, "retryable": result.retryable},
                    player=player,
                )
                if not result.retryable:
                    break
        finally:
            # 回合结束（正常/犯规/超时/异常）都封掉这一代的思考回调：
            # 超时留下的孤儿线程之后再到 on_thought 也不会写进日志。
            self._turn_generation += 1
        return result, move

    def _take_turn(self, state: dict[str, Any], player: PlayerId) -> "_TurnOutcome":
        result, move = self._ask_with_retry(state, player)
        if result is None or not result.legal or result.next_state is None:
            return self._forfeit_turn(state, player)

        state = result.next_state
        applied: dict[str, Any] = {
            "action": {"kind": move.kind, "value": move.value},
            "effects": [{"kind": e.kind, **e.detail} for e in result.effects],
        }
        # 只在真的量到东西时写：0ms 是合法值（脚本选手），None 表示不知道。
        if self._turn_elapsed_ms:
            applied["elapsedMs"] = self._turn_elapsed_ms
        if self._turn_usage:
            applied["usage"] = self._turn_usage
        self.log.append("action.applied", applied, player=player)
        self.log.append(
            "state.changed",
            {"publicState": result.public_state, "deltas": _deltas(result.effects)},
        )
        self._narrate_turn(state, player, move, result)
        return _TurnOutcome(state=state)

    def _narrate_turn(self, state: dict[str, Any], player: PlayerId, move: Move | None, result: Any) -> None:
        if self.director is not None:
            cue = self.director.cue_for(
                player=player,
                effects=result.effects,
                public_state=result.public_state,
                finished=bool(state["finished"]),
            )
            if cue is not None:
                self._append_cue(cue)
        if self.narrator is None:
            return

        line = self.narrator.line(
            result.public_state, player, move, result.effects, game_id=self.game.game_id
        )
        if line:
            self.log.append(
                "narration.line",
                {**_as_message_payload(line), "tone": "neutral", "refs": [self.log_last_seq() - 1]},
            )
        # 局势播报：只在转折点开口，避免每步都报变成噪音。
        situation = self._maybe_situation(result.public_state, state)
        if situation:
            self.log.append(
                "narration.line",
                {**_as_message_payload(situation), "tone": "turn", "refs": [self.log_last_seq() - 1]},
            )

    def _forfeit_turn(self, state: dict[str, Any], player: PlayerId) -> "_TurnOutcome":
        """动作始终不合法：按弃权处理，对手获胜。"""
        forfeit = self.game.apply(state, player, Move(kind="forfeit", value=""))
        if not forfeit.legal or forfeit.next_state is None:
            return _TurnOutcome(state=state, forfeited=True, reason="选手动作无法执行")
        state = forfeit.next_state
        self.log.append(
            "action.applied",
            {
                "action": {"kind": "forfeit", "value": ""},
                "effects": [{"kind": e.kind, **e.detail} for e in forfeit.effects],
            },
            player=player,
        )
        self.log.append(
            "state.changed",
            {"publicState": forfeit.public_state, "deltas": _deltas(forfeit.effects)},
        )
        return _TurnOutcome(state=state, forfeited=True, reason=state["endReason"])

    def _append_cue(self, cue: Any) -> None:
        self.log.append("director.cue", cue.payload())

    def _maybe_situation(self, public_state: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
        """判断此刻是否值得播报局势。

        触发条件（满足其一）：
        1. 领先方发生变化
        2. 距离上次播报已过 4 步（给沉默设上限）
        不满足就返回空，让画面安静。

        两类赛制的公开状态形状不同，必须分别认：
        - 代号等多方赛制给 `remaining: {队名: 剩余词数}`，能算出领先方；
        - 二十问等双人赛制只给 `remainingPublic: int`（候选还剩几个），
          没有"谁领先"的概念，按固定步数节奏播报。
        旧实现只读 `remaining` 并对它调 `.values()`，于是二十问永远走不进
        播报分支（`remainingPublic` 被忽略），11/12 个赛制的局势播报实际失效。
        """
        if self.narrator is None or not hasattr(self.narrator, "situation"):
            return None

        remaining = public_state.get("remaining")
        if isinstance(remaining, dict) and len(remaining) >= 2:
            values = sorted(remaining.values())
            leader = min(remaining, key=lambda k: remaining[k]) if values[0] != values[1] else None
            changed = leader != self._last_leader
            self._last_leader = leader
        elif isinstance(public_state.get("remainingPublic"), int):
            # 单人进度型赛制：没有领先方，靠步数上限制造播报节奏。
            changed = True
        else:
            return None

        steps_since = self._steps_since_situation + 1
        self._steps_since_situation = steps_since

        if not changed and steps_since < 4:
            return None

        self._steps_since_situation = 0
        return self.narrator.situation(public_state, game_id=self.game.game_id)

    def log_last_seq(self) -> int:
        return getattr(self.log, "next_seq", 0)

    def _finish(self, state: dict[str, Any], reason: str, winner: PlayerId | None, turns: int) -> RunnerResult:
        # 终局事件必须恰好一条：任务重试或副本切换时，replay 出来的状态可能
        # 已经 finished，但日志里还没有 match.ended。重复追加会让战报与
        # 事件计数翻倍，所以先查再写。
        payload = {"winner": winner, "reason": reason, "finalState": self.game.public_state(state)}
        terminal = getattr(self.log, "append_terminal", None)
        if terminal is not None:
            terminal(payload)
        elif not _has_ended(self.log):
            # 测试里的轻量日志没有事务版接口，退回"先查后写"。
            self.log.append("match.ended", payload)
            self.log.mark_ended()
        return RunnerResult(winner=winner, endReason=reason, turns=turns, final_state=state)


def _record_own_history(agents: dict[PlayerId, Any], player: PlayerId, move: Move, thoughts: list[str]) -> None:
    """把该选手这一手的 (action, thought) 记进它自己的记忆。

    includeHistory=false 的 agent（脚本/HTTP/默认 LLM）没有 record_history
    或直接 no-op：只对显式开启记忆的选手生效，且只回放它自己的——
    绝不把对手的动作或任何私有观察喂回去。
    """
    record = getattr(agents.get(player), "record_history", None)
    if record is None:
        return
    action_text = move.value if move.kind != "forfeit" else ""
    if not action_text:
        return
    record(action_text, thoughts[-1] if thoughts else "")


def _thought_visibility(game: Any, state: dict[str, Any] | None, player: PlayerId) -> dict[str, Any] | None:
    """问引擎这条思考能不能公开。引擎不实现或抛错时按公开处理。

    宁可公开也不能因为一个可选方法让整局崩掉——但代号这类隐藏信息赛制
    必须实现它，`test_codenames` 里有一条断言锁住队长思考不可见。
    """
    if state is None:
        return None
    probe = getattr(game, "thought_visibility", None)
    if probe is None:
        return None
    try:
        return probe(state, player)
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 可见性判定失败不该中断比赛
        return None


def _has_ended(log: Any) -> bool:
    reader = getattr(log, "read", None)
    if reader is None:
        return False
    try:
        return any(event.get("type") == "match.ended" for event in reader())
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 读不到就按"未结束"处理，宁可补写  # aqg: top-level boundary
        return False


def ensure_terminal(log: Any, game: Any, state: dict[str, Any]) -> bool:
    """断点续跑时补齐终局事件。返回是否补写。

    场景：worker 写完最后一条 action.applied 后崩溃，重放状态已经 finished，
    但没有 match.ended。旧实现直接跳过执行，比赛永远显示"直播中"。
    """
    if not state.get("finished"):
        return False
    payload = {
        "winner": state.get("winner"),
        "reason": state.get("endReason") or "结束",
        "finalState": game.public_state(state),
    }
    terminal = getattr(log, "append_terminal", None)
    if terminal is not None:
        # append_terminal 内部会顺带把 matches.ended_at 补上——
        # 旧实现只补事件、不补标记，比赛会永远算 live。
        return bool(terminal(payload))
    if _has_ended(log):
        log.mark_ended()
        return False
    log.append("match.ended", payload)
    log.mark_ended()
    return True


def _deltas(effects: Any) -> list[dict[str, Any]]:
    return [{"kind": e.kind, **e.detail} for e in effects]


def _merge_usage(acc: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """把一手内多次尝试的用量累加。requests/tokens 相加，快照类字段取最新。"""
    if acc is None:
        return dict(new)
    merged = dict(acc)
    for key, value in new.items():
        if isinstance(value, (int, float)) and isinstance(merged.get(key), (int, float)):
            merged[key] = merged[key] + value if key != "tokensUsedTotal" else value
        else:
            merged[key] = value
    return merged
