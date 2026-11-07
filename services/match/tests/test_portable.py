"""可移植导出/导入：单文件回放、指纹稳定、空库导入、seq 完整性。"""

from __future__ import annotations

import pytest

from services.match.portable import EXPORT_FORMAT, export_match, import_match


@pytest.fixture()
def finished_match_id(client):
    created = client.post(
        "/matches",
        json={"game": "tic-tac-toe", "seed": 77, "players": [{"id": "A"}, {"id": "B"}]},
    ).json()["matchId"]
    import time

    deadline = time.time() + 60
    while time.time() < deadline:
        events = client.get(f"/matches/{created}/events").json()["events"]
        if any(e["type"] == "match.ended" for e in events):
            return created
        time.sleep(0.5)
    raise AssertionError("match never ended")


def test_export_roundtrip(client, finished_match_id):
    document = export_match(client.app.state.db_path, finished_match_id)
    assert document["format"] == EXPORT_FORMAT
    assert document["game"] == "tic-tac-toe"
    assert document["players"] == ["A", "B"]
    assert any(e["type"] == "match.ended" for e in document["events"])
    assert len(document["fingerprint"]) == 16

    imported = import_match(client.app.state.db_path, document)
    assert imported["matchId"] != finished_match_id
    assert imported["events"] == len(document["events"])

    # 导入场次可被同一套读取面消费：recap 数字一致
    original = client.get(f"/matches/{finished_match_id}/recap").json()
    replayed = client.get(f"/matches/{imported['matchId']}/recap").json()
    assert original["turns"] == replayed["turns"]
    assert original["winner"] == replayed["winner"]


def test_import_rejects_bad_format(client):
    with pytest.raises(ValueError):
        import_match(client.app.state.db_path, {"format": "other/1", "events": []})


def test_import_rejects_broken_seq(client, finished_match_id):
    document = export_match(client.app.state.db_path, finished_match_id)
    document["events"] = document["events"][1:]  # 去掉 seq 0
    with pytest.raises(ValueError):
        import_match(client.app.state.db_path, document)


def test_import_into_empty_db(tmp_path, finished_match_id, client):
    """空库导入必须自建表：分享文件可以在全新实例上重放。"""
    document = export_match(client.app.state.db_path, finished_match_id)
    fresh = tmp_path / "fresh.db"
    imported = import_match(fresh, document)
    assert imported["events"] > 0

    # 在新库上再导出一次：事件数一致（幂等快照）
    again = export_match(fresh, imported["matchId"])
    assert len(again["events"]) == len(document["events"])


def test_fingerprint_stable_across_export(client, finished_match_id):
    first = export_match(client.app.state.db_path, finished_match_id)
    second = export_match(client.app.state.db_path, finished_match_id)
    assert first["fingerprint"] == second["fingerprint"]


def test_export_hides_private_events_by_default(client):
    """export 默认必须过滤私有事件——否则导出就成了 visibility 契约的后门。"""
    from services.match.portable import export_match

    # codenames 有私有事件（队长思考 visibility={players:[队长]}）
    created = client.post(
        "/matches",
        json={
            "game": "codenames", "seed": 2027,
            "players": [{"id": n} for n in ("RedA", "BlueB", "RedC", "BlueD")],
        },
    ).json()["matchId"]
    import time
    deadline = time.time() + 90
    while time.time() < deadline:
        events = client.get(f"/matches/{created}/events").json()["events"]
        if any(e["type"] == "match.ended" for e in events):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("codenames match never ended")

    public_export = export_match(client.app.state.db_path, created, reveal=False)
    private_export = export_match(client.app.state.db_path, created, reveal=True)
    def _leaks_content(document):
        """私有事件的内容是否泄漏：占位 payload 只有 redacted 标记，不算泄漏。"""
        for e in document["events"]:
            if isinstance(e.get("visibility"), dict) and e.get("payload") != {"redacted": True}:
                return True
        return False

    assert not _leaks_content(public_export), "默认导出泄漏了私有事件内容"
    assert _leaks_content(private_export), "赛后 reveal 导出应包含私有事件内容"


def test_export_reveal_requires_ended(client):
    """未结束的比赛不允许 reveal 导出（与 /events 契约一致）。"""
    import tempfile
    from pathlib import Path

    # 直接往一个新 matchId 写一条 created 事件（不跑 worker）
    from services.match.eventlog import EventLog
    from services.match.portable import export_match
    tmp = Path(tempfile.mkdtemp()) / "u.db"
    log = EventLog.open(tmp, "m_unfinished")
    log.create_match("tic-tac-toe", 1, ["A", "B"])
    log.append("match.created", {"game": "tic-tac-toe", "players": ["A", "B"], "seed": 1})
    log.close()

    with pytest.raises(ValueError):
        export_match(tmp, "m_unfinished", reveal=True)
