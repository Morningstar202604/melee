"""Idempotency-Key 的对外行为：重试不重复建场、冲突要明确拒绝。

为什么值得一组独立测试：这是"客户端超时后重发"的安全网，一旦失效
用户会遇到"点一次建出两场比赛"，而且很难在本地复现。
"""

from __future__ import annotations

import uuid

import pytest


def _body(**overrides):
    body = {"game": "tic-tac-toe", "players": [{"id": "A"}, {"id": "B"}]}
    body.update(overrides)
    return body


def _key():
    return str(uuid.uuid4())


def test_same_key_same_body_replays_the_first_match(client):
    key = _key()
    first = client.post("/matches", json=_body(), headers={"Idempotency-Key": key})
    second = client.post("/matches", json=_body(), headers={"Idempotency-Key": key})

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["matchId"] == first.json()["matchId"]


def test_same_key_different_body_is_rejected(client):
    key = _key()
    client.post("/matches", json=_body(), headers={"Idempotency-Key": key})
    conflict = client.post("/matches", json=_body(game="nim"), headers={"Idempotency-Key": key})

    assert conflict.status_code == 422
    assert "Idempotency" in conflict.json()["detail"]


def test_different_keys_create_different_matches(client):
    first = client.post("/matches", json=_body(), headers={"Idempotency-Key": _key()})
    second = client.post("/matches", json=_body(), headers={"Idempotency-Key": _key()})
    assert first.json()["matchId"] != second.json()["matchId"]


def test_without_a_key_behaviour_is_unchanged(client):
    """不给键时必须完全保持旧行为：不能因为加了幂等就要求客户端带键。"""
    first = client.post("/matches", json=_body())
    second = client.post("/matches", json=_body())
    assert first.status_code == second.status_code == 201
    assert first.json()["matchId"] != second.json()["matchId"]


def test_invalid_key_is_rejected_before_doing_any_work(client):
    """键会进 SQL 与日志，非法值要早拒，不能等到写库时才发现。"""
    bad = client.post("/matches", json=_body(), headers={"Idempotency-Key": "x" * 500})
    assert bad.status_code == 400
    assert "Idempotency-Key" in bad.json()["detail"]

    control = client.post("/matches", json=_body(), headers={"Idempotency-Key": "has space"})
    assert control.status_code == 400


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_card_theme_param_is_accepted(client, theme):
    """分享卡的 theme 参数是公开契约，两个值都必须 200。"""
    created = client.post("/matches", json=_body()).json()
    response = client.get(f"/matches/{created['matchId']}/card.png?theme={theme}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
