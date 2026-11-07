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

def test_import_reports_what_is_wrong(tmp_path):
    """坏导入必须说清坏在哪。

    旧实现把六种不同的问题都报成 unsupported export format，
    调用方拿到这句话无法判断是缺字段、seq 断号还是赛制名写错。
    """
    import pytest

    from services.match.portable import import_match

    db = tmp_path / "p.db"
    cases = {
        "format must be": {"game": "nim", "players": ["A", "B"], "events": []},
        "game must be": {"format": "melee.export/1", "players": ["A", "B"], "events": []},
        "events must be a list": {"format": "melee.export/1", "game": "nim", "players": ["A", "B"]},
    }
    for expected, document in cases.items():
        with pytest.raises(ValueError) as exc:
            import_match(db, document)
        assert expected in str(exc.value), (expected, str(exc.value))


def test_import_is_all_or_nothing(tmp_path):
    """中途失败不能留下半场数据。

    旧实现边校验边 append，每条 append 都 commit；第 N 条 seq 断号时，
    前 N-1 条已经落库，那场比赛永远无法重放，还占着 match id。
    """
    import sqlite3

    import pytest

    from services.match.portable import import_match

    db = tmp_path / "atomic.db"
    document = {
        "format": "melee.export/1",
        "game": "nim",
        "players": ["A", "B"],
        "seed": 1,
        "events": [
            {"seq": 0, "type": "match.created", "payload": {"game": "nim", "players": ["A", "B"]}},
            {"seq": 1, "type": "match.started", "payload": {"firstPlayer": "A"}},
            {"seq": 7, "type": "match.ended", "payload": {"winner": "A", "reason": "x", "finalState": {}}},
        ],
    }
    with pytest.raises(ValueError):
        import_match(db, document)

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] if _has_table(conn, "events") else 0
    finally:
        conn.close()
    assert rows == 0, f"失败导入残留了 {rows} 条事件"


def _has_table(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None

def test_validate_export_covers_every_shape(tmp_path):
    """校验器要能说清每一种坏法，而不是笼统报错。

    逐条覆盖分支：非对象、事件上限、事件不是对象、缺字段、字段类型错、
    seq 不连续、问题过多时截断。
    """
    from services.match.portable import EXPORT_FORMAT, MAX_IMPORT_EVENTS, validate_export

    good = {"format": EXPORT_FORMAT, "game": "nim", "players": ["A", "B"],
            "events": [{"seq": 0, "type": "match.created", "payload": {"game": "nim"}}]}
    assert validate_export(good) == []

    assert validate_export("not a dict") == ["document must be a JSON object"]

    too_many = {**good, "events": [{"seq": i, "type": "x", "payload": {}} for i in range(MAX_IMPORT_EVENTS + 1)]}
    assert any("exceeds" in problem for problem in validate_export(too_many))

    not_object = {**good, "events": ["nope"]}
    assert any("must be an object" in problem for problem in validate_export(not_object))

    missing = {**good, "events": [{"seq": 0}]}
    assert any("missing" in problem for problem in validate_export(missing))

    wrong_types = {**good, "events": [{"seq": "0", "type": 1, "payload": []}]}
    problems = validate_export(wrong_types)
    assert any(".seq must be an integer" in p for p in problems)
    assert any(".type must be a string" in p for p in problems)
    assert any(".payload must be an object" in p for p in problems)

    # 问题过多时截断，不能给客户端返回上千条
    many = {**good, "events": [{"bogus": True} for _ in range(100)]}
    problems = validate_export(many)
    assert len(problems) <= 22
    assert any("omitted" in p for p in problems)


def test_import_rejects_duplicate_match_id(tmp_path):
    """同 id 导入必须拒绝：否则两份导出互相覆盖。

    新 id 由服务端生成，这里直接指定同一个 id 走两次。
    """
    import pytest

    from services.match.portable import import_match

    db = tmp_path / "dup.db"
    document = {
        "format": "melee.export/1", "game": "nim", "players": ["A", "B"], "seed": 1,
        "events": [{"seq": 0, "type": "match.created", "payload": {"game": "nim", "players": ["A", "B"]}}],
    }
    import_match(db, document, new_match_id="m_fixed")
    with pytest.raises(ValueError) as exc:
        import_match(db, document, new_match_id="m_fixed")
    assert "already exists" in str(exc.value)


def test_import_marks_the_match_as_ended(tmp_path):
    """带 endedAt 的导出要落成"已结束"，否则赛事库会把回放场显示成直播中。"""
    import sqlite3

    from services.match.portable import import_match

    db = tmp_path / "ended.db"
    document = {
        "format": "melee.export/1", "game": "nim", "players": ["A", "B"], "seed": 1,
        "endedAt": "2026-01-01T00:01:00Z",
        "events": [
            {"seq": 0, "type": "match.created", "payload": {"game": "nim", "players": ["A", "B"]}},
            {"seq": 1, "type": "match.ended",
             "payload": {"winner": "A", "reason": "x", "finalState": {}}},
        ],
    }
    result = import_match(db, document)
    conn = sqlite3.connect(db)
    try:
        ended = conn.execute(
            "SELECT ended_at FROM matches WHERE match_id = ?", (result["matchId"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert ended, "导入的比赛没有标记结束时间"


def test_import_rolls_back_on_write_error(tmp_path, monkeypatch):
    """写入途中数据库报错时，整个导入必须回滚（不留半场）。"""
    import sqlite3

    import pytest

    from services.match import portable

    db = tmp_path / "rollback.db"
    document = {
        "format": "melee.export/1", "game": "nim", "players": ["A", "B"], "seed": 1,
        "events": [
            {"seq": 0, "type": "match.created", "payload": {"game": "nim", "players": ["A", "B"]}},
            {"seq": 1, "type": "match.started", "payload": {"firstPlayer": "A"}},
        ],
    }
    real_connect = sqlite3.connect

    class Exploding:
        """包装连接：第 2 条事件插入时抛错，验证回滚。"""

        def __init__(self, conn):
            self._conn = conn
            self._inserts = 0

        def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO events"):
                self._inserts += 1
                if self._inserts >= 2:
                    raise sqlite3.OperationalError("disk full (simulated)")
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def fake_connect(path, *args, **kwargs):
        return Exploding(real_connect(path, *args, **kwargs))

    monkeypatch.setattr(portable.sqlite3, "connect", fake_connect)
    with pytest.raises(sqlite3.OperationalError):
        portable.import_match(db, document)

    conn = real_connect(db)
    try:
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone()
        rows = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] if has_table else 0
    finally:
        conn.close()
    assert rows == 0, f"回滚后仍残留 {rows} 条事件"

