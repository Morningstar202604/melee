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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .schedule import SeasonBusyError, SeasonRun, run_season

logger = logging.getLogger("melee.schedule")


@dataclass
class ScheduleConfig:
    """调度策略。

    `season_ids=None` 表示自动发现：每个 tick 查"有到点未打场次"的赛季。
    旧语义（显式点名）保留为过滤列表；`MELEE_SCHEDULE=off` 整体关闭。
    之前要求显式点名才启用，导致"建赛季给了 startAt 却永远不开赛"——
    API 与前端都接受开赛时间，产品契约就是到点要开。
    """

    season_ids: list[str] | None = None
    interval_s: float = 120.0
    per_tick: int = 1
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "ScheduleConfig":
        from .settings import settings

        cfg = settings()
        raw = cfg.schedule.strip()
        if raw.lower() == "off":
            return cls(enabled=False)
        seasons = [item.strip() for item in raw.split(",") if item.strip()]
        return cls(
            season_ids=seasons or None,
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
        logger.info(
            "赛季调度已启动：%s 每 %.0fs",
            "自动发现到点赛季" if self.config.season_ids is None else self.config.season_ids,
            self.config.interval_s,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def tick(self) -> list[dict[str, Any]]:
        """推进一次。返回本轮跑掉的比赛。"""
        played: list[dict[str, Any]] = []
        # 显式点名时只跑点名的；未点名则扫描全部到点赛季。
        season_ids = self.config.season_ids
        if season_ids is None:
            from .schedule import due_season_ids

            season_ids = due_season_ids(self.db_path)
        for season_id in season_ids:
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


def current() -> SeasonScheduler | None:
    """当前进程里的调度器（未启用时 None）。/scheduler 端点读它。"""
    return _SCHEDULER


def start_from_env(db_path: str | Path) -> SeasonScheduler | None:
    global _SCHEDULER
    config = ScheduleConfig.from_env()
    if not config.enabled:
        return None
    if _SCHEDULER is None:
        _SCHEDULER = SeasonScheduler(config, db_path)
        _SCHEDULER.start()
    return _SCHEDULER


def stop_current() -> None:
    """停掉并清空单例：进程退出或测试切换环境时调用。"""
    global _SCHEDULER
    if _SCHEDULER is not None:
        _SCHEDULER.stop()
        _SCHEDULER = None
