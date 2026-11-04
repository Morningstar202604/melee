"""代号（Codenames）· 4 人团队版。

为什么自研而不是接 TextArena：
它的 Codenames 环境依赖 NLTK 的 `words` 语料，本机下载不可用（无外网，
且语料缺失时环境直接构造失败）。规则本身很简单，自己写反而更可控，
也能精确控制"哪些信息对谁可见"——这正是本赛制要验证的东西。

规则（4 人）：
- 两个队，红队 {0,2}，蓝队 {1,3}。
- 每队一名队长（红队 0、蓝队 1），只有队长看得到每个词的颜色。
- 25 个词，其中 9 红 / 8 蓝 / 7 中立 / 1 刺客。
- 队长每回合说「线索词 + 数量」，例如 `hint:海洋 2`。
- 队员每回合猜一个词，例如 `pick:灯塔`；猜中己方颜色可继续猜，
  猜错（对方/中立/刺客）立即换手。
- 先翻出全部己方颜色者胜；翻到刺客立即判负。

行动格式保持极简：`hint:<词> <数字>` 或 `pick:<词>`。
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass
from typing import Any

from .base import AppliedEffect, Move, MoveResult, PlayerId

# 25 个词。选常见名词，便于模型给出有意义的线索。
# 双语：英文界面下棋盘里出现中文词，是最刺眼的"语言错乱"——
# 界面文案翻了、内容没翻，读起来像半成品。词表按语言分，种子决定取哪些词，
# 所以同一种子在同一语言下仍然完全可复现。
WORDS_EN: list[str] = [
    "Lighthouse", "Ocean", "Desert", "Forest", "Snowpeak", "Volcano", "Glacier",
    "Prairie", "Canyon", "Waterfall", "Sun", "Moon", "Star", "Cloud", "Rainbow",
    "Thunder", "Mist", "Aurora", "Meteor", "Tide", "Page", "Canvas", "String",
    "Clay", "Door", "Lock", "Glass", "Silk", "Mirror", "Monolith",
]
WORDS_ZH: list[str] = [
    "灯塔", "海洋", "沙漠", "森林", "雪山", "火山", "冰川", "草原", "峡谷", "瀑布",
    "太阳", "月亮", "星星", "云朵", "彩虹", "雷雨", "晨雾", "极光", "流星", "潮汐",
    "书页", "油画", "琴弦", "陶罐", "木门", "铁锁", "玻璃", "丝绸", "铜镜", "石碑",
]
# 默认词表：中文，保持既有行为（历史事件、录像、测试都基于它）。
WORDS: list[str] = WORDS_ZH


def word_pool(lang: str = "zh") -> list[str]:
    """按语言取词表；未知语言回退中文（与既有行为一致）。"""
    return WORDS_EN if str(lang).lower().startswith("en") else WORDS_ZH

_CN = {"red": "红队", "blue": "蓝队"}


def team_label(team: str) -> str:
    """公开的显示名，其他模块（战报/解说/卡片/渲染）统一引用这一份。

    之前战报、解说、分享卡各自维护一份 {red: 红队, blue: 蓝队}，
    多队编队落地后 team3 的胜利在战报里显示原始字符串、还会触发
    「胜者不在参赛名单里」的假警告——五处漂移就是五处 bug。
    队伍与棋子（中立/刺客）都在这张表里，调用方不用再分。"""
    if team in _CN:
        return _CN[team]
    if team in ("neutral", "assassin"):
        return {"neutral": "中立", "assassin": "刺客"}[team]
    return _label(team)


def _label(team: str) -> str:
    """队伍显示名。多队编队时不能查表（会 KeyError），
    未预置的队伍按编号显示，不允许因为"多了一支队伍"就把比赛跑崩。"""
    if team in _CN:
        return _CN[team]
    if team.startswith("team") and team[4:].isdigit():
        # team3 是第 3 支队伍（索引从 2 开始编号），显示成"第 3 队"。
        return f"第 {int(team[4:])} 队"
    return team

TEAM_RED = "red"
TEAM_BLUE = "blue"
TEAM_NEUTRAL = "neutral"
TEAM_ASSASSIN = "assassin"

GRID_SIZE = 25
LAYOUT = {TEAM_RED: 9, TEAM_BLUE: 8, TEAM_NEUTRAL: 7, TEAM_ASSASSIN: 1}


@dataclass
class TeamView:
    team: str
    captain: PlayerId
    agents: list[PlayerId]


def _seat_plan(players: list[PlayerId], config: dict[str, Any] | None = None) -> list[TeamView]:
    """把选手列表解析成「每队：一名队长 + 若干队员」的席位计划。

    默认（4 人）必须与旧行为逐位一致：
        players[0] 红队队长、players[1] 蓝队队长、players[2] 红队队员、players[3] 蓝队队员。

    这是为了不破坏已有事件流与 rehydrate——历史上每一场代号局都是这个排法。
    想打"每队 2 名队员"或"3 支队伍"，走 config 的 teamSize / teams：
        teamSize=3 → 红队队长, 蓝队队长, 红队队员×2, 蓝队队员×2（共 6 人）
    """
    config = config or {}
    teams = int(config.get("teams", 2))
    team_size = int(config.get("teamSize", 2))
    if teams < 2:
        raise ValueError("代号至少需要 2 支队伍")
    if team_size < 2:
        raise ValueError("每队至少 1 名队长 + 1 名队员")
    needed = teams * team_size
    if len(players) != needed:
        raise ValueError(f"代号 {teams} 队 × 每队 {team_size} 人需要 {needed} 名选手，收到 {len(players)}")

    names = [TEAM_RED, TEAM_BLUE] + [f"team{index + 1}" for index in range(2, teams)]
    # 席位顺序必须与旧版 4 人排法逐位一致：
    #   players[0]=红队长, players[1]=蓝队长, players[2]=红队员, players[3]=蓝队员
    # 即"先按队列分队长，再按队轮转分队员"。多队/多队员只是把这个模式延长。
    views: list[TeamView] = [
        TeamView(team=name, captain=players[index], agents=[])
        for index, name in enumerate(names)
    ]
    for round_index in range(team_size - 1):
        for team_index in range(teams):
            slot = teams + round_index * teams + team_index
            views[team_index].agents.append(players[slot])
    return views


def _teams(players: list[PlayerId], config: dict[str, Any] | None = None) -> list[TeamView]:
    return _seat_plan(players, config)


def _team_of(players: list[PlayerId], player: PlayerId, config: dict[str, Any] | None = None) -> str | None:
    for view in _seat_plan(players, config):
        if player == view.captain or player in view.agents:
            return view.team
    return None


def _is_captain(players: list[PlayerId], player: PlayerId, config: dict[str, Any] | None = None) -> bool:
    return any(view.captain == player for view in _seat_plan(players, config))


class Codenames:
    game_id = "codenames"
    # 2 队 × 每队 2 人 = 4 人（默认），最多 4 队 × 4 人 = 16 人。
    player_range = (4, 16)

    def init(self, seed: int, players: list[PlayerId], config: dict[str, Any]) -> dict[str, Any]:
        views = _teams(players, config)  # 校验人数并解析席位
        rng = random.Random(seed)
        # 词表语言来自建场配置：同一场比赛的棋盘词必须与观看语言一致，
        # 否则英文界面里出现中文词。config 里没给就沿用中文（历史行为）。
        lang = str((config or {}).get("lang") or "zh")
        pool = list(word_pool(lang))
        rng.shuffle(pool)
        board = pool[:GRID_SIZE]

        # 配色分配：2 队沿用经典 9/8/7/1（红队先手多一个词）；
        # 3 队及以上按队伍均分剩余词，否则新增队伍永远没有自己的词，
        # 只能靠翻别人的词获胜——那不是三队，是"两队 + 一个搅局的"。
        names = [view.team for view in views]
        # 先手队由 seed 决定（旧实现固定红队）。
        first_team = names[seed % len(names)]
        if len(names) == 2:
            # 官方规则：先手队多 1 个词作为被针对的补偿。
            # 多出的那个必须给先手队，否则"词少的一方还后手"会双重吃亏。
            other = names[1] if first_team == names[0] else names[0]
            layout = {first_team: LAYOUT[TEAM_RED], other: LAYOUT[TEAM_BLUE]}
        else:
            # 词数必须尽量均等：旧实现把 25-7-1 的全部余数塞给首队，
            # 6 队时变成 7:2:2:2:2:2——首队要翻的词是别人的 3.5 倍，
            # 而首队又固定先手，等于把胜负写死在抽签里。
            # 现在按"每队至少 floor(左右)，余数轮流给前几队，每队至多多 1 个"。
            pool = GRID_SIZE - LAYOUT[TEAM_NEUTRAL] - LAYOUT[TEAM_ASSASSIN]
            base_count, extra = divmod(pool, len(names))
            # 余数优先给先手队，再按席位顺序补——保证"词最多的队就是先手队"。
            # 若按席位顺序分，会出现 seed=0 时先手队恰好拿少一个词的双重劣势。
            order = [first_team] + [name for name in names if name != first_team]
            bonus = set(order[:extra])
            layout = {name: base_count + (1 if name in bonus else 0) for name in names}
        colors = []
        for team in names:
            colors += [team] * layout[team]
        colors += [TEAM_NEUTRAL] * LAYOUT[TEAM_NEUTRAL]
        colors += [TEAM_ASSASSIN] * LAYOUT[TEAM_ASSASSIN]
        rng.shuffle(colors)
        assert len(colors) == GRID_SIZE, f"配色数量 {len(colors)} != {GRID_SIZE}"

        team_names = [view.team for view in views]
        return {
            "seed": seed,
            "players": list(players),
            # 编队参数必须进 state：rehydrate 用同一份 state 重放，
            # 不给的话重放会退回默认 4 人排法，事件与引擎立刻对不上。
            # 同时以 teams/teamSize 平铺进 state：match.created 会按这两个键
            # 记录配置，rehydrate 用同一份 config 就能重建一模一样的编队。
            "teams": int((config or {}).get("teams", 2)),
            "teamSize": int((config or {}).get("teamSize", 2)),
            "teamConfig": {
                "teams": int((config or {}).get("teams", 2)),
                "teamSize": int((config or {}).get("teamSize", 2)),
            },
            "teamNames": team_names,
            "words": board,
            "colors": colors,
            "revealed": [False] * GRID_SIZE,
            # 先手由 seed 决定：旧实现固定红队，是唯一"同一选手永远同席位"的
            # 赛制（其它赛制都随 seed 轮转）。多人时按队伍列表轮转。
            "turn": first_team,
            "phase": "hint",          # hint | guess
            "hint": None,             # {"word": str, "count": int}
            "remainingGuesses": 0,
            # 本回合内已经猜过几次：多队员编队用它决定下一个由谁开口。
            "guessesThisTurn": 0,
            "finished": False,
            "winner": None,
            "endReason": "",
            "turnIndex": 0,
            "history": [],
        }

    # ---- 视图裁剪 ----

    def _public_board(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "word": state["words"][i],
                "revealed": state["revealed"][i],
                # 未翻开的词不暴露颜色；翻开的才公开
                "color": state["colors"][i] if state["revealed"][i] else None,
            }
            for i in range(GRID_SIZE)
        ]

    def public_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "board": self._public_board(state),
            "turn": state["turn"],
            "phase": state["phase"],
            # 拷贝而不是引用：选手改 observation 不能改到引擎状态。
            "hint": dict(state["hint"]) if state.get("hint") else None,
            # 观众需要知道现在轮到哪一队、是队长给线索还是队员猜词，
            # 否则棋盘再漂亮也读不出"这一步在等谁"。
            "currentPlayer": self.current_player(state),
            # 每队剩余：按 state 里登记过的队伍动态生成，不写死红蓝。
            "remaining": {
                team: sum(
                    1
                    for i in range(GRID_SIZE)
                    if state["colors"][i] == team and not state["revealed"][i]
                )
                for team in state.get("teamNames", [TEAM_RED, TEAM_BLUE])
            },
            "finished": state["finished"],
            "winner": state["winner"],
            "endReason": state["endReason"],
            "turnIndex": state["turnIndex"],
            "history": list(state.get("history") or []),
        }

    def observe(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any]:
        players = state["players"]
        team = _team_of(players, player, state.get("teamConfig"))
        if team is None:
            raise KeyError(f"unknown player {player}")

        captain = _is_captain(players, player, state.get("teamConfig"))
        my_turn = self.current_player(state) == player

        payload: dict[str, Any] = {
            "you": player,
            "team": team,
            "isCaptain": captain,
            "isYourTurn": my_turn,
            "turn": state["turn"],
            "phase": state["phase"],
            "words": list(state["words"]),
            "revealed": list(state["revealed"]),
            "publicBoard": self._public_board(state),
            # 拷贝而不是引用：选手改 observation 不能改到引擎状态。
            "hint": dict(state["hint"]) if state.get("hint") else None,
            "remaining": self.public_state(state)["remaining"],
        }

        # 只有队长能看到完整配色（这是本赛制的隐藏信息核心）
        if captain:
            payload["secretColors"] = list(state["colors"])

        return payload

    def thought_visibility(self, state: dict[str, Any], player: PlayerId) -> dict[str, Any] | None:
        """队长的思考里有配色推理，只能自己看；队员的思路可以公开。

        这是 `visibility` 契约的第一个真实用例：隐藏信息此前只在 `observe()`
        裁剪，事件流本身是全公开的，队长一句"红队还剩 3 个"就把秘密说出去了。
        """
        if _is_captain(state["players"], player, state.get("teamConfig")):
            return {"players": [player]}
        return None

    def current_player(self, state: dict[str, Any]) -> PlayerId | None:
        if state["finished"]:
            return None
        players = state["players"]
        for view in _teams(players, state.get("teamConfig")):
            if view.team != state["turn"]:
                continue
            if state["phase"] == "hint":
                return view.captain
            # 多队员时轮流猜：只用"本回合内第几次猜"决定谁开口。
            # 不能用 turnIndex——它是全场步数，每步都涨，取模会跳号，
            # 实测每队 2 名队员时永远只轮到第 2 个人（r_a1 全程没出过场）。
            index = state.get("guessesThisTurn", 0) % len(view.agents)
            return view.agents[index]
        return None

    def legal_moves(self, state: dict[str, Any], player: PlayerId) -> list[str]:
        if state["phase"] == "hint":
            return ["hint:<线索词> <数量>"]
        # 已翻开的词不能再选：把 25 个词全给出去时，模型会反复挑已翻开的，
        # 连犯两次规就弃权，观众看到的是"AI 突然认输"。
        return [
            f"pick:{word}"
            for word, revealed in zip(state["words"], state["revealed"], strict=True)
            if not revealed
        ]

    # ---- 动作 ----

    def apply(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        if state["finished"]:
            return MoveResult(legal=False, reason="对局已结束", retryable=False)

        current = self.current_player(state)
        if current != player:
            return MoveResult(legal=False, reason=f"现在轮到 {current}", retryable=False)

        if move.kind == "forfeit":
            players = state["players"]
            team = _team_of(players, player, state.get("teamConfig"))
            next_state = copy.deepcopy(state)
            next_state["finished"] = True
            # 多队时旧实现一律判红队胜：3/4/6 队里任何非红队弃权都送给红队，
            # 这条路径还被超时复用，等于把故障判给固定一方。
            # 正确语义：弃权队出局，胜者按"活着的队伍里先手顺位最靠前的"。
            next_state["winner"] = _winner_after_forfeit(state, team)
            next_state["endReason"] = "弃权"
            return MoveResult(
                legal=True,
                next_state=next_state,
                effects=[AppliedEffect(kind="forfeit", detail={"by": player, "team": team})],
                public_state=self.public_state(next_state),
            )

        if state["phase"] == "hint":
            return self._apply_hint(state, player, move)
        return self._apply_pick(state, player, move)

    def _apply_hint(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        raw = (move.value or "").strip()
        if raw.lower().startswith("hint:"):
            raw = raw[5:].strip()

        parts = raw.rsplit(" ", 1)
        if len(parts) != 2:
            return MoveResult(legal=False, reason="线索格式应为「词 数量」，例如 hint:海洋 2", retryable=True)

        clue, count_text = parts[0].strip(), parts[1].strip()
        if not clue or " " in clue:
            return MoveResult(legal=False, reason="线索只能是一个词", retryable=True)
        try:
            count = int(count_text)
        except ValueError:
            return MoveResult(legal=False, reason="数量必须是整数", retryable=True)
        if not 1 <= count <= 9:
            return MoveResult(legal=False, reason="数量应在 1–9 之间", retryable=True)
        if clue in state["words"]:
            return MoveResult(legal=False, reason="线索不能是场上的词", retryable=True)

        next_state = copy.deepcopy(state)
        next_state["hint"] = {"word": clue, "count": count, "by": player}
        next_state["history"] = (state.get("history") or []) + [
            {"by": player, "kind": "hint", "value": f"{clue} {count}"}
        ]
        next_state["phase"] = "guess"
        next_state["remainingGuesses"] = count + 1  # 允许多猜一次
        next_state["turnIndex"] += 1

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=[AppliedEffect(kind="hint", detail={"word": clue, "count": count})],
            public_state=self.public_state(next_state),
        )

    def _apply_pick(self, state: dict[str, Any], player: PlayerId, move: Move) -> MoveResult:
        raw = (move.value or "").strip()
        if raw.lower().startswith("pick:"):
            raw = raw[5:].strip()

        if raw not in state["words"]:
            return MoveResult(legal=False, reason="场上没有这个词", retryable=True)

        index = state["words"].index(raw)
        if state["revealed"][index]:
            return MoveResult(legal=False, reason="这个词已经翻开", retryable=True)

        next_state = copy.deepcopy(state)
        next_state["revealed"][index] = True
        next_state["turnIndex"] += 1
        next_state["history"] = (state.get("history") or []) + [
            {"by": player, "kind": "pick", "value": raw, "color": next_state["colors"][index]}
        ]
        color = next_state["colors"][index]
        team = state["turn"]

        effects = [
            AppliedEffect(
                kind="reveal",
                detail={"word": raw, "color": color, "team": team},
            )
        ]

        winner, reason = self._resolve_pick(next_state, color, team, raw)
        if winner is not None:
            next_state["finished"] = True
            next_state["winner"] = winner
            next_state["endReason"] = reason

        return MoveResult(
            legal=True,
            next_state=next_state,
            effects=effects,
            public_state=self.public_state(next_state),
        )

    @staticmethod
    def _resolve_pick(
        next_state: dict[str, Any], color: str, team: str, raw: str
    ) -> tuple[str | None, str]:
        if color == TEAM_ASSASSIN:
            # 翻刺客 = 本队出局，胜者从其余队伍里按先手顺位取（不再固定红/蓝）。
            winner = _winner_after_forfeit(next_state, team)
            return winner, f"翻到刺客「{raw}」，{_label(team)}立即判负"
        if color != team:
            # 对手翻掉了你最后一个词，等于你的词全被翻开——该队直接获胜。
            # 旧实现只换手，0 个剩余词的一方永远赢不了，最后只能靠 max_turns 收场。
            if _own_left(next_state, color) == 0:
                return color, f"{_label(color)}的词已被翻完"
            _pass_turn(next_state)
            return None, ""

        if _own_left(next_state, team) == 0:
            return team, f"{_label(team)}翻完全部己方词"

        next_state["remainingGuesses"] -= 1
        next_state["guessesThisTurn"] = next_state.get("guessesThisTurn", 0) + 1
        if next_state["remainingGuesses"] <= 0:
            _pass_turn(next_state)
        return None, ""


def _winner_after_forfeit(state: dict[str, Any], out_team: str) -> str | None:
    """某队出局后的胜者：轮转顺序里紧接出局队的下一支存活队伍。

    为什么不是"存活队里的第一个"：那等于永远判给席位最靠前的队。
    实测 3 队时 team3 弃权会判红队胜——红队根本没参与，却被判赢。
    按轮转顺序取下一支，语义上等于"轮到谁"的延续，不偏向任何席位。
    """
    names = list(state.get("teamNames") or [TEAM_RED, TEAM_BLUE])
    if out_team not in names:
        alive = [name for name in names]
        return alive[0] if alive else None
    start = names.index(out_team)
    for step in range(1, len(names) + 1):
        candidate = names[(start + step) % len(names)]
        if candidate != out_team:
            return candidate
    return None


def _pass_turn(next_state: dict[str, Any]) -> None:
    """换手，回到下一支队伍的线索阶段。

    多队时按 state["teamNames"] 顺序轮转，不写死红蓝两色。
    """
    names = list(next_state.get("teamNames") or [TEAM_RED, TEAM_BLUE])
    current = next_state.get("turn", names[0])
    index = names.index(current) if current in names else 0
    next_state["turn"] = names[(index + 1) % len(names)]
    next_state["phase"] = "hint"
    next_state["hint"] = None
    next_state["guessesThisTurn"] = 0


def _own_left(next_state: dict[str, Any], team: str) -> int:
    return sum(
        1
        for i in range(GRID_SIZE)
        if next_state["colors"][i] == team and not next_state["revealed"][i]
    )
