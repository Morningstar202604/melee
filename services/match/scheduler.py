"""定时开赛：按间隔把赛季剩余的 fixture 逐场跑掉。

刻意不做常驻 cron：
- 默认关闭，只有显式设置 MELEE_SCHEDULE 才启动；
- 每次只跑一场，跑完等下一个间隔，避免一口气打满模型额度；
- 进程重启后从数据库现状继续（已经跑过的场次自动跳过）。
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .schedule import SeasonRun, run_season

logger = logging.getLogger("melee.schedule")


@dataclass
class ScheduleConfig:
    season_ids: list[str] = field(default_factory=list)
    interval_s: float = 120.0
    per_tick: int = 1
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        raw = os.environ.get("MELEE_SCHEDULE", "").strip()
        if not raw:
            return cls()
        seasons = [item.strip() for item in raw.split(",") if item.strip()]
        return cls(
            season_ids=seasons,
            interval_s=float(os.environ.get("MELEE_SCHEDULE_INTERVAL", "120")),
            per_tick=int(os.environ.get("MELEE_SCHEDULE_PER_TICK", "1")),
            enabled=bool(seasons),
        )


class SeasonScheduler:
    """一个后台线程，按间隔推进赛季。"""

    def __init__(
        self,
        config: ScheduleConfig,
        db_path: str | Path,
        *,
        run: Callable[..., SeasonRun] = run_season,
    ) -> None:
        self.config = config
        self.db_path = db_path
        self._run = run
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ticks = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if not self.config.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="season-scheduler", daemon=True)
        self._thread.start()
        logger.info("赛季调度已启动：%s 每 %.0fs", self.config.season_ids, self.config.interval_s)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.config.interval_s)

    def tick(self) -> list[dict[str, Any]]:
        """推进一次。返回本轮跑掉的比赛。"""
        played: list[dict[str, Any]] = []
        for season_id in self.config.season_ids:
            try:
                result = self._run(
                    season_id,
                    db_path=self.db_path,
                    max_fixtures=self.config.per_tick,
                )
            except Exception as exc:  # noqa: BLE001 - 调度线程不能因为一场失败而死
                self.last_error = str(exc)
                logger.exception("赛季 %s 推进失败：%s", season_id, exc)
                continue
            played.extend(outcome.as_dict() for outcome in result.outcomes)
        self.ticks += 1
        return played


_SCHEDULER: SeasonScheduler | None = None


def start_from_env(db_path: str | Path) -> SeasonScheduler | None:
    global _SCHEDULER
    config = ScheduleConfig.from_env()
    if not config.enabled:
        return None
    if _SCHEDULER is None:
        _SCHEDULER = SeasonScheduler(config, db_path)
        _SCHEDULER.start()
    return _SCHEDULER
