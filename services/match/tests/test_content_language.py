"""内容数据的语言一致性：界面语言为英文时，棋盘词/内心戏/解说都不能是中文。

为什么单独立一个测试文件：这是"语言错乱"最伤信任的一类缺陷——
模板翻了、参数没翻，用户看到的是
    Red Spymaster gives the clue "玻璃相关"
读起来像本地化只做了一半。之前 20 项双语巡检全绿却没抓到，
因为它只查界面 chrome，不查内容数据。
"""

from __future__ import annotations

import re

import pytest

HAN = re.compile(r"[\u4e00-\u9fff]+")


@pytest.fixture()
def played(client):
    """真跑一场代号，返回 (events, lang)。"""
    import time

    def _play(lang: str):
        created = client.post(
            "/matches",
            json={
                "game": "codenames",
                "seed": 2027,
                "players": [
                    {"id": n, "delayMs": 20}
                    for n in ("Red Spymaster", "Blue Spymaster", "Red Operative", "Blue Operative")
                ],
            },
            headers={"Accept-Language": lang},
        ).json()["matchId"]
        deadline = time.time() + 120
        while time.time() < deadline:
            events = client.get(
                f"/matches/{created}/events", headers={"Accept-Language": lang}
            ).json()["events"]
            if any(e["type"] == "match.ended" for e in events):
                return events, lang
            time.sleep(0.5)
        raise AssertionError("match never ended")

    return _play


def test_english_match_has_no_chinese_content(played):
    """英文界面下：棋盘词、内心戏、解说都不能出现中文。"""
    events, _ = played("en")

    board_state = next(
        e for e in events
        if e["type"] == "state.changed"
        and "board" in (e["payload"].get("publicState") or {})
    )
    words = [cell["word"] for cell in board_state["payload"]["publicState"]["board"]]
    assert words, "board must exist"
    assert not [w for w in words if HAN.search(w)], f"棋盘词含中文: {words}"

    # 内心戏是"若产生则必须干净"：极低 delayMs 下 ThoughtSink 可能被节流，
    # 数量不该成为断言目标；语言纯度才是这条测试要守的东西。
    thoughts = [e["payload"]["text"] for e in events if e["type"] == "thought.delta"]
    assert not [t for t in thoughts if HAN.search(t)], thoughts[:2]

    narration = [e["payload"]["text"] for e in events if e["type"] == "narration.line"]
    assert narration
    assert not [t for t in narration if HAN.search(t)], narration[:2]


def test_chinese_match_keeps_chinese_content(played):
    """中文界面下内容保持中文：本地化不能把默认语言也改掉。"""
    events, _ = played("zh")
    board_state = next(
        e for e in events
        if e["type"] == "state.changed"
        and "board" in (e["payload"].get("publicState") or {})
    )
    words = [cell["word"] for cell in board_state["payload"]["publicState"]["board"]]
    assert any(HAN.search(w) for w in words), f"中文棋盘词消失了: {words[:5]}"


def test_same_event_reads_in_both_languages(played):
    """同一条事件按两种语言重读：队伍名等参数必须是语言无关引用键。"""
    events, _ = played("en")
    situations = [
        e for e in events
        if e["type"] == "narration.line" and e["payload"].get("msg") == "narr.situation.leading"
    ]
    if not situations:
        pytest.skip("本场没有局势播报")
    params = situations[0]["payload"]["params"]
    assert params["team"].startswith("team."), f"队伍名不是引用键: {params}"
