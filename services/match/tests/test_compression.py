"""响应压缩的边界：JSON 走 gzip，SSE 绝不能被缓冲。"""

from __future__ import annotations

from .support import create, wait_finished


def test_event_payload_is_gzipped_when_the_client_accepts_it(client):
    data = create(client)
    wait_finished(data["matchId"])

    response = client.get(
        f"/matches/{data['matchId']}/events",
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    # TestClient 会解压 body；断言的是真实的网络编码，不是内容长度。
    assert response.json()["events"]


def test_small_responses_stay_uncompressed(client):
    response = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers.get("content-encoding") is None


def test_sse_is_never_compressed(client):
    """SSE 一旦被 gzip 就会按块缓冲，直播会变成一段一段地吐。"""
    data = create(client)
    wait_finished(data["matchId"])

    with client.stream(
        "GET",
        f"/matches/{data['matchId']}/stream",
        headers={"Accept-Encoding": "gzip"},
    ) as response:
        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("text/event-stream")
        assert response.headers.get("content-encoding") is None
