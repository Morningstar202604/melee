"""高光模板：三套版式必须真的不同，且同一输入逐像素可复现。"""

from __future__ import annotations

import hashlib

import pytest

from services.match.agents.scripted import GreedyAgent
from services.match.clips import build_clips
from services.match.engine.twenty_questions import CATALOG
from services.match.eventlog import EventLog
from services.match.render import TEMPLATES, build_context, draw_frame, normalize_template
from services.match.runner import Runner


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    db = tmp_path_factory.mktemp("render") / "m.db"
    from services.match.engine.twenty_questions import TwentyQuestions

    game = TwentyQuestions()
    state = game.init(seed=7, players=["A", "B"], config={"maxQuestions": 8})
    log = EventLog.open(db, "m1")
    try:
        log.create_match(game="twenty-questions", seed=7, players=["A", "B"])
        Runner(game, log, {p: GreedyAgent(p, CATALOG) for p in ("A", "B")}).play(state)
        events = log.read()
    finally:
        log.close()
    clips = build_clips(events, limit=1)
    assert clips
    context = build_context(events)
    context["total"] = len(clips[0].events)
    return clips[0], events, context


def _frame_hash(clip, event, context, template: str) -> str:
    image = draw_frame(event, clip=clip, context={**context, "template": template, "index": 0})
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_normalize_template_falls_back_to_default():
    assert normalize_template(None) == "broadcast"
    assert normalize_template("") == "broadcast"
    assert normalize_template("nope") == "broadcast"
    assert normalize_template("SPOTLIGHT") == "spotlight"
    assert normalize_template("scorecard") == "scorecard"


def test_every_template_renders_and_differs(sample):
    clip, _events, context = sample
    event = clip.events[1]
    hashes = {template: _frame_hash(clip, event, context, template) for template in TEMPLATES}
    assert len(set(hashes.values())) == len(TEMPLATES), hashes


def test_same_template_is_pixel_identical(sample):
    """确定性：同一事件、同一模板，两次绘制必须逐像素相同。"""
    clip, _events, context = sample
    event = clip.events[1]
    for template in TEMPLATES:
        assert _frame_hash(clip, event, context, template) == _frame_hash(clip, event, context, template)


def test_spotlight_wraps_long_text_without_losing_characters(sample):
    from services.match.render import _wrap_text

    text = "灰隼问「是不是建筑」答否排除 8 个候选只剩 3 个"
    lines = _wrap_text(text, 16)
    assert "".join(lines).replace(" ", "") == text.replace(" ", "")
    assert all(lines)


def test_templates_do_not_invent_numbers(sample):
    """版式只重排已有事实：没有候选数时不能凭空画一个数字。"""
    from services.match.render import _spotlight_metric

    assert _spotlight_metric({}, {}) == ("", "")
