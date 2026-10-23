#!/usr/bin/env python
"""SQLite 在线备份：用官方 backup API，不加锁、不影响正在进行的比赛。

用法：
    python scripts/backup.py --db data/match.db --out backups/
    python scripts/backup.py --db data/match.db --out backups/ --keep 7

恢复（服务已停）：
    cp backups/match-20261023-030000.db data/match.db
    rm -f data/match.db-wal data/match.db-shm
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def backup(db: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{db.stem}-{stamp}.db"
    source = sqlite3.connect(str(db))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def prune(out_dir: Path, prefix: str, keep: int) -> list[Path]:
    files = sorted(out_dir.glob(f"{prefix}-*.db"), reverse=True)
    removed = []
    for path in files[keep:]:
        path.unlink()
        removed.append(path)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/match.db")
    parser.add_argument("--out", default="backups")
    parser.add_argument("--keep", type=int, default=7, help="保留最近 N 份")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"数据库不存在：{db}", file=sys.stderr)
        return 2
    target = backup(db, Path(args.out))
    size = target.stat().st_size
    removed = prune(Path(args.out), db.stem, args.keep)
    print(f"已备份 {db} → {target}（{size / 1024:.0f} KB）")
    for path in removed:
        print(f"已清理旧备份 {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
