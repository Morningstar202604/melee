from __future__ import annotations

import tempfile
from pathlib import Path

from services.match.agents.scripted import GreedyAgent, ScriptedAgent
from services.match.director import Director
from services.match.engine.base import Move
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.narrator import RuleNarrator
from services.match.recap import build_recap, find_highlights, verify_summary
from services.match.runner import Runner
from services.match.season import Season


def play(seed: int = 7, agents=None) -> list[dict]:
    game = TwentyQuestions()
    state = game.init(seed=seed, players=["A", "B"], config={})
    log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", f"m{seed}")
    log.create_match(game.game_id, seed, ["A", "B"])
    agents = agents or {"A": GreedyAgent("A", CATALOG), "B": GreedyAgent("B", CATALOG)}
    Runner(game, log, agents, narrator=RuleNarrator(), director=Director()).play(state)
    events = log.read()
    log.close()
    return events


def test_recap_counts_match_events():
    events = play()
    summary = build_recap(events)
    assert verify_summary(summary, events) == []


def test_recap_one_line_mentions_winner():
    events = play()
    summary = build_recap(events)
    if summary["winner"]:
        assert summary["winner"] in summary["oneLine"]


def test_highlights_include_the_guess():
    events = play()
    highlights = find_highlights(events)
    assert any(h.kind == "guess" for h in highlights)


def test_highlights_are_capped_and_ordered():
    """选哪几段由权重决定，展示顺序按 seq 还原时间线。

    按权重排序展示会让战报把 seq 250 的瞬间排在 seq 13 前面，
    读起来像时间倒流（代号局实测踩到）。
    """
    events = play()
    highlights = find_highlights(events, limit=2)
    assert len(highlights) <= 2
    seqs = [h.seq for h in highlights]
    assert seqs == sorted(seqs), seqs
    # 被选中的仍应是权重最高的那几段
    all_highlights = find_highlights(events, limit=99)
    top_weights = sorted((h.weight for h in all_highlights), reverse=True)[: len(highlights)]
    assert sorted((h.weight for h in highlights), reverse=True) == top_weights


def test_wasted_question_is_detected():
    agents = {
        "A": ScriptedAgent(
            "A",
            [
                Move(kind="ask", value="category=建筑"),
                Move(kind="ask", value="category=建筑"),
            ],
        ),
        "B": GreedyAgent("B", CATALOG),
    }
    events = play(seed=3, agents=agents)
    kinds = {h.kind for h in find_highlights(events, limit=5)}
    assert "wasted_question" in kinds or "guess" in kinds


def test_forfeit_is_highlighted():
    from services.match.agents.llm import LLMAgent, LLMConfig

    from .fake_openai import FakeOpenAI

    with FakeOpenAI() as server:
        server.replies = [{"content": "我不知道"}, {"content": "还是不知道"}]
        agent = LLMAgent("A", LLMConfig(base_url=server.base_url, api_key="k", model="m", timeout_s=5))
        events = play(seed=3, agents={"A": agent, "B": GreedyAgent("B", CATALOG)})

    highlights = find_highlights(events, limit=5)
    assert any(h.kind in {"forfeit", "rejected"} for h in highlights)
    summary = build_recap(events)
    assert summary["winner"] == "B"
    assert "弃权" in summary["reason"]


def test_full_season_of_eight_players():
    """验收：一个 8 人循环赛可完整跑完并出榜。"""
    season = Season("s1", "2026 秋季")
    for index in range(8):
        pid = chr(ord("A") + index)
        season.add_player(pid, f"选手{pid}")
    season.build_round_robin()

    for round_no, a, b, _scheduled in season.fixtures:
        game = TwentyQuestions()
        state = game.init(seed=round_no * 100 + ord(a), players=[a, b], config={})
        log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", f"m{round_no}{a}{b}")
        log.create_match(game.game_id, state["seed"], [a, b])
        result = Runner(
            game,
            log,
            {a: GreedyAgent(a, CATALOG), b: GreedyAgent(b, CATALOG)},
            narrator=RuleNarrator(),
        ).play(state)
        log.close()
        season.record(f"m{round_no}{a}{b}", a, b, result.winner, result.endReason)

    table = season.standings()
    assert len(table) == 8
    assert sum(s.played for s in table) == 56

    total_wins = sum(s.wins for s in table)
    total_losses = sum(s.losses for s in table)
    assert total_wins == total_losses


def _synthetic(game: str, winner: str, reason: str, effects: list[dict], player: str = "A") -> list[dict]:
    return [
        {
            "seq": 0,
            "matchId": "m",
            "type": "match.created",
            "at": "2026-10-21T00:00:00+00:00",
            "payload": {"game": game, "players": ["A", "B"], "seed": 1, "config": {}},
        },
        {
            "seq": 1,
            "matchId": "m",
            "type": "action.applied",
            "at": "2026-10-21T00:00:01+00:00",
            "player": player,
            "payload": {"action": {"kind": "guess", "value": "x"}, "effects": effects},
        },
        {
            "seq": 2,
            "matchId": "m",
            "type": "match.ended",
            "at": "2026-10-21T00:00:02+00:00",
            "payload": {"winner": winner, "reason": reason, "finalState": {}},
        },
    ]


def test_board_match_recap_does_not_repeat_the_winner():
    """棋类引擎的 endReason 已经是「A 获胜」，战报不能再拼一遍。"""
    events = _synthetic("tic-tac-toe", "A", "A 获胜", [{"kind": "action", "action": "[4]"}])
    summary = build_recap(events)
    # 断言模板与参数，而不是某一种语言的成品句子：
    # 文案要能切换语言，键与参数才是稳定契约。
    assert summary["oneLineKey"] == "recap.win.plain"
    assert summary["oneLineParams"] == {"winner": "A"}
    assert summary["oneLine"] == "A wins."
    assert summary["highlights"], "棋类终局也必须能剪出高光"


def test_codenames_team_winner_is_readable_and_highlighted():
    """代号胜者是 red/blue，战报要翻成人话；翻刺客必须进高光。

    断言的是**参数是语言无关的引用键**，再由它渲染出两种语言的句子。
    旧版本断言"英文模板里出现红队/蓝队"，等于把"半中半英"当成了正确
    行为固化下来——那正是要修的缺陷。
    """
    from services.match.messages import localize_recap

    events = _synthetic(
        "codenames",
        "blue",
        "翻到刺客「灯塔」，红队立即判负",
        [{"kind": "reveal", "word": "灯塔", "color": "assassin", "team": "red"}],
    )
    summary = build_recap(events)
    assert summary["oneLineParams"]["winner"] == "team.blue"

    english = localize_recap(summary, "en")
    assert "Blue wins" in english["oneLine"]
    assert "Red" in english["oneLine"] and "assassin" in english["oneLine"]
    # 文案必须全英文；棋盘词是内容（这里事件里就是中文词"灯塔"），
    # 不算翻译缺陷——英文对局会由 lang 决定取英文词表。
    prose = english["oneLine"].replace("灯塔", "")
    assert not any("一" <= ch <= "鿿" for ch in prose), english["oneLine"]

    chinese = localize_recap(summary, "zh")
    assert "蓝队" in chinese["oneLine"] and "红队" in chinese["oneLine"]

    kinds = {h.kind for h in find_highlights(events)}
    assert "assassin" in kinds


def test_highlight_limit_must_be_positive_and_respected():
    """limit 是调用方契约：默认 3，超出的高光必须被截断。"""
    events = _synthetic("tic-tac-toe", "A", "A 获胜", [{"kind": "action", "action": "[4]"}])
    assert len(find_highlights(events)) <= 3
    assert len(find_highlights(events, limit=1)) == 1


def test_recap_counts_are_zero_for_empty_stream():
    """空事件流不能崩，数字必须是 0 而不是 None。"""
    summary = build_recap([])
    assert summary["turns"] == 0 and summary["asks"] == 0 and summary["guesses"] == 0
    assert summary["winner"] is None


def test_highlight_boundaries_are_exact():
    """阈值是产品规则：>=4 才算大范围收窄，==1 才算收敛，==0 才算白问。"""
    import tempfile
    from pathlib import Path

    from services.match.eventlog import EventLog

    def make(effects):
        events = [
            {"seq": 0, "matchId": "m", "type": "match.created", "at": "2026-01-01T00:00:00Z",
             "payload": {"game": "twenty-questions", "players": ["A", "B"], "seed": 1}},
            {"seq": 1, "matchId": "m", "type": "action.applied", "at": "2026-01-01T00:00:01Z",
             "player": "A", "payload": {"action": {"kind": "ask", "value": "x"}, "effects": effects}},
        ]
        return events

    # narrowed=3 不算大扫除
    assert not any(h.kind == "big_sweep" for h in find_highlights(make([
        {"kind": "answer", "value": "place=海边", "answer": "yes", "narrowed": 3, "remainingPublic": 5}]), limit=10))
    # narrowed=4 才算
    assert any(h.kind == "big_sweep" for h in find_highlights(make([
        {"kind": "answer", "value": "place=海边", "answer": "yes", "narrowed": 4, "remainingPublic": 5}]), limit=10))
    # remaining=2 不算收敛到一
    assert not any(h.kind == "narrowed_to_one" for h in find_highlights(make([
        {"kind": "answer", "value": "place=海边", "answer": "yes", "narrowed": 1, "remainingPublic": 2}]), limit=10))
    # narrowed=1 不算白问
    assert not any(h.kind == "wasted_question" for h in find_highlights(make([
        {"kind": "answer", "value": "place=海边", "answer": "yes", "narrowed": 1, "remainingPublic": 2}]), limit=10))


def test_highlight_limit_default_is_three():
    """默认只给三个瞬间：多了会稀释重点。"""
    events = []
    for index in range(10):
        events.append({"seq": index, "matchId": "m", "type": "action.applied",
                       "at": "2026-01-01T00:00:01Z", "player": "A",
                       "payload": {"action": {"kind": "guess", "value": str(index)},
                                   "effects": [{"kind": "guess", "value": str(index), "correct": True}]}})
    assert len(find_highlights(events)) == 3
    assert len(find_highlights(events, limit=10)) == 10


def test_answer_highlight_mark_text():
    """答案是否在解说里必须与 effect 一致（是/否不能反）。"""
    yes = find_highlights([{"seq": 1, "matchId": "m", "type": "action.applied",
                            "at": "2026-01-01T00:00:00Z", "player": "A",
                            "payload": {"action": {"kind": "ask", "value": "x"},
                                        "effects": [{"kind": "answer", "value": "place=海边",
                                                     "answer": "yes", "narrowed": 5, "remainingPublic": 3}]}}], limit=5)
    no = find_highlights([{"seq": 1, "matchId": "m", "type": "action.applied",
                           "at": "2026-01-01T00:00:00Z", "player": "A",
                           "payload": {"action": {"kind": "ask", "value": "x"},
                                       "effects": [{"kind": "answer", "value": "place=海边",
                                                    "answer": "no", "narrowed": 5, "remainingPublic": 3}]}}], limit=5)
    # 断言模板参数里的 answer 方向，避免依赖某一种语言的措辞。
    assert any(
        h.detail_key == "hl.big_sweep.detail" and h.detail_params["answer"] == "yes"
        for h in yes
    )
    assert any(
        h.detail_key == "hl.big_sweep.detail" and h.detail_params["answer"] == "no"
        for h in no
    )


def test_recap_accepts_winner_when_nobody_applied_an_action():
    """全程没走出合法动作、直接弃权的比赛，胜者仍必须在参赛名单里。"""
    events = [
        {
            "seq": 0,
            "matchId": "m",
            "type": "match.created",
            "at": "2026-01-01T00:00:00Z",
            "payload": {"game": "tic-tac-toe", "players": ["模型选手", "脚本选手"], "seed": 1},
        },
        {
            "seq": 1,
            "matchId": "m",
            "type": "match.ended",
            "at": "2026-01-01T00:00:01Z",
            "payload": {"winner": "脚本选手", "reason": "弃权", "finalState": {}},
        },
    ]
    recap = build_recap(events)
    assert recap["problems"] == []
    # 棋类没有"提问/报答案"这两个概念，perPlayer 只给 moves/rejected/thoughts。
    # 硬塞 asks=0 会让战报显示"提问 0 / 报答案 10"这种读起来像 bug 的数字。
    assert "asks" not in recap["perPlayer"]["脚本选手"]
    assert recap["perPlayer"]["脚本选手"]["moves"] == 0


def test_codenames_per_player_has_no_quiz_keys():
    """代号把翻牌也记成 kind=guess，perPlayer 不能因此冒出"提问/报答案"。

    真实缺陷：战报页面显示"提问 0 / 报答案 10"，读起来像数据错了。
    判断依据必须是赛制 id（twenty-questions），不是"有没有 guess 事件"。
    """
    events = [
        {"seq": 0, "matchId": "m", "type": "match.created", "at": "2026-01-01T00:00:00Z",
         "payload": {"game": "codenames", "players": ["A", "B", "C", "D"], "seed": 1}},
        {"seq": 1, "matchId": "m", "type": "action.applied", "at": "2026-01-01T00:00:01Z", "player": "A",
         "payload": {"action": {"kind": "hint", "value": "hint:海 2"},
                     "effects": [{"kind": "hint", "word": "海", "count": 2}]}},
        {"seq": 2, "matchId": "m", "type": "action.applied", "at": "2026-01-01T00:00:02Z", "player": "C",
         "payload": {"action": {"kind": "guess", "value": "pick:海岸"},
                     "effects": [{"kind": "reveal", "word": "海岸", "color": "red", "team": "red"}]}},
        {"seq": 3, "matchId": "m", "type": "match.ended", "at": "2026-01-01T00:00:03Z",
         "payload": {"winner": "red", "reason": "翻完", "finalState": {}}},
    ]
    recap = build_recap(events)
    row = recap["perPlayer"]["C"]
    assert "asks" not in row
    assert "guesses" not in row
    assert row["moves"] == 1


def test_highlights_are_chronological_not_by_weight():
    """选哪几段看权重，展示顺序按 seq——否则战报会把后半场的瞬间排在前面。"""
    def applied(seq: int, weight_kind: str) -> dict:
        if weight_kind == "assassin":
            effect = {"kind": "reveal", "word": "x", "color": "assassin", "team": "red"}
        else:
            effect = {"kind": "reveal", "word": "y", "color": "red", "team": "red"}
        return {"seq": seq, "matchId": "m", "type": "action.applied", "at": "2026-01-01T00:00:00Z",
                "player": "A", "payload": {"action": {"kind": "guess", "value": "pick:x"}, "effects": [effect]}}

    events = [applied(5, "own"), applied(90, "assassin"), applied(200, "own")]
    highlights = find_highlights(events, limit=3)
    assert [h.seq for h in highlights] == [5, 90, 200]
    assert highlights[-1].weight >= 0


# ---- 棋类高光：README 宣称「十二种赛制都有高光剪辑」 ----
# 旧实现里 _EFFECT_HIGHLIGHTS 没有 action 分支，10 个 TextArena 赛制
# 全程只能剪出 1 条 match.ended，等于「有剪辑但没内容」。


def _board_stream(game: str, moves: list[str], winner: str = "A") -> list[dict]:
    """构造一段棋类事件流：每一步一个 action.applied + state.changed。"""
    events: list[dict] = [
        {
            "seq": 0,
            "matchId": "m",
            "type": "match.created",
            "at": "2026-10-21T00:00:00+00:00",
            "payload": {"game": game, "players": ["A", "B"], "seed": 1, "config": {}},
        }
    ]
    for index, move in enumerate(moves):
        player = "A" if index % 2 == 0 else "B"
        seq = index * 2 + 1
        events.append(
            {
                "seq": seq,
                "matchId": "m",
                "type": "action.applied",
                "at": f"2026-10-21T00:00:{seq:02d}+00:00",
                "player": player,
                "payload": {"action": {"kind": "guess", "value": move}, "effects": [{"kind": "action", "action": move}]},
            }
        )
        events.append(
            {
                "seq": seq + 1,
                "matchId": "m",
                "type": "state.changed",
                "at": f"2026-10-21T00:00:{seq + 1:02d}+00:00",
                "payload": {"publicState": {"turnIndex": index + 1, "finished": False}, "deltas": []},
            }
        )
    events.append(
        {
            "seq": len(moves) * 2 + 1,
            "matchId": "m",
            "type": "match.ended",
            "at": "2026-10-21T00:01:00+00:00",
            "payload": {"winner": winner, "reason": f"{winner} 获胜", "finalState": {}},
        }
    )
    return events


def test_board_game_highlights_are_not_only_the_ending():
    """棋类必须能从过程里剪出高光，不能只剩「比赛结束」一条。"""
    events = _board_stream("othello", [f"[{i}]" for i in range(12)])
    highlights = find_highlights(events, limit=6)
    kinds = {h.kind for h in highlights}
    assert kinds - {"end"}, f"棋类高光只有终局：{kinds}"


def test_board_game_highlights_are_deterministic():
    """同一份事件流两次必须给出同一份高光（导播与剪辑都不得含随机）。"""
    events = _board_stream("nim", ["[0 1]", "[1 2]", "[2 1]"])
    first = [h.as_dict() for h in find_highlights(events, limit=5)]
    second = [h.as_dict() for h in find_highlights(events, limit=5)]
    assert first == second


def test_board_game_clips_cover_more_than_the_tail():
    """剪辑窗口必须落在过程里，不能永远贴着最后 3 条事件。"""
    from services.match.clips import build_clips

    events = _board_stream("crusade", [f"[a{i} b{i}]" for i in range(20)])
    clips = build_clips(events, limit=3)
    assert len(clips) >= 2, f"20 步的棋局只剪出 {len(clips)} 段"
    assert min(clip.startSeq for clip in clips) < events[-1]["seq"] - 6


# ---- 逐手耗时 / 用量：战报必须能回答"这一手为什么慢" ----


def _stream_with_timing() -> list[dict]:
    events = _board_stream("tic-tac-toe", ["[0]", "[4]", "[8]"])
    timings = [1200, 340, 2100]
    applied = [e for e in events if e["type"] == "action.applied"]
    for event, value in zip(applied, timings, strict=True):
        event["payload"]["elapsedMs"] = value
    applied[-1]["payload"]["usage"] = {"requests": 1, "total_tokens": 512}
    return events


def test_recap_reports_real_turn_timing():
    summary = build_recap(_stream_with_timing())
    timing = summary["timing"]
    assert timing["elapsedMsTotal"] == 3640
    assert timing["elapsedMsMax"] == 2100
    assert timing["tokensTotal"] == 512
    assert timing["slowestTurn"]["elapsedMs"] == 2100


def test_recap_timing_is_zero_when_unobserved():
    """脚本选手没有耗时数据：必须是 0/null，不能编一个看起来合理的均值。"""
    events = _board_stream("tic-tac-toe", ["[0]", "[4]"])
    timing = build_recap(events)["timing"]
    assert timing["elapsedMsTotal"] == 0 and timing["elapsedMsMax"] == 0
    assert timing["slowestTurn"] is None


def test_recap_timing_does_not_count_zero_placeholders():
    """elapsedMs=0 表示"没量到"，不能参与最慢一手与均值。"""
    events = _board_stream("tic-tac-toe", ["[0]", "[4]"])
    applied = [e for e in events if e["type"] == "action.applied"]
    for event in applied:
        event["payload"]["elapsedMs"] = 0
    timing = build_recap(events)["timing"]
    assert timing["elapsedMsMax"] == 0 and timing["slowestTurn"] is None


def test_action_applied_timing_survives_contract_check():
    """新增字段必须符合 JSON Schema，否则写库时会被契约告警。"""
    from services.match.contract import check

    event = {
        "seq": 1,
        "matchId": "m",
        "type": "action.applied",
        "at": "2026-10-21T00:00:01+00:00",
        "player": "A",
        "payload": {
            "action": {"kind": "guess", "value": "[4]"},
            "effects": [{"kind": "action", "action": "[4]"}],
            "elapsedMs": 1234,
            "usage": {"requests": 1, "total_tokens": 99},
        },
    }
    check(event)
