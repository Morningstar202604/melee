"""测试公共夹具：一套 client / 等待 / 建场，六个测试文件不再各写一遍。

之前 `client` / `wait_finished` / `create` 在 6 个文件里重复，改一次要改 6 处，
而且各自的超时与断言口径逐渐分叉。这里只保留一份实现。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from services.match import api as api_module
from services.match.eventlog import read_match
from services.match.worker import ensure_inline_worker, stop_inline_worker


def make_client(tmp_path: Path, monkeypatch: Any, *, worker: bool = True) -> TestClient:
    """独立临时库的 API 客户端。

    `worker=False` 时不启动内嵌 worker（只测 HTTP 接线，不跑比赛）。
    TestClient 不跑 lifespan，所以 worker 必须显式拉起。
    """
    db_path = Path(tmp_path) / "match.db"
    # 历史测试直接读 api.DB_PATH 定位临时库；保持这一契约。
    monkeypatch.setattr(api_module, "DB_PATH", db_path)
    client = TestClient(api_module.create_app(db_path))
    if worker:
        ensure_inline_worker(db_path)
    return client


def stop_worker() -> None:
    stop_inline_worker()


def wait_finished(match_id: str, timeout: float = 60.0) -> None:
    """等比赛结束：写路径走队列后只能看日志里有没有终态事件。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            events = read_match(api_module.DB_PATH, match_id)
        # aqg: top-level boundary
        except Exception:  # noqa: BLE001 - 表还没建好
            events = []
        if any(event["type"] == "match.ended" for event in events):
            return
        time.sleep(0.05)
    raise TimeoutError(f"{match_id} 在 {timeout}s 内没有结束")


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """建一场二十问；overrides 覆盖默认字段。"""
    body: dict[str, Any] = {
        "game": "twenty-questions",
        "seed": 7,
        "maxQuestions": 6,
        "players": [{"id": "A"}, {"id": "B"}],
    }
    body.update(overrides)
    response = client.post("/matches", json=body)
    assert response.status_code == 201, response.text
    return response.json()
