"""批量导入外部 episode。

单场导入是产品入口，批量导入是"把 Kaggle 数据集真的搬进来"的运营动作。
这里只做文件发现、逐场导入和一份可复核的成绩单；所有解析仍走
``episode.parse_episode``，不另造一套规则。
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Callable

from .episode import MAX_EPISODE_BYTES, import_episode


def discover_episode_files(
    path: str | Path, *, pattern: str = "*.json"
) -> list[Path]:
    """接收单文件、目录或 glob，返回按路径排序的 JSON 文件列表。"""
    target = Path(path)
    if target.is_file():
        return [target]
    if not target.exists():
        raise ValueError(f"path does not exist: {target}")
    files = sorted(
        (p for p in target.rglob(pattern) if p.is_file()),
        key=lambda p: str(p),
    )
    if not files:
        raise ValueError(f"no files matching {pattern!r} under {target}")
    return files


def select_files(
    files: list[Path],
    *,
    limit: int = 0,
    shuffle_seed: int | None = None,
    sample: str = "hash",
) -> list[Path]:
    """挑出要导入的文件。

    旧实现按文件名取前 N 个，Kaggle 的文件名是数字递增，前面几百场
    恰好来自同一批模型，100 场里 98 场出现 claude-fable-5。这里默认
    按 `sha256(seed:path)` 排序后取，文件顺序被打散但仍可复现；
    需要严格复现旧行为时显式传 `sample='first'`。
    """
    selected = list(files)
    if sample == "random":
        random.Random(shuffle_seed if shuffle_seed is not None else 0).shuffle(selected)
    elif sample == "hash":
        seed = str(shuffle_seed if shuffle_seed is not None else 0)
        selected.sort(key=lambda p: hashlib.sha256(f"{seed}:{p}".encode()).hexdigest())
    elif sample != "first":
        raise ValueError(f"unknown sample mode: {sample}")
    if shuffle_seed is not None and sample != "random":
        # 保留旧 --shuffle 的语义：给了种子就随机打散。
        random.Random(shuffle_seed).shuffle(selected)
    if limit and limit > 0:
        selected = selected[:limit]
    return selected


def import_batch(
    db_path: str | Path,
    files: list[Path],
    *,
    fmt: str = "auto",
    limit: int = 0,
    shuffle_seed: int | None = None,
    sample: str = "hash",
    on_item: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    import_document: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """逐场导入并返回成绩单。

    一场失败不影响下一场：真实数据集里总有字段缺失或坏文件，批量入口的
    价值是"把能转播的都转播掉"，不是第一场失败就整体退出。
    """
    selected = select_files(files, limit=limit, shuffle_seed=shuffle_seed, sample=sample)

    items: list[dict[str, Any]] = []
    imported = 0
    duplicates = 0
    for file in selected:
        if should_stop and should_stop():
            break
        item: dict[str, Any] = {"file": str(file), "status": "failed"}
        try:
            size = file.stat().st_size
            if size > MAX_EPISODE_BYTES:
                raise ValueError(f"episode file too large: {size} > {MAX_EPISODE_BYTES} bytes")
            document = json.loads(file.read_text(encoding="utf-8"))
            result = (
                import_document(document)
                if import_document is not None
                else import_episode(db_path, document, fmt=fmt, metadata=metadata)
            )
            status = "duplicate" if result.get("deduplicated") else "imported"
            item.update(
                {
                    "status": status,
                    "matchId": result.get("matchId"),
                    "episodeId": result.get("episodeId"),
                    "source": result.get("source"),
                    "game": result.get("game"),
                    "players": result.get("players"),
                    "winner": result.get("winner"),
                    "steps": result.get("steps"),
                    "durationSec": result.get("durationSec"),
                    "events": result.get("events"),
                    "bytes": size,
                }
            )
            if status == "duplicate":
                duplicates += 1
            else:
                imported += 1
        except Exception as exc:  # noqa: BLE001 - 批量报告需要保留每场失败原因
            item["error"] = f"{type(exc).__name__}: {exc}"
        items.append(item)
        if on_item:
            on_item(item)

    failed = sum(1 for item in items if item["status"] == "failed")
    sources: dict[str, int] = {}
    for item in items:
        source = str(item.get("source") or "")
        if source:
            sources[source] = sources.get(source, 0) + 1
    meta = metadata or {}
    return {
        "format": fmt,
        "db": str(db_path),
        "found": len(files),
        "selected": len(selected),
        "sample": sample,
        "imported": imported,
        "duplicates": duplicates,
        "failed": failed,
        "bytes": sum(int(item.get("bytes") or 0) for item in items),
        "sources": sources,
        "sourceLabel": meta.get("sourceLabel", ""),
        "sourceUrl": meta.get("sourceUrl", ""),
        "license": meta.get("license", ""),
        "items": items,
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
