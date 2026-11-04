"""SQLite 建表缓存：同一进程、同一路径只执行一次 DDL。

之前 `EventLog.open`、`read_match`、`workqueue.connect`、`auth.check_and_consume`
每次调用都 `executescript(SCHEMA)`。一次读事件就要跑一遍建表语句，
导播台 1.5 秒轮询一次，DDL 成了最热的 SQL。DDL 幂等但绝不廉价：
它会拿写锁，和 worker 的写入互相排队。

这里按 (数据库绝对路径, scope) 记住"已经建过表"，进程内只建一次。
测试换库时路径不同，天然隔离；同一路径删库重建的场景调用 `reset_schema_cache()`。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

_READY: set[tuple[str, str]] = set()
_LOCK = threading.Lock()


def ensure_schema(conn: Any, db_path: str | Path, schema: str, scope: str) -> bool:
    """确保 `scope` 对应的表已存在；同一进程同一库只执行一次。

    返回本次是否真的执行了 DDL——调用方据此决定要不要做一次性的库级设置
    （例如切 WAL），避免每次 open 都抢写锁。
    """
    key = (str(Path(db_path).resolve()), scope)
    if key in _READY:
        return False
    with _LOCK:
        if key in _READY:
            return False
        conn.executescript(schema)
        _READY.add(key)
        return True


def ensure_schema_for(conn: Any, schema: str, scope: str) -> bool:
    """连接版：从 PRAGMA database_list 取出主库路径后走 `ensure_schema`。

    给只拿得到连接、拿不到路径的调用方（赛季、凭据等）使用。
    """
    row = conn.execute("PRAGMA database_list").fetchone()
    path = row[2] if row and row[2] else ""
    if not path:
        # 内存库（测试里的 :memory:）：没有跨调用复用的意义，直接建。
        conn.executescript(schema)
        return True
    return ensure_schema(conn, path, schema, scope)


def reset_schema_cache() -> None:
    """测试与「删库重建」场景用：忘掉已建表记录。"""
    with _LOCK:
        _READY.clear()
