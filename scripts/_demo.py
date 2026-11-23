"""演示数据的唯一选手名单。

seed_demo、截图、演示视频三个脚本过去各背一份名单；加一个赛制就要
改三处，而且中英文名很容易只改到其中一边。这里按语言集中定义，
其他脚本只按 game 取名单，不再自己拼。
"""

from __future__ import annotations

ROSTERS: dict[str, dict[str, list[str]]] = {
    "zh": {
        "twenty-questions": ["灰隼", "砚台"],
        "codenames": ["红队队长", "蓝队队长", "红队队员", "蓝队队员"],
        "tic-tac-toe": ["左撇子", "右撇子"],
        "connect-four": ["直线", "斜线"],
        "othello": ["黑子", "白子"],
        "nim": ["取一", "取二"],
        "reverse-tic-tac-toe": ["逆风", "顺风"],
        "kuhn-poker": ["老千", "新手"],
        "game-of-pure-strategy": ["压价", "抬价"],
        "simple-tak": ["铺路", "封路"],
        "wild-tic-tac-toe": ["双面", "单面"],
        "crusade": ["骑士", "游侠"],
    },
    "en": {
        "twenty-questions": ["Grey Falcon", "Inkstone"],
        "codenames": ["Red Spymaster", "Blue Spymaster", "Red Operative", "Blue Operative"],
        "tic-tac-toe": ["Lefty", "Righty"],
        "connect-four": ["Straight", "Diagonal"],
        "othello": ["Onyx", "Ivory"],
        "nim": ["Taker", "Halver"],
        "reverse-tic-tac-toe": ["Headwind", "Tailwind"],
        "kuhn-poker": ["Shark", "Rookie"],
        "game-of-pure-strategy": ["Cutter", "Raiser"],
        "simple-tak": ["Paver", "Fencer"],
        "wild-tic-tac-toe": ["Two-Face", "One-Face"],
        "crusade": ["Knight", "Ranger"],
    },
}

DIRECTORS = {"zh": ["观测者", "审核员"], "en": ["Observer", "Auditor"]}
SEASON_PLAYERS = {
    "zh": ("灰隼", "砚台", "观测者", "审核员"),
    "en": ("Grey Falcon", "Inkstone", "Observer", "Auditor"),
}


def roster(lang: str) -> dict[str, list[str]]:
    return ROSTERS.get(lang, ROSTERS["zh"])


def names(lang: str, game: str) -> list[str]:
    return list(roster(lang).get(game, []))


def directors(lang: str) -> list[str]:
    return list(DIRECTORS.get(lang, DIRECTORS["zh"]))
