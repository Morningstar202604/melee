"""端点冒烟：每个 GET 端点必须有一个非 5xx 的真实响应。

拆分路由时踩过一次：`/scheduler` 调用了不存在的函数，返回 500，
而旧测试只断言 token 模式下 401，正常路径完全没有覆盖。
这条测试按"对外路由清单"逐个打一遍，避免同类漏网。
"""

from __future__ import annotations

from .support import create, wait_finished

# 只读端点；带参数的用固定假值，断言"不是 5xx"而非具体业务结果。
READ_PATHS = [
    "/health",
    "/games",
    "/stats",
    "/archive",
    "/matches",
    "/seasons",
    "/scheduler",
    "/quota",
    "/jobs",
    "/director/matches",
    "/metrics",
    "/metrics/business",
    "/ops/health",
    "/ops/metrics",
]


def test_every_read_endpoint_answers(client):
    failures = []
    for path in READ_PATHS:
        response = client.get(path)
        if response.status_code >= 500:
            failures.append(f"{path} → {response.status_code} {response.text[:120]}")
    assert not failures, failures


def test_match_scoped_endpoints_answer_for_a_real_match(client):
    match_id = create(client)["matchId"]
    wait_finished(match_id)
    paths = [
        f"/matches/{match_id}/events",
        f"/matches/{match_id}/view",
        f"/matches/{match_id}/recap",
        f"/matches/{match_id}/state",
        f"/matches/{match_id}/presence",
        f"/matches/{match_id}/clips",
        f"/matches/{match_id}/card.png",
        f"/share/{match_id}",
    ]
    failures = []
    for path in paths:
        response = client.get(path)
        if response.status_code >= 500:
            failures.append(f"{path} → {response.status_code} {response.text[:120]}")
    assert not failures, failures
