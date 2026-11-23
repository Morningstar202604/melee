"""数据库迁移：显式版本号 + 有序步骤 + 向前兼容检查。

问题：旧实现的迁移散落在各模块里（`eventlog._migrate_visibility`、
`season._migrate`），每处都用 "PRAGMA table_info 看列在不在" 的方式补列。
这有三个真实后果：

1. **没有版本号**：无法回答"这个库是哪一版建的"，升级时只能靠猜；
2. **不可审计**：不知道一共有哪些迁移、谁跑过、跑了几次；
3. **无降级保护**：把新库拿给旧版本用，旧代码会在缺列上炸，
   而不是明确拒绝启动。

现在统一到 `user_version`（SQLite 自带的库级整数，不需要额外表）：
- 每个迁移有递增版本、说明、幂等的 apply 函数；
- 启动时对比 `user_version` 与 `TARGET_VERSION`：
  - 落后 → 按序执行缺失的迁移，每步一个事务；
  - 超前 → 拒绝启动并说明（旧代码不能读新库，硬跑会写坏数据）；
- 迁移本身必须幂等，重复执行不报错（多副本同时启动会各自尝试）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import connect, ensure_schema


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - table 名来自本模块常量


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """加列是幂等的：已经存在就什么都不做。"""
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")  # noqa: S608


def _m001_visibility(conn: sqlite3.Connection) -> None:
    """1.0：私有事件可见性。"""
    if "events" in _tables(conn):
        _add_column(conn, "events", "visibility", "TEXT")


def _m002_season_game_status(conn: sqlite3.Connection) -> None:
    """1.4：赛季绑定赛制与状态。"""
    if "seasons" in _tables(conn):
        _add_column(conn, "seasons", "game", "TEXT NOT NULL DEFAULT 'twenty-questions'")
        _add_column(conn, "seasons", "status", "TEXT NOT NULL DEFAULT 'active'")


def _m003_leaderboard_index(conn: sqlite3.Connection) -> None:
    """1.9：排行榜按 (type, seq) 索引，去掉全表扫描。"""
    if "events" in _tables(conn):
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_seq ON events (type, seq DESC)")


def _m004_match_source(conn: sqlite3.Connection) -> None:
    """1.9.9：区分站内赛事与外部导入，并给导入场次留出处主键。

    source 是稳定枚举（melee / kaggle-game-arena / textarena / external），
    source_id 是外部 episode 的原始 ID；两者一起承担幂等。
    旧库没有这两列时，历史比赛统一视为 melee，不做来源推断，
    避免把用户自己的比赛误标成导入。
    """
    if "matches" in _tables(conn):
        _add_column(conn, "matches", "source", "TEXT NOT NULL DEFAULT 'melee'")
        _add_column(conn, "matches", "source_id", "TEXT")
        _add_column(conn, "matches", "played_at", "TEXT")
        # 旧库已有导入场（1.9.8），不能一律标成 melee：从 match.created
        # 的 config.imported 回填来源和 episodeId，并把首事件时间作为
        # played_at，统计页才不会把历史导入当成今天发生的站内赛事。
        conn.execute(
            "UPDATE matches SET played_at = COALESCE(played_at, "
            "(SELECT MIN(e.at) FROM events e WHERE e.match_id = matches.match_id))"
        )
        rows = conn.execute(
            "SELECT m.match_id, e.payload FROM matches m JOIN events e "
            "ON e.match_id = m.match_id AND e.type = 'match.created'"
        ).fetchall()
        for match_id, payload in rows:
            try:
                config = json.loads(payload).get("config") or {}
            except (TypeError, json.JSONDecodeError):
                continue
            if not config.get("imported"):
                continue
            source = str(config.get("source") or "external")
            source_id = str(config.get("episodeId") or "").strip()
            conn.execute(
                "UPDATE matches SET source = ?, source_id = ? WHERE match_id = ?",
                (source, source_id or None, match_id),
            )
        # 旧版本重导入可能留下同 (source, source_id) 的重复场。唯一索引
        # 不能因为历史脏数据建不起来；保留最早一场的 source_id，
        # 其余置 NULL（仍可观看，只是不再参与幂等键）。
        duplicates = conn.execute(
            "SELECT source, source_id, COUNT(*) FROM matches "
            "WHERE source_id IS NOT NULL AND source_id != '' "
            "GROUP BY source, source_id HAVING COUNT(*) > 1"
        ).fetchall()
        for source_value, source_id_value, _count in duplicates:
            keep = conn.execute(
                "SELECT match_id FROM matches WHERE source = ? AND source_id = ? "
                "ORDER BY created_at LIMIT 1",
                (source_value, source_id_value),
            ).fetchone()
            if keep:
                conn.execute(
                    "UPDATE matches SET source_id = NULL WHERE source = ? AND source_id = ? "
                    "AND match_id != ?",
                    (source_value, source_id_value, keep[0]),
                )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_source_id "
            "ON matches (source, source_id) WHERE source_id IS NOT NULL AND source_id != ''"
        )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "events.visibility", _m001_visibility),
    Migration(2, "seasons.game/status", _m002_season_game_status),
    Migration(3, "index events(type, seq)", _m003_leaderboard_index),
    Migration(4, "matches source/source_id/played_at", _m004_match_source),
)

TARGET_VERSION = MIGRATIONS[-1].version


class SchemaTooNewError(RuntimeError):
    """库的版本高于本程序。旧代码读新库会写坏数据，必须明确拒绝。"""


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    # PRAGMA 不支持参数绑定，版本号来自本模块常量，不来自外部输入。
    conn.execute(f"PRAGMA user_version = {int(version)}")  # noqa: S608


def migrate(conn: sqlite3.Connection, *, on_progress: Callable[[str], None] | None = None) -> int:
    """把连接上的库迁到 TARGET_VERSION，返回最终版本。

    每步一个事务：中途崩溃时已完成的步骤保持生效，未完成的不留半截。
    """
    version = current_version(conn)
    if version > TARGET_VERSION:
        raise SchemaTooNewError(
            f"数据库版本 {version} 高于本程序支持的 {TARGET_VERSION}；"
            "请升级程序，或从备份恢复旧库。继续运行会写坏数据。"
        )
    for migration in MIGRATIONS:
        if migration.version <= version:
            continue
        if on_progress:
            on_progress(f"migrating {migration.version}: {migration.description}")
        conn.execute("BEGIN IMMEDIATE")
        try:
            migration.apply(conn)
            _set_version(conn, migration.version)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        version = migration.version
    return version


def migrate_path(db_path: str | Path, schema: str, scope: str, *, on_progress: Callable[[str], None] | None = None) -> int:
    """建表 + 迁移的入口：先确保基础 schema，再跑迁移。

    `ensure_schema` 带进程内缓存（同一库只建一次表），迁移同样应只跑一次，
    但它还要处理"库是别的进程建的"这种情况，所以每次都检查版本号——
    已经是最新版时只是一次 PRAGMA 读，代价可以忽略。
    """
    conn = connect(str(db_path), timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        ensure_schema(conn, db_path, schema, scope)
        return migrate(conn, on_progress=on_progress)
    finally:
        conn.close()


def describe() -> list[dict[str, Any]]:
    """给运维/文档用的迁移清单（`melee doctor` 会打印它）。"""
    return [
        {"version": m.version, "description": m.description}
        for m in MIGRATIONS
    ]


def pending(conn: sqlite3.Connection) -> list[int]:
    version = current_version(conn)
    return [m.version for m in MIGRATIONS if m.version > version]


def iter_migrations() -> Iterable[Migration]:
    return iter(MIGRATIONS)
