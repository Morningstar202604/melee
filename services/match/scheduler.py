"""定时开赛：交给 APScheduler 调度，不再自己写线程 + sleep 循环。

为什么换：手写循环要自己处理停止、间隔、异常隔离、多任务并发；
APScheduler 的 BackgroundScheduler 这些都现成，且支持 cron/interval/date 三类触发器。

行为保持：
- 默认关闭，只有配置了赛季才启动；
- 每 tick 只跑 N 场（per_tick），避免一口气打满模型额度；
- 进程重启后从数据库现状继续（已经跑过的场次自动跳过）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .schedule import SeasonBusyError, SeasonRun, run_season

logger = logging.getLogger("melee.schedule")


@dataclass
class ScheduleConfig:
    season_ids: list[str] = field(default_factory=list)
    interval_s: float = 120.0
    per_tick: int = 1
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        from .settings import settings

        cfg = settings()
        seasons = cfg.season_ids
        if not seasons:
            return cls()
        return cls(
            season_ids=seasons,
            interval_s=cfg.schedule_interval,
            per_tick=cfg.schedule_per_tick,
            enabled=True,
        )


class SeasonScheduler:
    """用 APScheduler 的 interval job 推进赛季。"""

    def __init__(
        self,
        config: ScheduleConfig,
        db_path: str | Path,
        *,
        run: Callable[..., SeasonRun] = run_season,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self.config = config
        self.db_path = db_path
        self._run = run
        self._scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.ticks = 0
        self.last_error: str | None = None
        self._job_id = "melee-season-tick"

    @property
    def running(self) -> bool:
        return bool(self._scheduler.running and self._scheduler.get_job(self._job_id))

    def start(self) -> None:
        if not self.config.enabled or self.running:
            return
        self._scheduler.add_job(
            self.tick,
            trigger=IntervalTrigger(seconds=self.config.interval_s),
            id=self._job_id,
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        if not self._scheduler.running:
            self._scheduler.start()
        logger.info("赛季调度已启动：%s 每 %.0fs", self.config.season_ids, self.config.interval_s)

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

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
            except SeasonBusyError:
                # 别的副本正在推进：这一轮跳过，不算错误。
                continue
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
