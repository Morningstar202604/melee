"""runner 辅助函数的分支补齐：narration 触发、ensure_terminal、usage 合并、清理回调。

这些分支原来只被 e2e 偶发覆盖，全量 coverage 跑得慢就常年露在门禁外。
"""

from __future__ import annotations

from services.match.engine.twenty_questions import TwentyQuestions
from services.match.runner import (
    Runner,
    _has_ended,
    _merge_usage,
    _thought_visibility,
    ensure_terminal,
)


class _Log:
    def __init__(self, with_terminal: bool = True) -> None:
        self.events: list[dict] = []
        self.ended = False
        if with_terminal:
            self.append_terminal = self._terminal

    def append(self, type_, payload, **kw):
        event = {"type": type_, "payload": payload, **kw}
        self.events.append(event)
        return event

    def read(self):
        """_has_ended 通过 read() 判断；与 EventLog 同名接口。"""
        return iter(self.events)

    def _terminal(self, payload):
        if self.ended:
            return False
        self.ended = True
        self.events.append({"type": "match.ended", "payload": payload})
        return True

    def mark_ended(self):
        self.ended = True


class _Narrator:
    def __init__(self):
        self.situations = 0

    def situation(self, public_state, game_id=None):
        self.situations += 1
        return "局势播报"

    def line(self, *args, **kwargs):
        return "一句解说"


class _Agent:
    name = "a"

    def __init__(self, legal_pick=None):
        self._pick = legal_pick

    def act(self, observation, legal, on_thought=None):
        from services.match.engine.base import Move

        pick = self._pick or next((x for x in legal if x.startswith("ask:")), legal[0])
        value = pick.split(":", 1)[1] if ":" in pick else pick
        return type("O", (), {"move": Move(kind="ask", value=value), "raw": "", "elapsed_ms": 1})()


class TestNarratorSituation:
    def test_situation_triggers_on_leader_change_and_every_four_steps(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 8})
        narrator = _Narrator()
        runner = Runner(game, _Log(), {"A": _Agent(), "B": _Agent()}, narrator=narrator)
        runner.play(state)
        # 回归锁：二十问的 publicState 只有 remainingPublic（int），
        # 旧实现只认 remaining（dict）导致这里 0 次播报。
        assert narrator.situations >= 1

    def test_codenames_leader_change_still_works(self):
        """代号赛制仍按"领先方变化"触发，不被上面那条修复影响。"""
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        narrator = _Narrator()
        runner = Runner(game, _Log(), {"A": _Agent(), "B": _Agent()}, narrator=narrator)
        assert runner._maybe_situation({"remaining": {"红": 3, "蓝": 5}}, state) == "局势播报"
        assert runner._last_leader == "红"
        # 领先方没变且步数不足 4：安静。
        assert runner._maybe_situation({"remaining": {"红": 2, "蓝": 4}}, state) is None

    def test_situation_skipped_without_narrator(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        runner = Runner(game, _Log(), {"A": _Agent(), "B": _Agent()})
        # 内部方法直接调：没有 narrator 时必须安全返回空串。
        assert runner._maybe_situation({"remaining": {"A": 1, "B": 2}}, state) is None

    def test_situation_skipped_when_no_progress_shape(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        runner = Runner(game, _Log(), {"A": _Agent(), "B": _Agent()}, narrator=_Narrator())
        assert runner._maybe_situation({"remaining": {"A": 1}}, state) is None
        assert runner._maybe_situation({}, state) is None


class TestEnsureTerminal:
    def test_not_finished_returns_false(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        assert ensure_terminal(_Log(), game, state) is False

    def test_uses_append_terminal_when_available(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        state["finished"] = True
        state["winner"] = "A"
        state["endReason"] = "测试结束"
        log = _Log()
        assert ensure_terminal(log, game, state) is True
        assert log.ended is True

    def test_falls_back_when_log_has_no_append_terminal(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        state["finished"] = True
        state["winner"] = "B"
        state["endReason"] = "测试结束"
        log = _Log(with_terminal=False)
        assert ensure_terminal(log, game, state) is True
        assert any(e["type"] == "match.ended" for e in log.events)

    def test_second_call_is_noop(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        state["finished"] = True
        state["winner"] = "A"
        state["endReason"] = "测试结束"
        log = _Log()
        ensure_terminal(log, game, state)
        assert ensure_terminal(log, game, state) is False


class TestHasEnded:
    def test_true_when_readers_show_ended(self):
        log = _Log()
        log.events.append({"type": "match.ended", "payload": {}})
        assert _has_ended(log) is True

    def test_false_when_reader_raises(self):
        class _Bad:
            def __iter__(self):
                raise RuntimeError("boom")

        assert _has_ended(_Bad()) is False


class TestMergeUsage:
    def test_none_accumulator_returns_copy(self):
        assert _merge_usage(None, {"requests": 1}) == {"requests": 1}

    def test_sums_numbers_and_keeps_latest_snapshot(self):
        merged = _merge_usage(
            {"requests": 1, "prompt_tokens": 10, "tokensUsedTotal": 100},
            {"requests": 2, "prompt_tokens": 5, "tokensUsedTotal": 250, "note": "x"},
        )
        assert merged["requests"] == 3
        assert merged["prompt_tokens"] == 15
        # tokensUsedTotal 是快照，不累加。
        assert merged["tokensUsedTotal"] == 250
        assert merged["note"] == "x"


class TestThoughtVisibility:
    def test_missing_probe_returns_none(self):
        game = TwentyQuestions()
        state = game.init(seed=3, players=["A", "B"], config={"maxQuestions": 4})
        assert _thought_visibility(game, state, "A") is None

    def test_probe_exception_returns_none(self):
        class _G:
            def thought_visibility(self, state, player):
                raise ValueError("boom")

        assert _thought_visibility(_G(), {}, "A") is None

    def test_probe_value_returned(self):
        class _G:
            def thought_visibility(self, state, player):
                return {"mode": "private"}

        assert _thought_visibility(_G(), {}, "A") == {"mode": "private"}
