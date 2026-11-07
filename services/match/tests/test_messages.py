"""服务端文案目录：模板完整性、语言覆盖、参数替换与本地化边界。

这些用例存在的理由：`msg` 键是事件流的契约的一部分。写错一个键名，
事件流里会存进一个永远展开不出文本的引用；漏一种语言，
该语言用户会看到键名本身。两者都不该靠人眼发现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.match import messages


def test_every_message_has_both_languages():
    """每条消息都必须有英文与中文；缺一种就是漏翻。"""
    missing = [
        (key, lang)
        for key, table in messages.MESSAGES.items()
        for lang in messages.LANGS
        if not table.get(lang)
    ]
    assert not missing, missing


def test_message_keys_follow_naming_convention():
    """键名两段式 `域.用途`：域与模块同名，方便 grep 到唯一使用者。"""
    bad = [key for key in messages.MESSAGES if key.count(".") < 1]
    assert not bad, f"键名缺少域前缀: {bad}"


def test_placeholders_match_between_languages():
    """同一键的中英文必须用同一组占位符。

    少一个 {value} 会让中文句子丢掉关键信息，多一个则直接渲染成字面量
    `{value}`——两种都是用户可见的缺陷，但都是静态可查的。
    """
    import re

    placeholder = re.compile(r"\{(\w+)\}")
    problems = []
    for key, table in messages.MESSAGES.items():
        sets = {lang: set(placeholder.findall(table[lang])) for lang in table}
        if len({frozenset(value) for value in sets.values()}) > 1:
            problems.append((key, sets))
    assert not problems, problems


def test_format_substitutes_known_parameters():
    text = messages.format_message("narr.guess.correct", {"player": "A", "value": "x"}, lang="en")
    assert text == "A calls out x — correct."

    zh = messages.format_message("narr.guess.correct", {"player": "A", "value": "x"}, lang="zh")
    assert zh == "A 直接报出 x，猜中。"


def test_format_keeps_unknown_placeholders_visible():
    """参数缺失时保留 `{name}` 字面量，而不是替换成 None。

    静默变成 "None" 会让日志与界面出现莫名其妙的英文单词；
    保留花括号一眼就能看出是调用方的参数没传。
    """
    text = messages.format_message("narr.guess.correct", {"player": "A"}, lang="en")
    assert "{value}" in text


def test_unknown_key_returns_key_itself():
    """未知键返回键名：漏翻要在界面上显形，不能被空串吞掉。"""
    assert messages.format_message("nope.nothing", lang="en") == "nope.nothing"


def test_unknown_language_falls_back_to_english():
    assert messages.format_message("hl.end.none", lang="fr") == "No winner"


def test_message_payload_shape():
    payload = messages.message("hl.end.none")
    assert payload["text"] == "No winner"
    assert payload["msg"] == "hl.end.none"
    assert payload["params"] == {}


def test_boolean_parameters_are_localised():
    """`answer` 存的是语言无关的 yes/no，渲染时再落成各语言的说法。"""
    params = {"player": "A", "value": "x", "answer": "no", "narrowed": 3, "remaining": 2}
    en = messages.format_message("narr.answer.narrowed", messages._localize_params(params, "en"), lang="en")
    zh = messages.format_message("narr.answer.narrowed", messages._localize_params(params, "zh"), lang="zh")
    assert "no" in en and "否" in zh


def test_localize_payload_rewrites_narration_text():
    payload = {"text": "fallback", "msg": "narr.forfeit", "params": {"player": "A"}, "refs": [1]}
    zh = messages.localize_payload(payload, "zh")
    assert zh["text"] == "A 无法给出合法动作，弃权。"
    assert zh["refs"] == [1], "非文案字段必须原样保留"


def test_localize_payload_leaves_plain_text_untouched():
    """老事件没有 msg：必须原样返回，不能猜它的语言。"""
    payload = {"text": "这是一条旧解说", "refs": []}
    assert messages.localize_payload(payload, "en") == payload


def test_localize_payload_rewrites_cue_trigger():
    payload = {
        "shot": "slowmo",
        "trigger": "last move — slow motion",
        "triggerKey": "cue.last_move",
        "triggerParams": {},
    }
    zh = messages.localize_payload(payload, "zh")
    assert zh["trigger"] == "最后一手，慢镜收尾"
    assert zh["shot"] == "slowmo"


def test_localize_payload_rewrites_end_reason():
    payload = {"winner": "A", "reason": "猜中", "finalState": {}}
    assert messages.localize_payload(payload, "en")["reason"] == "guessed correctly"


def test_localize_reason_passes_unknown_reason_through():
    """引擎里没登记的新原因不能被猜翻译，否则会编造事实。"""
    assert messages.localize_reason("某个新赛制的自定义原因", "en") == "某个新赛制的自定义原因"


def test_localize_recap_translates_one_line_and_highlights():
    recap = {
        "oneLine": "A wins.",
        "oneLineKey": "recap.win.plain",
        "oneLineParams": {"winner": "A"},
        "reason": "猜中",
        "highlights": [
            {
                "title": "A wins",
                "titleKey": "hl.end.win",
                "titleParams": {"winner": "A"},
                "detail": "guessed correctly",
                "detailKey": "hl.end.none",
            }
        ],
    }
    zh = messages.localize_recap(recap, "zh")
    assert zh["oneLine"] == "A 获胜。"
    assert zh["reason"] == "猜中"
    assert zh["highlights"][0]["title"] == "A 获胜"
    assert zh["highlights"][0]["detail"] == "无人获胜"


def test_localize_recap_handles_missing_timing():
    """没有逐手耗时数据时不能抛异常：脚本选手本来就没有耗时。"""
    recap = {"oneLine": "x", "highlights": []}
    assert messages.localize_recap(recap, "zh")["oneLine"] == "x"


def test_highlights_without_keys_are_returned_as_is():
    items = [{"title": "原始标题", "detail": "原始说明"}]
    assert messages.localize_highlights(items, "en") == items


def test_localize_action_kind_covers_all_engine_kinds():
    """引擎发得出的动作类型都必须有译文；漏一个就会在战报里露英文枚举。"""
    assert messages.localize_action_kind("ask", "en") == "asked"
    assert messages.localize_action_kind("guess", "zh") == "报答案"
    assert messages.localize_action_kind("totally-new-kind", "en") == "totally-new-kind"
    assert messages.localize_action_kind("", "en") == ""
    assert messages.localize_action_kind(None, "en") is None


def test_frontend_dictionary_covers_backend_keys():
    """前后端字典必须覆盖同一批赛制名。

    赛制名两边各有一份（后端用于分享页/卡片，前端用于交互界面）；
    新增赛制时只改一边，另一边就会显示原始 id。
    """
    frontend = (Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "i18n.js").read_text(
        encoding="utf-8"
    )
    backend_games = {key for key in messages.MESSAGES if key.startswith("game.")}
    missing = [key for key in backend_games if f"'{key}'" not in frontend]
    assert not missing, f"前端字典缺少赛制名: {sorted(missing)}"


@pytest.mark.parametrize("lang", messages.LANGS)
def test_render_is_deterministic(lang):
    """同一个键与参数必须每次渲染出同一句话：文案进事件流，不能随机。"""
    first = messages.format_message("narr.answer.narrowed", {"player": "A", "value": "x",
                                                             "answer": "yes", "narrowed": 3,
                                                             "remaining": 2}, lang=lang)
    second = messages.format_message("narr.answer.narrowed", {"player": "A", "value": "x",
                                                              "answer": "yes", "narrowed": 3,
                                                              "remaining": 2}, lang=lang)
    assert first == second


def test_messages_json_serialisable():
    """载荷要能直接写进事件流：必须可 JSON 序列化。"""
    payload = messages.message("narr.hint", {"player": "A", "word": "海", "count": 2})
    assert json.loads(json.dumps(payload))["msg"] == "narr.hint"


def test_api_surfaces_localise_game_labels(client):
    """/games、/archive、/stats 的赛制名必须跟随请求语言。

    前端有自己的字典，但 API 是对外契约：第三方拿 Accept-Language: en
    调用时不应看到中文赛制名混在英文响应里。
    """
    en_games = client.get("/games", headers={"Accept-Language": "en"}).json()["games"]
    zh_games = client.get("/games", headers={"Accept-Language": "zh"}).json()["games"]
    en_by_id = {item["gameId"]: item["label"] for item in en_games}
    zh_by_id = {item["gameId"]: item["label"] for item in zh_games}
    assert en_by_id["twenty-questions"] == "Twenty Questions"
    assert zh_by_id["twenty-questions"] == "二十问"
    # 两种语言都要覆盖注册表里的每一个赛制。
    assert set(en_by_id) == set(zh_by_id)

    created = client.post(
        "/matches",
        json={"game": "tic-tac-toe", "seed": 1, "players": [{"id": "A"}, {"id": "B"}]},
    ).json()["matchId"]
    en_item = client.get("/archive?limit=1", headers={"Accept-Language": "en"}).json()["items"][0]
    zh_item = client.get("/archive?limit=1", headers={"Accept-Language": "zh"}).json()["items"][0]
    assert en_item["matchId"] == zh_item["matchId"] == created
    assert en_item["game"] == zh_item["game"] == "tic-tac-toe"
    assert en_item["gameLabel"].isascii()
    assert not zh_item["gameLabel"].isascii()

    en_stats = client.get("/stats", headers={"Accept-Language": "en"}).json()
    zh_stats = client.get("/stats", headers={"Accept-Language": "zh"}).json()
    en_labels = {row["label"] for row in en_stats["byGame"]}
    zh_labels = {row["label"] for row in zh_stats["byGame"]}
    assert en_labels and all(label.isascii() for label in en_labels)
    assert zh_labels and any(not label.isascii() for label in zh_labels)

def test_end_reason_with_embedded_names_is_translated():
    """引擎把队伍名/棋盘词拼进终局原因。这些句子以前原样进英文战报，
    出现「红队 wins: 翻到刺客「Ocean」，红队立即判负.」这种半中半英。

    这类句子只能按句式翻：内容（棋盘词、第 N 队）保留，句式换语言。
    """
    from services.match.messages import localize_reason

    cases = {
        "红队 获胜": "Red won",
        "第 3 队 获胜": "Team 3 won",
        "蓝队的词已被翻完": "Blue's words were all taken",
        "红队翻完全部己方词": "Red revealed all of its own words",
        "中立词被翻完": "the last neutral word was revealed",
        "平局": "a draw",
        "对局结束": "the match ended",
    }
    for zh, expected_en in cases.items():
        assert localize_reason(zh, "en") == expected_en, zh
        assert localize_reason(zh, "zh") == zh, zh

    assassin = "翻到刺客「Ocean」，红队立即判负"
    en = localize_reason(assassin, "en")
    assert "Ocean" in en and "assassin" in en and "红队" not in en
    assert localize_reason(assassin, "zh") == assassin


def test_unknown_end_reason_is_left_untouched():
    """没见过的原因不能被"翻译"成空串或键名——那比原样显示更糟。"""
    from services.match.messages import localize_reason

    assert localize_reason("某个新赛制的自定义原因", "en") == "某个新赛制的自定义原因"

