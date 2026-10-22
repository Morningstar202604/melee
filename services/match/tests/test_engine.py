from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.match.agents.scripted import GreedyAgent, ScriptedAgent
from services.match.contract import ContractViolation, check, check_all
from services.match.engine.base import Move
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog, EventLogError, replay
from services.match.narrator import RuleNarrator
from services.match.runner import Runner


def play(seed: int = 7, *, agents=None, max_questions: int = 21):
    game = TwentyQuestions()
    players = ["A", "B"]
    state = game.init(seed=seed, players=players, config={"maxQuestions": max_questions})
    tmp = tempfile.mkdtemp()
    log = EventLog.open(Path(tmp) / "m.db", f"m{seed}")
    log.create_match(game=game.game_id, seed=seed, players=players)
    agents = agents or {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)}
    result = Runner(game, log, agents, narrator=RuleNarrator()).play(state)
    log.assert_contiguous()
    events = log.read()
    log.close()
    return result, events


def test_seq_starts_at_zero_and_is_contiguous():
    _, events = play()
    assert [e["seq"] for e in events] == list(range(len(events)))


def test_every_event_matches_contract():
    _, events = play()
    check_all(events)


def test_contract_rejects_unknown_event_type():
    with pytest.raises(ContractViolation):
        check({"seq": 0, "matchId": "m", "type": "made.up", "at": "2026-10-19T00:00:00Z", "payload": {}})


def test_contract_rejects_missing_payload_field():
    with pytest.raises(ContractViolation):
        check(
            {
                "seq": 0,
                "matchId": "m",
                "type": "match.ended",
                "at": "2026-10-19T00:00:00Z",
                "payload": {"winner": None},
            }
        )


def test_replay_is_deterministic_and_matches_final_state():
    result, events = play(seed=11)
    first = replay(events)
    second = replay(events)
    assert first == second
    assert first["winner"] == result.winner
    assert first["endReason"] == result.endReason


def test_replaying_a_prefix_never_sees_the_future():
    _, events = play(seed=11)
    full = replay(events)
    half = replay(events[: len(events) // 2])
    assert set(half["narration"]).issubset(set(full["narration"]))
    assert half["winner"] is None


def test_target_never_leaks_into_observation():
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    target = state["target"]
    for player in ("A", "B"):
        observation = game.observe(state, player)
        assert target not in repr(observation)
        assert "target" not in observation


def test_target_never_leaks_into_public_state():
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    public = game.public_state(state)
    assert state["target"] not in repr(public)


def test_no_event_payload_contains_the_target_before_the_end():
    """第一次有人报答案之前，事件流里绝不能出现答案。

    报答案之后它就已经是公开事实（观众必须看到判中还是判错），
    所以只检查第一次 guess 之前的部分。
    """
    _, events = play(seed=11)
    game = TwentyQuestions()
    target = game.init(seed=11, players=["A", "B"], config={})["target"]

    first_guess = None
    for index, event in enumerate(events):
        payload = event["payload"]
        action = payload.get("action") if isinstance(payload.get("action"), dict) else None
        if action and action.get("kind") == "guess":
            first_guess = index
            break

    assert first_guess is not None, "这一局必须有人报答案"
    for event in events[:first_guess]:
        assert target not in repr(event["payload"]), f"第 {event['seq']} 条事件（{event['type']}）泄漏了答案"


def test_guessing_wrong_immediately_loses():
    agents = {
        "A": ScriptedAgent("A", [Move(kind="guess", value="不存在的答案")]),
        "B": ScriptedAgent("B", []),
    }
    result, events = play(seed=5, agents=agents)
    assert result.winner == "B"
    assert "猜错" in result.endReason
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "match.ended"


def test_turn_order_alternates():
    _, events = play(seed=9)
    players = [e["player"] for e in events if e["type"] == "turn.started"]
    assert players == ["A", "B"] * (len(players) // 2) + (["A"] if len(players) % 2 else [])


def test_illegal_move_is_logged_and_retried():
    agents = {
        "A": ScriptedAgent("A", [Move(kind="ask", value="category=不存在的类别"), Move(kind="ask", value="category=建筑")]),
        "B": GreedyAgent("B", CATALOG),
    }
    _, events = play(seed=4, agents=agents)
    rejected = [e for e in events if e["type"] == "action.rejected"]
    assert len(rejected) == 1
    assert rejected[0]["payload"]["retryable"] is True
    assert any(e["type"] == "action.applied" and e["player"] == "A" for e in events)


def test_eventlog_rejects_unknown_type():
    tmp = tempfile.mkdtemp()
    log = EventLog.open(Path(tmp) / "m.db", "m1")
    with pytest.raises(EventLogError):
        log.append("not.a.type", {})
    log.close()


def test_eventlog_resumes_seq_after_reopen():
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "m.db"
    log = EventLog.open(path, "m1")
    log.append("match.started", {"firstPlayer": "A"})
    log.append("turn.started", {"deadlineMs": 1000}, player="A")
    log.close()

    reopened = EventLog.open(path, "m1")
    assert reopened.next_seq == 2
    reopened.append("turn.started", {"deadlineMs": 1000}, player="B")
    last = reopened.read()[-1]
    assert last["seq"] == 2
    reopened.close()


# ---------- 补齐变异测试暴露的缺口 ----------

def test_question_cap_is_twenty_one_by_default():
    """默认上限是产品规则：被改成 22 会让"二十问"名不副实。"""
    from services.match.engine.twenty_questions import OPTIONS

    assert OPTIONS["maxQuestions"] == 21


def test_catalog_rows_are_real_and_non_empty():
    from services.match.engine.twenty_questions import CATALOG

    assert CATALOG, "目录不能为空"
    for row in CATALOG:
        assert row.get("name"), row
        assert row.get("category"), row


def test_non_matching_aspect_value_is_rejected():
    """属性名合法但取值不属于该属性时必须拒绝，而不是当成"否"。"""
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    result = game.apply(state, "A", Move("ask", "place=海底"))
    assert not result.legal
    assert "没有取值" in result.reason


def test_forfeit_marks_opponent_as_winner():
    game = TwentyQuestions()
    state = game.init(seed=3, players=["A", "B"], config={})
    result = game.apply(state, "A", Move("forfeit", ""))
    assert result.legal
    assert result.next_state["winner"] == "B"
    assert result.next_state["endReason"] == "弃权"


def test_concurrent_open_does_not_fail_when_switching_wal(tmp_path):
    """并发 open 同一库时，切 WAL 撞锁不能把建场打断（CI 上真实炸过）。"""
    import threading

    from services.match.eventlog import EventLog

    db = tmp_path / "concurrent.db"
    errors: list[str] = []

    def open_one(index: int) -> None:
        try:
            log = EventLog.open(db, f"m{index}")
            log.create_match("twenty-questions", index, ["A", "B"])
            log.close()
        except Exception as exc:  # noqa: BLE001 - 收集所有失败
            errors.append(f"{index}:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=open_one, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
