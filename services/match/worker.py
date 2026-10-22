"""任务执行器：任意副本都能跑比赛与渲染。

两种运行方式：
- 内嵌：随 API 进程启动（默认），单实例部署不需要额外进程。
- 独立：`python -m services.match.worker`，多副本时每个副本一个。

两者用同一张队列表与租约机制，谁抢到谁跑，不会重复执行。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import workqueue
from .agents.scripted import GreedyAgent
from .clips import build_clips
from .director import Director
from .engine import registry as game_registry
from .engine.twenty_questions import CATALOG
from .eventlog import EventLog, read_match
from .narrator import narrator_from_env
from .render import find_ffmpeg, render_clip
from .runner import Runner
from .season import load, persist
import sqlite3

logger = logging.getLogger("melee.worker")

MATCH_KIND = "run-match"
RENDER_KIND = "render-clip"


def owner_id(prefix: str = "node") -> str:
    return f"{prefix}-{socket.gethostname()}-{os.getpid()}-{threading.get_ident()}"


def enqueue_match(
    db_path: str | Path,
    *,
    match_id: str,
    seed: int,
    players: list[str],
    max_questions: int = 21,
    season_id: str | None = None,
    specs: list[dict[str, Any]] | None = None,
    cipher: Any | None = None,
    game: str = "twenty-questions",
) -> str:
    """把"跑这一场"放进队列。

    specs 与 API 的 PlayerSpec 同构。带 model 的条目里含有 API Key，
    入队前必须用 SecretCipher 封存——队列是落盘存储，不是内存。
    """
    from .secrets import cipher_from_env, seal_specs

    sealed = seal_specs(specs or [], cipher or cipher_from_env())
    return workqueue.enqueue(
        db_path,
        MATCH_KIND,
        {
            "matchId": match_id,
            "game": game,
            "seed": seed,
            "players": players,
            "maxQuestions": max_questions,
            "seasonId": season_id,
            "specs": sealed.as_dict(),
        },
        job_id=f"job-{match_id}",
    )


def enqueue_render(
    db_path: str | Path,
    *,
    match_id: str,
    limit: int = 3,
    per_event_ms: int = 900,
) -> str:
    return workqueue.enqueue(
        db_path,
        RENDER_KIND,
        {"matchId": match_id, "limit": limit, "perEventMs": per_event_ms},
    )


# ---------- 执行体 ----------

def run_match_job(db_path: str | Path, payload: dict[str, Any]) -> None:
    from .player_specs import PlayerSpec, build_agents

    from .secrets import cipher_from_env, open_specs

    match_id = payload["matchId"]
    players = list(payload["players"])
    raw_specs = open_specs(payload.get("specs") or {}, cipher_from_env())
    specs = [PlayerSpec(**spec) for spec in raw_specs]

    game_id = payload.get("game") or "twenty-questions"
    game = game_registry.get(game_id)
    # 只有二十问认 maxQuestions；别的赛制传了也白传，还可能触发 KeyError。
    config = {"maxQuestions": payload.get("maxQuestions", 21)} if game_id == "twenty-questions" else {}
    state = game.init(seed=payload["seed"], players=players, config=config)

    log = EventLog.open(db_path, match_id)
    # 事件可能已经存在（任务重试）：只在第一条不存在时写 match.created。
    if not log.read():
        log.create_match(game=game.game_id, seed=payload["seed"], players=players)
    log.close()

    # 重试时从已有进度继续：重放已落库的动作，再接着跑。
    from .eventlog import EventLog as _Log
    from .rehydrate import rehydrate

    resume = False
    try:
        resumed = rehydrate(db_path, match_id)
        state = resumed.state
        game = resumed.game
        resume = bool(resumed.events)
    except Exception:  # noqa: BLE001 - 还没有日志时正常走新局
        pass

    log = _Log.open(db_path, match_id)
    agents = build_agents(specs, game_id) if specs else _default_agents(players, game_id)
    if resume and state.get("finished"):
        # 已经跑完了，只补记分，不再重跑。
        log.close()
    else:
        Runner(
            game, log, agents, narrator=narrator_from_env(), director=Director(), resume=resume
        ).play(state)
        log.close()

    season_id = payload.get("seasonId")
    if season_id:
        winner = _winner_of(db_path, match_id)
        reason = _reason_of(db_path, match_id)
        conn = sqlite3.connect(str(db_path))
        try:
            season = load(conn, season_id)
            # record 对同一 matchId 是覆盖语义，重试不会重复计分。
            if len(players) == 2:
                season.record(match_id, players[0], players[1], winner, reason)
            persist(conn, season)
        finally:
            conn.close()


def render_job(db_path: str | Path, payload: dict[str, Any]) -> None:
    if find_ffmpeg() is None:
        raise RuntimeError("没有 ffmpeg")

    match_id = payload["matchId"]
    events = read_match(db_path, match_id)
    if not events:
        raise RuntimeError(f"没有这场比赛的日志：{match_id}")

    clips = build_clips(events, limit=payload.get("limit", 3), per_event_ms=payload.get("perEventMs", 900))
    out_dir = Path(db_path).parent / "clips" / match_id
    for index, clip in enumerate(clips, start=1):
        render_clip(
            clip,
            out_dir / f"{index:02d}-{clip.kind}.mp4",
            per_event_ms=payload.get("perEventMs", 900),
            all_events=events,
        )


def _winner_of(db_path: str | Path, match_id: str) -> str | None:
    for event in read_match(db_path, match_id):
        if event["type"] == "match.ended":
            return event["payload"]["winner"]
    return None


def _reason_of(db_path: str | Path, match_id: str) -> str:
    for event in read_match(db_path, match_id):
        if event["type"] == "match.ended":
            return event["payload"]["reason"]
    return ""


def _default_agents(players: list[str], game_id: str) -> dict[str, Any]:
    """没带选手声明时，按赛制给默认对手。"""
    if game_id == "twenty-questions":
        return {p: GreedyAgent(p, CATALOG) for p in players}
    if game_id == "codenames":
        from .agents.scripted import CodenamesAgent

        return {p: CodenamesAgent(p) for p in players}
    from .agents.scripted import BoardAgent

    return {p: BoardAgent(p, game_id) for p in players}


HANDLERS: dict[str, Callable[[str | Path, dict[str, Any]], None]] = {
    MATCH_KIND: run_match_job,
    RENDER_KIND: render_job,
}


# ---------- 轮询器 ----------

@dataclass
class WorkerConfig:
    kinds: tuple[str, ...] = (MATCH_KIND, RENDER_KIND)
    interval_s: float = 0.5
    lease_s: float = 180.0
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        from .settings import settings

        cfg = settings()
        return cls(
            kinds=tuple(cfg.worker_kind_list),
            interval_s=cfg.worker_interval,
            lease_s=cfg.worker_lease,
            enabled=cfg.worker,
        )


class Worker:
    def __init__(self, db_path: str | Path, config: WorkerConfig | None = None) -> None:
        self.db_path = db_path
        self.config = config or WorkerConfig()
        self.owner = owner_id()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.processed = 0
        self.failed = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if not self.config.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="melee-worker", daemon=True)
        self._thread.start()
        logger.info("worker 已启动：%s", self.owner)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.run_once()
            if job is None:
                self._stop.wait(self.config.interval_s)

    def run_once(self) -> workqueue.Job | None:
        """领一个任务并执行。返回领到的任务（没有则 None）。"""
        job = workqueue.claim(self.db_path, self.owner, self.config.kinds, lease_s=self.config.lease_s)
        if job is None:
            return None

        # 长任务续租：一局比赛可能跑几分钟，默认租约 180s 到期后
        # 别的副本会把同一场再领一次，导致重复的 match.ended。
        stop_renew = threading.Event()

        def renew_loop() -> None:
            # 续租间隔必须小于租约本身：取 1/3 留两次重试余量，
            # 下限 0.2s 是为了让短租约（测试/小任务）也能生效。
            interval = max(0.2, self.config.lease_s / 3)
            while not stop_renew.wait(interval):
                if not workqueue.renew(self.db_path, job.job_id, self.owner, self.config.lease_s):
                    logger.warning("任务 %s 租约已被接手，当前执行继续但不再续租", job.job_id)
                    return

        renewer = threading.Thread(target=renew_loop, name="melee-lease-renew", daemon=True)
        renewer.start()

        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"未知任务类型：{job.kind}")
            handler(self.db_path, job.payload)
        except Exception as exc:  # noqa: BLE001 - 任务失败要落库，不能杀死轮询
            self.failed += 1
            self.last_error = str(exc)
            status = workqueue.fail(self.db_path, job.job_id, str(exc))
            logger.warning("任务 %s 失败（%s）：%s", job.job_id, status, exc)
        else:
            self.processed += 1
            workqueue.complete(self.db_path, job.job_id)
        finally:
            stop_renew.set()
        return job


_QUEUE_WORKER: Worker | None = None


def start_from_env(db_path: str | Path) -> Worker | None:
    global _QUEUE_WORKER
    config = WorkerConfig.from_env()
    if not config.enabled:
        return None
    if _QUEUE_WORKER is None:
        _QUEUE_WORKER = Worker(db_path, config)
        _QUEUE_WORKER.start()
    return _QUEUE_WORKER


def main() -> int:
    import argparse
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="melee 任务执行器")
    parser.add_argument("--db", default=str(__import__("services.match.settings", fromlist=["settings"]).settings().db))
    parser.add_argument("--once", action="store_true", help="只跑一轮就退出")
    args = parser.parse_args()

    worker = Worker(args.db)
    if args.once:
        job = worker.run_once()
        print("执行了" if job else "没有可执行任务")
        return 0 if job else 1

    worker.start()
    print(f"worker 运行中：{args.db}（Ctrl+C 结束）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
