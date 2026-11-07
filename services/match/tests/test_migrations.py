"""迁移框架的行为：版本推进、幂等、拒绝降级、旧库升级。

为什么值得独立测：迁移错了会让**已有数据**打不开或写坏，
而且往往只在"从旧版本升级"这条路径上暴露——新人从零建库永远测不到。
"""

from __future__ import annotations

import sqlite3

import pytest

from services.match.migrations import (
    MIGRATIONS,
    TARGET_VERSION,
    SchemaTooNewError,
    current_version,
    describe,
    migrate,
    pending,
)


def _fresh() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE events (
          match_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL,
          player TEXT, at TEXT NOT NULL, payload TEXT NOT NULL,
          PRIMARY KEY (match_id, seq)
        );
        CREATE TABLE seasons (season_id TEXT PRIMARY KEY, name TEXT, created_at TEXT);
        """
    )
    return conn


def test_fresh_database_reaches_target_version():
    conn = _fresh()
    assert current_version(conn) == 0
    assert migrate(conn) == TARGET_VERSION
    assert current_version(conn) == TARGET_VERSION


def test_migrate_is_idempotent():
    """多副本会各自尝试迁移，重复执行必须无副作用。"""
    conn = _fresh()
    migrate(conn)
    first = current_version(conn)
    assert migrate(conn) == first
    assert migrate(conn) == first


def test_old_database_gains_the_columns_it_needs():
    """1.0 建的库没有 visibility / seasons.game，升级后必须补齐。"""
    conn = _fresh()
    migrate(conn)

    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    season_columns = {row[1] for row in conn.execute("PRAGMA table_info(seasons)")}
    assert "visibility" in event_columns
    assert {"game", "status"} <= season_columns


def test_newer_database_is_refused_not_silently_opened():
    """库版本高于程序时必须拒绝：旧代码读新库会把数据写坏。"""
    conn = _fresh()
    migrate(conn)
    conn.execute(f"PRAGMA user_version = {TARGET_VERSION + 5}")
    with pytest.raises(SchemaTooNewError) as exc:
        migrate(conn)
    assert "高于本程序" in str(exc.value)


def test_pending_lists_only_missing_steps():
    conn = _fresh()
    assert pending(conn) == [m.version for m in MIGRATIONS]
    migrate(conn)
    assert pending(conn) == []


def test_every_migration_is_described():
    """迁移清单要给运维看：没有说明的迁移等于黑盒。"""
    items = describe()
    assert len(items) == len(MIGRATIONS)
    for item in items:
        assert item["description"], item


def test_migrations_have_unique_increasing_versions():
    versions = [m.version for m in MIGRATIONS]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)
    assert versions[0] == 1, "版本号必须从 1 开始，否则 user_version=0 无法表达"


def test_failure_mid_migration_leaves_version_unchanged(monkeypatch):
    """某步失败时不能把版本号往前推，否则下次启动会跳过它。"""
    conn = _fresh()
    from services.match import migrations as module

    def boom(_conn):
        raise RuntimeError("simulated failure")

    broken = module.Migration(99, "boom", boom)
    # 只跑"到最后一个正常版本"+ 一个坏步骤
    monkeypatch.setattr(module, "MIGRATIONS", (*module.MIGRATIONS, broken))
    monkeypatch.setattr(module, "TARGET_VERSION", 99)
    with pytest.raises(RuntimeError):
        module.migrate(conn)
    # 已经成功的步骤保留，失败那步不推进
    assert current_version(conn) == TARGET_VERSION
