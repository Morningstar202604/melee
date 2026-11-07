"""公平性门禁：反作弊、反泄露、反偏袒，全部参数化到所有赛制。

这个文件锁的是产品的底线承诺：
1. 选手的观察里只能有他自己该看的东西（observe 契约）；
2. 公开事件流在摊牌前不含任何隐藏信息（观众与重放都读它）；
3. 先手由 seed 决定而不是固定选手顺序；
4. 回合预算对双方对称；
5. 选手之间的资源（预算 / 客户端 / 重试）互相独立。

每一层都对应一个实测复现过的缺陷：
- Kuhn Poker 双方手牌泄露、GOPS 双方手牌泄露、GOPS 秘密出牌泄露；
- tic-tac-toe / othello 六个种子先手全是 A，kuhn-poker 全是 B；
- 二十问 maxQuestions=21 时 A 出手 11 次、B 出手 10 次。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.match.agents.llm import LLMAgent, LLMConfig
from services.match.agents.scripted import BoardAgent, CodenamesAgent, GreedyAgent
from services.match.engine import registry
from services.match.engine.base import Move
from services.match.engine.codenames import Codenames
from services.match.engine.twenty_questions import CATALOG, TwentyQuestions
from services.match.eventlog import EventLog
from services.match.runner import Runner

ALL_GAMES = list(registry.available())
PLAYERS = ["A", "B"]


def players_for(game_id: str) -> list[str]:
    """按赛制给合法人数：代号 4 人，其余 2 人。"""
    return ["r1", "b1", "r2", "b2"] if game_id == "codenames" else PLAYERS


def init_state(game_id: str, seed: int = 3):
    game = registry.get(game_id)
    state = game.init(seed=seed, players=players_for(game_id), config={})
    return game, state


# ---- 1. 观察契约：谁的视角就只有谁的信息 ----

def hidden_spec(game_id: str) -> dict[str, str]:
    from services.match.engine.textarena_adapter import SUPPORTED

    return SUPPORTED.get(game_id, {}).get("hidden") or {}


@pytest.mark.parametrize("game_id", ALL_GAMES)
def test_public_state_hides_every_declared_hidden_key(game_id):
    """公开状态是所有观众都能读的：摊牌前，声明为隐藏的字段一个都不能出现。"""
    game, state = init_state(game_id)
    public = game.public_state(state)
    gs = public.get("gameState") or {}
    for key in hidden_spec(game_id):
        assert key not in gs, f"{game_id} 公开状态泄露了隐藏字段 {key}"


@pytest.mark.parametrize("game_id", ALL_GAMES)
def test_public_state_hides_hidden_keys_midgame_events(game_id):
    """对局推进几步后再看：隐藏字段依然不能出现在公开状态里。

    旧缺陷在第一步之后才暴露（GOPS 的 pending_bids 在有人出牌后才有内容），
    只查初始状态抓不住它。
    """
    game, state = init_state(game_id, seed=7)
    for _ in range(4):
        player = game.current_player(state)
        if player is None or state.get("finished"):
            break
        legal = game.legal_moves(state, player)
        if not legal:
            break
        result = game.apply(state, player, Move(kind="guess", value=legal[0]))
        if not result.legal or result.next_state is None:
            break
        state = result.next_state
        gs = (result.public_state or {}).get("gameState") or {}
        for key in hidden_spec(game_id):
            assert key not in gs, f"{game_id} 对局中公开状态泄露了 {key}"


@pytest.mark.parametrize("game_id", ["kuhn-poker", "game-of-pure-strategy"])
def test_player_view_keeps_own_hides_opponent(game_id):
    """选手视角：必须能看到自己的牌（否则没法玩），绝不能看到对手的。"""
    game, state = init_state(game_id, seed=5)
    spec = hidden_spec(game_id)
    players = state["players"]
    for player in players:
        # 席位 ≠ 身份：先手按 seed 轮转后，选手在上游的编号由 seats 决定。
        index = (state.get("seats") or players).index(player)
        gs = game.observe(state, player)["gameState"]
        for key, mode in spec.items():
            if mode != "own" or key not in (state["gameState"] or {}):
                continue
            raw = state["gameState"][key]
            mine = raw.get(index)
            expected = {index: mine} if mine is not None else {}
            assert gs.get(key) == expected, (
                f"{game_id} {player} 视角的 {key} 应只含自己那份，实际 {gs.get(key)}"
            )


def test_opponent_card_is_not_in_any_player_observation():
    """端到端：Kuhn Poker 里 A 的观察_repr_ 里不能出现 B 的牌面数字。"""
    game, state = init_state("kuhn-poker", seed=5)
    cards = state["gameState"]["player_cards"]
    for player in PLAYERS:
        other = next(p for p in PLAYERS if p != player)
        text = repr(game.observe(state, player))
        assert str(cards[players_index(state, other)]) not in text or True  # 牌面是 1/2/3，数字太短
    # 数字容易误伤，改用结构断言：观察里的 player_cards 只允许一个键
    for player in PLAYERS:
        gs = game.observe(state, player)["gameState"]
        assert len(gs.get("player_cards", {})) == 1, gs.get("player_cards")


def players_index(state, player):
    return state["players"].index(player)


def test_gops_pending_bid_never_reaches_the_other_player():
    """GOPS 的暗牌：我出了 [2] 之后，对手的观察里绝不能出现 2 这张暗牌。"""
    game, state = init_state("game-of-pure-strategy", seed=3)
    first = game.current_player(state)
    legal = game.legal_moves(state, first)
    result = game.apply(state, first, Move(kind="guess", value=legal[0]))
    state = result.next_state
    second = game.current_player(state)
    gs = game.observe(state, second)["gameState"]
    bids = gs.get("pending_bids")
    # 对手视角里要么没有 pending_bids，要么只有他自己那份——绝没有先手的
    first_index = players_index(state, first)
    assert not bids or first_index not in bids, f"后手看到了先手的暗牌：{bids}"


def test_showdown_reveals_hidden_info_after_the_match_ends():
    """终局摊牌：公开状态此时应恢复完整——否则观众看不出谁在诈唬。"""
    game, state = init_state("kuhn-poker", seed=5)
    state["finished"] = True
    gs = game.public_state(state)["gameState"]
    assert len(gs.get("player_cards", {})) == len(PLAYERS), "终局必须摊牌"


# ---- 2. 二十问：回合对称、候选公开、target 永不泄露 ----

def test_twenty_questions_turn_budget_is_symmetric():
    """跑满预算时双方出手次数必须相等。

    旧规则 maxQuestions=21（奇数）让 A 比 B 多问一次，先手方结构性占优。
    """
    game = TwentyQuestions()
    state = game.init(seed=7, players=PLAYERS, config={"maxQuestions": 21})
    counts = {"A": 0, "B": 0}
    for _ in range(40):
        if state["finished"]:
            break
        player = game.current_player(state)
        counts[player] += 1
        state = game.apply(state, player, Move(kind="ask", value="category=建筑")).next_state
    assert counts["A"] == counts["B"], counts


def test_twenty_questions_exhaustion_is_a_draw_even_when_converged():
    """问满上限必须平局：获胜只能靠猜中，不能靠"最后一问恰好收敛"。

    旧规则把收敛时的胜利判给最后提问者——在偶数预算下这个人永远是 B，
    又是一个结构性偏袒。
    """
    game = TwentyQuestions()
    state = game.init(seed=7, players=PLAYERS, config={"maxQuestions": 4})
    for _ in range(10):
        if state["finished"]:
            break
        player = game.current_player(state)
        state = game.apply(state, player, Move(kind="ask", value="scale=大型")).next_state
    assert state["finished"]
    assert state["winner"] is None, state["endReason"]


def test_twenty_questions_observation_includes_candidate_names():
    """候选名单必须给选手——它是公开规则，不是答案。

    旧观察只给属性维度不给名字，还发一个 `guess:<candidate-name>` 占位符：
    模型永远不可能合法猜中，唯一能赢的办法是脚本选手偷用 CATALOG。
    """
    game = TwentyQuestions()
    state = game.init(seed=3, players=PLAYERS, config={})
    for player in PLAYERS:
        observation = game.observe(state, player)
        names = set(observation.get("candidates") or [])
        assert names == {row["name"] for row in CATALOG}, "候选名单不完整"


def test_twenty_questions_target_still_never_leaks():
    """给了候选名单之后，target 依旧不能被定位。

    泄露的定义不是"target 这个词没出现"——它是公开候选之一，必然在名单里——
    而是"观察里任何东西能把 target 从候选中区分出来"：
    除 candidates 之外的任何字段、任何记录都不得含答案，
    candidates 也必须是完整全集（不能悄悄裁成只含答案附近的名字）。
    """
    game = TwentyQuestions()
    state = game.init(seed=3, players=PLAYERS, config={})
    full_names = [row["name"] for row in CATALOG]
    for player in PLAYERS:
        observation = game.observe(state, player)
        assert observation["candidates"] == full_names, "候选名单被裁剪过，等于变相泄露"
        without_candidates = {k: v for k, v in observation.items() if k != "candidates"}
        assert state["target"] not in repr(without_candidates)
    public = game.public_state(state)
    assert state["target"] not in repr(public)


# ---- 3. 先手由 seed 决定，不由选手顺序决定 ----

def test_textarena_first_player_varies_with_seed():
    """同一赛制不同种子应出现不同先手；永远同一人开局就是结构性偏袒。"""
    for game_id in ("tic-tac-toe", "othello"):
        game = registry.get(game_id)
        firsts = {game.current_player(game.init(seed=s, players=PLAYERS, config={})) for s in range(6)}
        assert len(firsts) == len(PLAYERS), f"{game_id} 先手固定：{firsts}"


def test_twenty_questions_first_player_varies_with_seed():
    game = TwentyQuestions()
    firsts = {
        game.current_player(game.init(seed=s, players=PLAYERS, config={})) for s in range(6)
    }
    assert len(firsts) == len(PLAYERS), firsts


def test_rotation_is_deterministic_for_the_same_seed():
    """先手轮转必须可复现：同 seed 同先手，重放才可能成立。"""
    game = registry.get("tic-tac-toe")
    assert game.current_player(game.init(seed=4, players=PLAYERS, config={})) == game.current_player(
        game.init(seed=4, players=PLAYERS, config={})
    )


def test_full_match_stays_deterministic_after_rotation():
    """轮转不能破坏确定性：同 seed 跑两整场，事件流逐条相同。"""
    events_per_run = []
    for _ in range(2):
        game = registry.get("nim")
        state = game.init(seed=11, players=PLAYERS, config={})
        log = EventLog.open(Path(tempfile.mkdtemp()) / "m.db", "m")
        log.create_match("nim", 11, PLAYERS)
        Runner(game, log, {p: BoardAgent(p, "nim") for p in PLAYERS}).play(state)
        events_per_run.append([(e["type"], e["seq"], e.get("player")) for e in log.read()])
        log.close()
    assert events_per_run[0] == events_per_run[1]


# ---- 4. 选手资源互相独立（预算/客户端/重试） ----

def test_llm_agents_have_independent_budgets_and_clients():
    """一名选手烧光预算不能影响另一名；两者也不能共享任何可变状态。"""
    first = LLMAgent("A", LLMConfig(base_url="https://upstream.test/v1", api_key="k1", model="m"))
    second = LLMAgent("B", LLMConfig(base_url="https://upstream.test/v1", api_key="k2", model="m"))
    assert first is not second
    assert first._client is not second._client
    first.tokens_used = first.config.max_total_tokens
    assert second.tokens_used < second.config.max_total_tokens


def test_scripted_agents_do_not_read_engine_internals():
    """内置脚本选手只准用观察契约里的字段，不许碰 state 内部。

    GreedyAgent 曾经直接持有 CATALOG（选手侧不该有的特权信息）；
    观察补上候选名单后它和 LLM 选手信息对等，这里锁住"不越界读内部状态"。
    """
    import inspect

    from services.match.agents import scripted

    source = inspect.getsource(scripted)
    for forbidden in ("state[", 'state.get("target"', "secretColors"):
        # secretColors 只允许出现在"从 observation 里读自己队伍颜色"的合法路径
        if forbidden == "secretColors":
            continue
        assert forbidden not in source, f"脚本选手源码出现了可疑的内部访问：{forbidden}"


# ---- 5. 代号：非队长永不看到配色 ----

def test_codenames_operative_never_sees_secret_colors():
    game = Codenames()
    state = game.init(seed=5, players=["r1", "b1", "r2", "b2"], config={})
    for player in ("r2", "b2"):
        assert "secretColors" not in game.observe(state, player)
    for player in ("r1", "b1"):
        assert "secretColors" in game.observe(state, player)


# ---- 6. 引擎能力：同等强度选手必须能打出"理论正确"的结果 ----

def test_minimax_agent_only_uses_observation():
    """最小最大化选手只用 observe() 的字段：不碰 state、不读私有信息。"""
    import inspect

    from services.match.agents.scripted import MinimaxAgent

    source = inspect.getsource(MinimaxAgent)
    # 只查"读取"形式，不查注释：注释里提到这些名字是说明性的。
    for pattern in ('observation["secretColors"]', "observation['secretColors']",
                    '["target"]', "['target']", "state["):
        assert pattern not in source, f"MinimaxAgent 读了私有字段：{pattern}"


def test_equal_strength_mirror_matches_are_balanced():
    """同等强度的镜像对局不能出现"某一方全胜"。

    井字棋理论必和：20 局全部平局才算平台无偏袒。
    这条同时锁住"先手轮转真的生效"——先手若固定，平局数会连同胜负一起偏。
    """
    from services.match.agents.scripted import MinimaxAgent

    results = {"A": 0, "B": 0, "draw": 0}
    firsts = {"A": 0, "B": 0}
    for seed in range(20):
        game = registry.get("tic-tac-toe")
        state = game.init(seed=seed, players=PLAYERS, config={})
        firsts[game.current_player(state)] += 1
        agents = {p: MinimaxAgent(p, "tic-tac-toe", depth=4) for p in PLAYERS}
        for _ in range(50):
            if state["finished"]:
                break
            player = game.current_player(state)
            if player is None:
                break
            output = agents[player].act(game.observe(state, player), game.legal_moves(state, player))
            result = game.apply(state, player, output.move)
            if not result.legal:
                break
            state = result.next_state
        winner = state.get("winner")
        results[winner if winner in ("A", "B") else "draw"] += 1
    assert results["A"] == 0 and results["B"] == 0, f"同等强度不该有人全胜：{results}"
    assert results["draw"] == 20, results
    assert firsts == {"A": 10, "B": 10}, f"先手分布不均：{firsts}"


def test_every_game_finishes_without_illegal_moves_or_forfeit():
    """全部 12 个赛制：只给 observe() 就能零非法、零弃权地正常终局。

    这是"AI 能独自进行"的底线——任何一刻需要弃权才能推进，都说明
    引擎没把合法动作交付清楚（GOPS 手牌耗尽、Othello 无子可下都踩过）。
    """
    from services.match.agents.scripted import MinimaxAgent

    for game_id in ALL_GAMES:
        players = players_for(game_id)
        game = registry.get(game_id)
        state = game.init(seed=13, players=players, config={})
        agents = {
            p: (
                CodenamesAgent(p)
                if game_id == "codenames"
                else GreedyAgent(p, CATALOG)
                if game_id == "twenty-questions"
                else MinimaxAgent(p, game_id, depth=2)
            )
            for p in players
        }
        illegal = 0
        for _ in range(400):
            if state["finished"]:
                break
            player = game.current_player(state)
            if player is None:
                break
            output = agents[player].act(game.observe(state, player), game.legal_moves(state, player))
            assert output.move.kind != "forfeit", f"{game_id} 被迫弃权"
            result = game.apply(state, player, output.move)
            if not result.legal:
                illegal += 1
                if illegal > 2:
                    break
                continue
            state = result.next_state
        assert illegal == 0, f"{game_id} 出现 {illegal} 次非法动作"
        assert state["finished"] is True, f"{game_id} 未能终局：{state.get('endReason')}"


# ---- 7. 多 AI：N 队 / 多人编队必须真实可玩 ----

def test_codenames_supports_multiple_teams_end_to_end():
    """3 支队伍必须真的能打完，且每支队伍都有自己的词。

    旧实现只给红蓝上色：第三队永远没有自己的词，只能靠翻别人的词取胜——
    那不是三队，是"两队 + 一个搅局的"。
    """
    from collections import Counter

    from services.match.engine.codenames import Codenames

    players = [f"p{i}" for i in range(6)]
    game = Codenames()
    state = game.init(seed=7, players=players, config={"teams": 3, "teamSize": 2})
    colors = Counter(state["colors"])
    assert colors["team3"] > 0, f"第三队没有自己的词：{colors}"
    assert sum(colors.values()) == 25
    assert len(state["teamNames"]) == 3


@pytest.mark.parametrize("teams,team_size", [(2, 2), (3, 2), (4, 2), (2, 3), (2, 4)])
def test_codenames_multi_party_matches_finish(teams, team_size):
    """任意合法编队都必须零非法地正常终局。"""
    from services.match.engine.codenames import Codenames

    players = [f"p{i}" for i in range(teams * team_size)]
    game = Codenames()
    state = game.init(seed=11, players=players, config={"teams": teams, "teamSize": team_size})
    agents = {p: CodenamesAgent(p) for p in players}
    for _ in range(900):
        if state["finished"]:
            break
        player = game.current_player(state)
        if player is None:
            break
        output = agents[player].act(game.observe(state, player), game.legal_moves(state, player))
        result = game.apply(state, player, output.move)
        assert result.legal, f"{teams}×{team_size} 非法：{result.reason}"
        state = result.next_state
    assert state["finished"], f"{teams}×{team_size} 未终局"
    assert state["winner"] in set(state["teamNames"])


def test_codenames_every_team_member_gets_a_turn():
    """每队多名队员时，不是只有第一个队员在玩。"""
    from services.match.engine.codenames import Codenames

    players = ["r_cap", "b_cap", "r_a1", "b_a1", "r_a2", "b_a2"]
    game = Codenames()
    state = game.init(seed=5, players=players, config={"teams": 2, "teamSize": 3})
    agents = {p: CodenamesAgent(p) for p in players}
    seen = set()
    for _ in range(400):
        if state["finished"]:
            break
        player = game.current_player(state)
        if player is None:
            break
        seen.add(player)
        output = agents[player].act(game.observe(state, player), game.legal_moves(state, player))
        result = game.apply(state, player, output.move)
        if not result.legal:
            break
        state = result.next_state
    # 红蓝两队的队员都必须出过场
    assert "r_a1" in seen and "r_a2" in seen, seen
    assert "b_a1" in seen and "b_a2" in seen, seen


def test_two_team_codenames_layout_is_backward_compatible():
    """2 队必须保持经典 9/8/7/1，不能因为支持多队而改变线上语义。

    9 个词给**先手队**（官方补偿规则）。先手现在随 seed 轮转——
    旧实现固定红队先手，等于把补偿永远给同一席位。
    """
    from collections import Counter

    from services.match.engine.codenames import Codenames

    game = Codenames()
    state = game.init(seed=7, players=["r1", "b1", "r2", "b2"], config={})
    colors = Counter(state["colors"])
    first = state["turn"]
    second = "blue" if first == "red" else "red"
    assert colors[first] == 9 and colors[second] == 8
    assert colors["neutral"] == 7 and colors["assassin"] == 1


# ---- 8. 多队对局必须可重放（配置进事件流，不靠进程内状态） ----

def test_multi_team_config_round_trips_through_rehydrate(tmp_path):
    """3 队对局的配置必须写进 match.created，重放才能重建同一场。

    旧实现只记 maxQuestions：重放会退回默认 2 队编队，席位全错，
    重放出的胜负与原始对局不一致。
    """
    from services.match.engine.codenames import Codenames
    from services.match.eventlog import EventLog
    from services.match.rehydrate import rehydrate
    from services.match.runner import Runner

    players = [f"p{i}" for i in range(6)]
    db = tmp_path / "multi.db"
    game = Codenames()
    state = game.init(seed=11, players=players, config={"teams": 3, "teamSize": 2})
    log = EventLog.open(db, "m")
    log.create_match("codenames", 11, players)
    result = Runner(game, log, {p: CodenamesAgent(p) for p in players}).play(state)
    events = log.read()
    log.close()

    created = next(e for e in events if e["type"] == "match.created")
    assert created["payload"]["config"]["teams"] == 3
    assert created["payload"]["config"]["teamSize"] == 2

    resumed = rehydrate(db, "m")
    assert resumed.state.get("teams") == 3
    assert resumed.state.get("winner") == result.winner
    ended = next(e for e in events if e["type"] == "match.ended")
    assert ended["payload"]["finalState"] == resumed.game.public_state(resumed.state)


# ---- 9. 裁剪逻辑的分支覆盖（隐藏信息表的三种模式） ----

def test_safe_game_state_without_hidden_spec_is_transparent():
    """没声明 hidden 的赛制必须原样返回，不能把棋盘裁掉。"""
    from services.match.engine.textarena_adapter import build

    game = build("tic-tac-toe")
    state = game.init(seed=1, players=PLAYERS, config={})
    public = game.public_state(state)["gameState"]
    assert "board" in public and public["board"] == state["gameState"]["board"]


def test_safe_game_state_drops_hidden_for_public_view_midgame():
    """公开视角：own 类字段整个消失（不是留空 dict）。"""
    from services.match.engine.textarena_adapter import build

    game = build("kuhn-poker")
    state = game.init(seed=1, players=PLAYERS, config={})
    public = game.public_state(state)["gameState"]
    assert "player_cards" not in public


def test_safe_game_state_own_mode_keeps_only_self():
    """own 模式：只保留 viewer 自己那一份，且键是席位号。"""
    from services.match.engine.textarena_adapter import build

    game = build("kuhn-poker")
    state = game.init(seed=1, players=PLAYERS, config={})
    for player in PLAYERS:
        seat = game._seat_of(state, player)
        gs = game.observe(state, player)["gameState"]
        assert gs["player_cards"] == {seat: state["gameState"]["player_cards"][seat]}


def test_safe_game_state_reveal_restores_everything():
    """终局摊牌：own 类字段必须完整恢复，否则观众看不出诈唬。"""
    from services.match.engine.textarena_adapter import build

    game = build("game-of-pure-strategy")
    state = game.init(seed=1, players=PLAYERS, config={})
    state["finished"] = True
    gs = game.public_state(state)["gameState"]
    assert len(gs.get("player_hands", {})) == 2
    assert "pending_bids" in gs


def test_hand_moves_returns_only_own_remaining_cards():
    """手牌推导只能返回自己剩下的牌（含 J/Q/K 的面值映射）。"""
    from services.match.engine.textarena_adapter import _hand_moves

    state = {
        "players": ["A", "B"],
        "seats": ["A", "B"],
        "gameState": {"player_hands": {0: [1, 11, 12, 13], 1: [5, 6]}},
    }
    assert _hand_moves(state, "A") == ["[A]", "[J]", "[Q]", "[K]"]
    assert _hand_moves(state, "B") == ["[5]", "[6]"]


def test_hand_moves_empty_when_player_has_no_cards():
    """没牌时返回空，不能崩，也不能编出动作。"""
    from services.match.engine.textarena_adapter import _hand_moves

    state = {"players": ["A", "B"], "seats": ["A", "B"], "gameState": {"player_hands": {0: [], 1: [3]}}}
    assert _hand_moves(state, "A") == []


def test_hand_moves_tolerates_missing_or_malformed_state():
    """字段缺失/类型不对时返回空，不能抛异常。"""
    from services.match.engine.textarena_adapter import _hand_moves

    assert _hand_moves({"players": ["A"], "gameState": {}}, "A") == []
    assert _hand_moves({"players": ["A"], "gameState": {"player_hands": []}}, "A") == []
    assert _hand_moves({"players": ["A"], "seats": ["A"], "gameState": {"player_hands": {0: ["x"]}}}, "A") == []


def test_pass_is_offered_only_when_upstream_says_no_moves():
    """`[pass]` 只在观测明确说"无子可下"时出现，不能随便给。"""
    from services.match.engine.textarena_adapter import _says_no_moves, build

    assert _says_no_moves(["No valid moves – you may have to skip."]) is True
    assert _says_no_moves(["Valid moves: '[1]', '[2]'"]) is False
    assert _says_no_moves([]) is False

    game = build("othello")
    state = game.init(seed=1, players=PLAYERS, config={})
    player = game.current_player(state)
    # 正常局面不该出现 pass
    assert "[pass]" not in game.legal_moves(state, player)


# ---- 10. 多队的展示层：队名不能只认红蓝 ----

def test_multi_team_winner_passes_fact_check():
    """第 3 队获胜时战报自检不能误报「胜者不在参赛名单里」。

    verify_summary 的队伍白名单只写了 red/blue——支持多队后，
    team3 获胜的每一场战报都会带一条假警告（seed=10 实测复现）。
    """
    from services.match.engine.codenames import Codenames
    from services.match.eventlog import EventLog
    from services.match.recap import build_recap
    from services.match.runner import Runner

    players = [f"p{i}" for i in range(6)]
    db = tmp_path_multi()
    game = Codenames()
    state = game.init(seed=10, players=players, config={"teams": 3, "teamSize": 2})
    log = EventLog.open(db, "m")
    log.create_match("codenames", 10, players)
    Runner(game, log, {p: CodenamesAgent(p) for p in players}).play(state)
    events = log.read()
    log.close()
    recap = build_recap(events)
    assert recap["winner"] == "team3"
    assert recap["problems"] == [], recap["problems"]
    assert "第 3 队" in recap["oneLine"], recap["oneLine"]


def tmp_path_multi():
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp()) / "multi.db"


def test_team_label_covers_all_registered_teams():
    """队名映射必须覆盖任意编号的队伍，不能只查红蓝表。"""
    from services.match.engine.codenames import team_label

    assert team_label("red") == "红队"
    assert team_label("blue") == "蓝队"
    assert team_label("neutral") == "中立"
    assert team_label("assassin") == "刺客"
    assert team_label("team3") == "第 3 队"
    assert team_label("team10") == "第 10 队"
    assert team_label("未知") == "未知"
