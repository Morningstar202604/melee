from __future__ import annotations

import pytest

from services.match.engine import registry
from services.match.engine import textarena_adapter as adapter
from services.match.engine.base import Move

requires_textarena = pytest.mark.skipif(
    not adapter.is_available(), reason="未安装 textarena"
)


def play(game_id: str, moves: list[str], players=("A", "B"), state=None):
    game = registry.get(game_id)
    if state is None:
        state = game.init(seed=1, players=list(players), config={})
    for value in moves:
        current = game.current_player(state)
        if current is None:
            break
        result = game.apply(state, current, Move("guess", value))
        if not result.legal:
            return game, state, result
        state = result.next_state
    return game, state, None


# ---------- 注册表 ----------

def test_registry_always_has_twenty_questions():
    assert "twenty-questions" in registry.available()


def test_registry_returns_new_instance_each_time():
    first = registry.get("twenty-questions")
    second = registry.get("twenty-questions")
    assert first is not second


def test_unknown_game_raises():
    with pytest.raises(KeyError):
        registry.get("no-such-game")


def test_catalog_entries_are_well_formed():
    for entry in registry.catalog():
        assert entry["gameId"]
        assert entry["label"]
        assert len(entry["players"]) == 2
        assert entry["players"][0] <= entry["players"][1]


def test_twenty_questions_player_range_is_two():
    assert registry.player_range("twenty-questions") == (2, 2)


# ---------- 适配器：安装相关 ----------

@requires_textarena
def test_textarena_games_are_registered():
    assert "tic-tac-toe" in registry.available()


@requires_textarena
def test_adapter_rejects_wrong_player_count():
    game = registry.get("tic-tac-toe")
    with pytest.raises(ValueError):
        game.init(seed=1, players=["A"], config={})


# ---------- 适配器核心：纯函数、合法性、终局 ----------

@requires_textarena
def test_apply_does_not_mutate_previous_state():
    """apply 必须是纯的：旧 state 的棋盘不能变。

    选手身份一律从 current_player() 取——先手由 seed 轮转，
    写死 "A" 等于把"永远 A 先手"这个旧偏袒固化进测试。
    """
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    before = repr(state["gameState"])
    first = game.current_player(state)

    result = game.apply(state, first, Move("guess", "4"))
    assert result.legal
    assert repr(state["gameState"]) == before, "旧 state 被就地修改了"


@requires_textarena
def test_env_instances_are_not_shared_between_states():
    """每一步都要用独立的 env，否则历史 state 会跟着一起前进。"""
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    p1 = game.current_player(state)
    first = game.apply(state, p1, Move("guess", "4")).next_state
    p2 = game.current_player(first)
    second = game.apply(first, p2, Move("guess", "0")).next_state

    assert first["env"] is not second["env"]
    assert first["gameState"] != second["gameState"]


@requires_textarena
def test_occupied_cell_is_rejected():
    game, state, rejection = play("tic-tac-toe", ["4", "0", "4"])
    assert rejection is not None and not rejection.legal


@requires_textarena
def test_non_numeric_move_is_rejected():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    result = game.apply(state, game.current_player(state), Move("guess", "左上角"))
    assert not result.legal
    assert result.retryable


@requires_textarena
def test_out_of_turn_move_is_rejected():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    # 轮到谁由 seed 决定；"另一位"才是越位的那一个。
    current = game.current_player(state)
    other = next(p for p in ["A", "B"] if p != current)
    result = game.apply(state, other, Move("guess", "4"))
    assert not result.legal


@requires_textarena
def test_winning_line_sets_winner():
    """先手方连成三子获胜——先手是谁由 seed 决定，不再硬编码 A。"""
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    first = game.current_player(state)
    game, state, rejection = play("tic-tac-toe", ["3", "0", "4", "1", "5"], state=state)
    assert rejection is None
    assert state["finished"] is True
    assert state["winner"] == first, state["endReason"]


@requires_textarena
def test_forfeit_ends_match():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    quitter = game.current_player(state)
    other = next(p for p in ["A", "B"] if p != quitter)
    result = game.apply(state, quitter, Move("forfeit", ""))
    assert result.legal
    assert result.next_state["finished"] is True
    assert result.next_state["winner"] == other


@requires_textarena
def test_finished_match_rejects_further_moves():
    game, state, _ = play("tic-tac-toe", ["3", "0", "4", "1", "5"])
    result = game.apply(state, "A", Move("guess", "8"))
    assert not result.legal


@requires_textarena
def test_observation_hides_nothing_for_board_games():
    """井字棋是公开信息，两份观察都应包含同一个棋盘。"""
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    a = game.observe(state, "A")
    b = game.observe(state, "B")
    assert a["gameState"] == b["gameState"]


@requires_textarena
def test_only_current_player_gets_observation_text():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    current = game.current_player(state)
    other = "B" if current == "A" else "A"

    assert game.observe(state, current)["isYourTurn"] is True
    assert game.observe(state, other)["isYourTurn"] is False
    assert game.observe(state, other)["observations"] == []


@requires_textarena
def test_unknown_player_observation_raises():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=1, players=["A", "B"], config={})
    with pytest.raises(KeyError):
        game.observe(state, "Z")


# ---------- 多个赛制都能开局 ----------

@requires_textarena
@pytest.mark.parametrize("game_id", list(adapter.SUPPORTED))
def test_each_supported_game_can_start(game_id):
    game = registry.get(game_id)
    state = game.init(seed=7, players=["A", "B"], config={})
    # 先手由各环境自己决定（库恩扑克按轮次轮换），这里只要求是登记过的选手。
    assert game.current_player(state) in {"A", "B"}
    assert game.public_state(state)["finished"] is False


# ---------- 默认选手：必须能零犯规打完 ----------

@requires_textarena
@pytest.mark.parametrize("game_id", ["tic-tac-toe", "connect-four", "othello", "nim"])
@pytest.mark.parametrize("seed", [1, 5])
def test_board_agent_finishes_without_illegal_moves(game_id, seed):
    """这是回归锁：之前三个赛制都会在中途犯规弃权。"""
    from services.match.agents.scripted import BoardAgent

    game = registry.get(game_id)
    state = game.init(seed=seed, players=["A", "B"], config={})

    for _ in range(200):
        if state["finished"]:
            break
        current = game.current_player(state)
        observation = game.observe(state, current)
        output = BoardAgent(current, game_id).act(observation, [])
        result = game.apply(state, current, output.move)
        assert result.legal, f"{game_id} 第 {state.get('turnIndex', '?')} 步非法：{result.reason}"
        state = result.next_state

    assert state["finished"] is True
    assert state["winner"] in {"A", "B"}


@requires_textarena
def test_extract_moves_reads_only_latest_observation():
    """观察是累积的：旧动作不能再被当成可用动作。"""
    from services.match.agents.scripted import extract_moves

    observations = [
        "Available Moves: '[0]', '[1]'",
        "Available Moves: '[2]', '[3]'",
    ]
    assert extract_moves(observations) == ["[2]", "[3]"]


def test_normalize_action_accepts_non_numeric_brackets():
    from services.match.engine.textarena_adapter import normalize_action

    assert normalize_action("[col 1]") == "[col 1]"
    assert normalize_action("[2,3]") == "[2,3]"
    assert normalize_action("[1 2]") == "[1 2]"
    assert normalize_action("乱写") is None


@requires_textarena
@pytest.mark.parametrize("game_id", list(adapter.SUPPORTED))
def test_legal_moves_are_actually_legal(game_id):
    """枚举出的每个动作都必须真能落子：给模型的提示不能是幻觉。"""
    from services.match.agents.scripted import BoardAgent

    game = registry.get(game_id)
    state = game.init(seed=3, players=["A", "B"], config={})
    player = game.current_player(state)
    moves = game.legal_moves(state, player)
    assert moves, f"{game_id} 没枚举出动作"
    for value in moves[:4]:
        result = game.apply(state, player, Move("guess", value))
        assert result.legal, f"{game_id} 的合法列表里混进了非法动作 {value}：{result.reason}"

    # 对手回合不该看到动作列表
    other = "B" if player == "A" else "A"
    assert game.legal_moves(state, other) == []


@requires_textarena
def test_legal_moves_exclude_taken_cells():
    game = registry.get("tic-tac-toe")
    state = game.init(seed=3, players=["A", "B"], config={})
    mover = game.current_player(state)
    first = game.legal_moves(state, mover)[0]
    result = game.apply(state, mover, Move("guess", first))
    state = result.next_state
    assert first not in game.legal_moves(state, game.current_player(state))


def test_probe_reports_failure_for_unknown_env():
    """探测脚本遇到不存在的环境要返回 ok=False + 原因，而不是抛异常。"""
    from services.match.engine import textarena_probe

    if not adapter.is_available():
        pytest.skip("未安装 textarena")
    outcome = textarena_probe.probe("NoSuchGame-v0")
    assert outcome["ok"] is False
    assert outcome["reason"].startswith("make 失败")


def test_probe_first_number_handles_strings_and_tuples():
    from services.match.engine.textarena_probe import _first_number

    assert _first_number("Available Moves: '[3]'") == "3"
    assert _first_number([(0, "Available Moves: '[7]'", None)]) == "7"
    assert _first_number([]) is None
    assert _first_number("没有编号") is None


def test_probe_main_on_unknown_env_returns_zero(tmp_path):
    """main 对不可用环境只报告，不返回非零（它是诊断工具）。"""
    from services.match.engine import textarena_probe

    if not adapter.is_available():
        pytest.skip("未安装 textarena")
    assert textarena_probe.main(["probe", "NoSuchGame-v0"]) == 0


def test_observe_does_not_consume_legal_moves():
    """上游的观察是一次性队列：observe() 之后 legal_moves 仍必须可用。

    真实模型联调时踩过：observe() 把观察读空，legal_moves 退化成格式提示，
    模型两次犯规弃权，看起来像"模型不会玩"。
    """
    from services.match.engine import textarena_adapter as ta

    for game_id in ("tic-tac-toe", "connect-four", "othello", "nim"):
        game = ta.build(game_id)
        state = game.init(seed=3, players=["A", "B"], config={})
        # 先手由 seed 轮转：从 current_player 取，不能写死 "A"。
        current = game.current_player(state)
        before = game.legal_moves(state, current)
        observation = game.observe(state, current)
        after = game.legal_moves(state, current)
        assert observation["observations"], f"{game_id} 观察为空"
        assert before == after, f"{game_id} observe 之后 legal_moves 变了：{before} → {after}"
        assert all(item.startswith("[") for item in after), f"{game_id} 退化成了格式提示：{after}"


def test_legal_moves_survive_a_turn():
    """落子后的新状态也必须带可用动作（缓存由 apply 写回）。"""
    from services.match.engine import textarena_adapter as ta
    from services.match.engine.base import Move

    game = ta.build("tic-tac-toe")
    state = game.init(seed=3, players=["A", "B"], config={})
    first = game.current_player(state)
    legal = game.legal_moves(state, first)
    result = game.apply(state, first, Move(kind="guess", value=legal[0]))
    assert result.legal
    next_legal = game.legal_moves(result.next_state, game.current_player(result.next_state))
    assert len(next_legal) == 8, next_legal
    assert all(item.startswith("[") for item in next_legal)


def test_nim_enumerates_every_take():
    """取石子必须枚举"每堆取 1..n"，否则模型回 [0 2] 会被判非法。"""
    from services.match.engine import textarena_adapter as ta

    game = ta.build("nim")
    state = game.init(seed=1, players=["A", "B"], config={})
    moves = game.legal_moves(state, game.current_player(state))
    assert "[0 2]" in moves
    assert "[0 1]" in moves and "[2 5]" in moves


# ---- 观察解析的边界（纯函数，覆盖各种上游格式） ----

def test_observations_handles_all_upstream_shapes():
    """上游观察是三种形状：纯字符串、三元组、单元素元组。"""
    from services.match.engine.textarena_adapter import _observations

    class _State:
        def __init__(self, raw):
            self._raw = raw

        def get_current_player_observation(self):
            return self._raw

    class _Env:
        def __init__(self, raw):
            self.state = _State(raw)

    assert _observations(_Env("hello")) == ["hello"]
    assert _observations(_Env([(0, "msg", "type")])) == ["msg"]
    assert _observations(_Env([("only",)])) == ["only"]
    assert _observations(_Env(["bare"])) == ["bare"]
    assert _observations(_Env(None)) == []


def test_parse_available_only_reads_latest_observation():
    """历史观察里的动作已经不可用，不能当成候选。"""
    from services.match.engine.textarena_adapter import _parse_available

    assert _parse_available(["Available Moves: '[1]'", "Available Moves: '[2]'"]) == ["[2]"]
    assert _parse_available([]) == []


def test_parse_available_falls_back_to_bracketed_numbers():
    """没有 Available Moves 前缀时，抓纯数字方括号。"""
    from services.match.engine.textarena_adapter import _parse_available

    assert _parse_available(["你可以下 [3] 或 [4]"]) == ["[3]", "[4]"]
    # 带字母的坐标不该被误抓（那要走各赛制自己的推导）
    assert _parse_available(["坐标 [a2 b4]"]) == []


def test_columns_from_board_prefers_structured_board():
    """结构化棋盘优先：顶行决定可落子列。"""
    from services.match.engine.textarena_adapter import _columns_from_board

    board = [["X", ".", "O", ".", ".", ".", "."]]
    assert _columns_from_board([], board=board) == ["[col 1]", "[col 3]", "[col 4]", "[col 5]", "[col 6]"]


def test_columns_from_board_returns_empty_without_any_board():
    from services.match.engine.textarena_adapter import _columns_from_board

    assert _columns_from_board([], board=None) == []
    assert _columns_from_board(["no board here"]) == []


def test_columns_from_board_falls_back_to_text_when_board_malformed():
    from services.match.engine.textarena_adapter import _columns_from_board

    text = ". . . . . . ."
    assert _columns_from_board([text], board=[["bad"]]) == [
        "[col 0]", "[col 1]", "[col 2]", "[col 3]", "[col 4]", "[col 5]", "[col 6]"
    ]


def test_says_no_moves_only_matches_upstream_phrases():
    from services.match.engine.textarena_adapter import _says_no_moves

    assert _says_no_moves(["No valid moves – you may have to skip."])
    assert _says_no_moves(["You had to skip their turn"])
    assert not _says_no_moves(["Valid moves: '[1]'"])
    assert not _says_no_moves([])


def test_normalize_action_accepts_pass_and_forms():
    from services.match.engine.textarena_adapter import normalize_action

    assert normalize_action("[4]") == "[4]"
    assert normalize_action("4") == "[4]"
    assert normalize_action("2,3") == "[2,3]"
    assert normalize_action("[pass]") == "[pass]"
    assert normalize_action("skip") == "[pass]"
    assert normalize_action("") is None
    assert normalize_action("乱写") is None
