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
import sqlite3
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
from .runner import Runner, ensure_terminal
from .season import load, persist

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
    sealed_specs: Any | None = None,
    game: str = "twenty-questions",
    config: dict[str, Any] | None = None,
) -> str:
    """把"跑这一场"放进队列。

    specs 与 API 的 PlayerSpec 同构。带 model 的条目里含有 API Key，
    入队前必须用 SecretCipher 封存——队列是落盘存储，不是内存。
    """
    from .secrets import cipher_from_env, seal_specs

    cipher = cipher or cipher_from_env()
    sealed = sealed_specs or seal_specs(specs or [], cipher)
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
            # 编队参数随任务一起落库：worker 可能在另一个进程/副本上执行，
            # 不能依赖任何进程内状态。
            "config": dict(config or {}),
        },
        job_id=f"job-{match_id}",
    )


def enqueue_render(
    db_path: str | Path,
    *,
    match_id: str,
    limit: int = 3,
    per_event_ms: int = 900,
    template: str = "broadcast",
) -> str:
    return workqueue.enqueue(
        db_path,
        RENDER_KIND,
        {"matchId": match_id, "limit": limit, "perEventMs": per_event_ms, "template": template},
    )


# ---------- 执行体 ----------

def run_match_job(db_path: str | Path, payload: dict[str, Any]) -> None:
    from .player_specs import ApiKeyMissing, PlayerSpec, build_agents
    from .secrets import cipher_from_env, open_specs

    match_id = payload["matchId"]
    players = list(payload["players"])
    raw_specs = open_specs(payload.get("specs") or {}, cipher_from_env())
    specs = [PlayerSpec(**spec) for spec in raw_specs]

    try:
        # {env:VAR} 引用在这里解成真实值；缺失时不崩 worker，
        # 把该选手按明确原因弃权（走 judge_missing_key 落事件）。
        specs = resolve_spec_secrets(specs, match_id, db_path)
    except ApiKeyMissing as exc:
        logger.warning("任务 %s 终止：选手 %s 缺少环境凭据 %s", match_id, exc.player_id, exc.variable)
        judge_missing_key(db_path, payload, str(exc), player_id=exc.player_id)
        return

    game, state = _prepare(db_path, payload, match_id, players)

    log = EventLog.open(db_path, match_id)
    try:
        # 重试时从已有进度继续：重放已落库的动作，再接着跑。
        resume = _resume_state(db_path, match_id)
        if resume.state is not None:
            state = resume.state
            game = resume.game
        agents = build_agents(specs, game.game_id) if specs else _default_agents(players, game.game_id)
        if resume.events and state.get("finished"):
            # 已经跑完的只补记分，不再重跑；但终局事件可能因崩溃缺失，
            # 必须补上，否则这一场会永远停在"直播中"。
            ensure_terminal(log, game, state)
        else:
            turn_timeout = _turn_timeout_of(payload)
            Runner(
                game,
                log,
                agents,
                narrator=narrator_from_env(),
                director=Director(),
                resume=resume.events,
                deadline_ms=turn_timeout,
                enforce_deadline=turn_timeout is not None,
            ).play_with_budget(state)
    finally:
        log.close()

    if payload.get("seasonId"):
        _record_season(db_path, payload["seasonId"], match_id, players)

    from .webhooks import dispatch

    dispatch(db_path, match_id)


@dataclass
class _Snapshot:
    state: dict[str, Any] | None = None
    game: Any = None
    events: bool = False


def _turn_timeout_of(payload: dict[str, Any]) -> int | None:
    """取 config.turnTimeoutMs；没设置返回 None（Runner 保持旧行为）。"""
    value = (payload.get("config") or {}).get("turnTimeoutMs")
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def resolve_spec_secrets(specs: list[Any], match_id: str, db_path: str | Path) -> list[Any]:
    """{env:VAR} 解引用；顺便记录用了引用的选手（不记值，只记变量名）。"""
    from .player_specs import resolve_env_references

    resolved = resolve_env_references(specs)
    for spec in resolved:
        for key in ("model", "http"):
            block = getattr(spec, key, None)
            if block is not None and getattr(block, "apiKey", ""):
                logger.info(
                    "比赛 %s 选手 %s 的 %s.apiKey 已从环境变量解析（值不落日志）",
                    match_id,
                    spec.id,
                    key,
                )
    return resolved


def judge_missing_key(
    db_path: str | Path,
    payload: dict[str, Any],
    reason: str,
    *,
    player_id: str | None = None,
) -> None:
    """env 引用缺失：给指定选手记弃权、对手获胜，写完整终局事件。

    比赛要留下可解释的记录，而不是停在 live 或让 worker 报 failed。
    """
    from .engine.base import Move
    from .eventlog import EventLog, has_match

    match_id = payload["matchId"]
    players = list(payload["players"])
    if not has_match(db_path, match_id):
        log = EventLog.open(db_path, match_id)
        try:
            log.create_match(game=payload.get("game") or "twenty-questions", seed=payload["seed"], players=players)
        finally:
            log.close()
    game_obj, state = _prepare(db_path, payload, match_id, players)
    # 新调用通过结构化 player_id 精确指定；旧测试/调用点没有结构化字段时，
    # 只允许完整匹配旧格式，不再用子串猜测，避免重叠 ID 误判。
    legacy_prefix = "选手 "
    legacy_marker = " 的 apiKey 引用了环境变量 "
    legacy_id = reason[len(legacy_prefix) : reason.index(legacy_marker)] if reason.startswith(legacy_prefix) and legacy_marker in reason else None
    forfeited = player_id if player_id in players else legacy_id if legacy_id in players else players[0]
    winner = next((p for p in players if p != forfeited), None)
    log = EventLog.open(db_path, match_id)
    try:
        log.append("turn.started", {"deadlineMs": 30000}, player=forfeited)
        log.append(
            "action.proposed",
            {"action": {"kind": "forfeit", "value": ""}, "raw": reason[:4000]},
            player=forfeited,
        )
        result = game_obj.apply(state, forfeited, Move(kind="forfeit", value=""))
        if result.legal and result.next_state is not None:
            state = result.next_state
            log.append(
                "action.applied",
                {
                    "action": {"kind": "forfeit", "value": ""},
                    "effects": [{"kind": e.kind, **e.detail} for e in result.effects],
                },
                player=forfeited,
            )
            log.append(
                "state.changed",
                {"publicState": result.public_state, "deltas": [{"kind": e.kind, **e.detail} for e in result.effects]},
            )
            winner = state.get("winner") or winner
            end_reason = state.get("endReason") or reason
        else:
            end_reason = reason
        log.append("match.ended", {"winner": winner, "reason": end_reason[:200], "finalState": game_obj.public_state(state)})
        log.mark_ended()
    finally:
        log.close()


def _prepare(db_path: str | Path, payload: dict[str, Any], match_id: str, players: list[str]) -> tuple[Any, dict[str, Any]]:
    game_id = payload.get("game") or "twenty-questions"
    game = game_registry.get(game_id)
    # 赛制配置：先铺默认，再合并调用方显式给的高级编队参数。
    # 不能只按赛制写死——多 AI 编队（teams/teamSize）必须能传进去。
    raw = dict(payload.get("config") or {})
    config: dict[str, Any] = {}
    if game_id == "twenty-questions":
        config["maxQuestions"] = payload.get("maxQuestions", raw.pop("maxQuestions", 21))
    # turnTimeoutMs 是平台层的回合墙钟，不属于任何赛制的状态：不能漏进
    # game.init（引擎收到未知键会忽略，但重放 config 会带着它误导人）。
    raw.pop("turnTimeoutMs", None)
    for key in ("teams", "teamSize", "catalogSize"):
        if key in raw:
            config[key] = raw[key]
    state = game.init(seed=payload["seed"], players=players, config=config)

    log = EventLog.open(db_path, match_id)
    # 事件可能已经存在（任务重试）：只在第一条不存在时写 match.created。
    if not log.read():
        log.create_match(game=game.game_id, seed=payload["seed"], players=players)
    log.close()
    return game, state


def _resume_state(db_path: str | Path, match_id: str) -> _Snapshot:
    """重放已有日志；只有"没有日志"才算新局。

    之前 `except Exception: return _Snapshot()` 会把"日志损坏"也当成新局，
    于是从 seq N 再写一整局，追加第二条 match.created，整场再也无法 rehydrate。
    """
    from .eventlog import read_match
    from .rehydrate import RehydrateError, rehydrate

    try:
        existing = read_match(db_path, match_id)
    # aqg: top-level boundary
    except Exception:  # noqa: BLE001 - 表还没建：新库
        return _Snapshot()
    if not existing:
        return _Snapshot()
    try:
        resumed = rehydrate(db_path, match_id)
    except RehydrateError as exc:
        # 有日志但不能重放：这是数据损坏，必须让任务失败并留下 last_error，
        # 而不是静默重开一局把日志写得更烂。
        raise RuntimeError(f"日志无法重放，任务中止：{exc}") from exc
    return _Snapshot(state=resumed.state, game=resumed.game, events=True)


def _record_season(db_path: str | Path, season_id: str, match_id: str, players: list[str]) -> None:
    winner, reason = _terminal_of(db_path, match_id)
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
            template=payload.get("template", "broadcast"),
        )


def _terminal_of(db_path: str | Path, match_id: str) -> tuple[str | None, str]:
    """一次读日志拿到（胜者, 原因）。之前两个函数各扫一遍全量事件。"""
    for event in read_match(db_path, match_id):
        if event["type"] == "match.ended":
            return event["payload"].get("winner"), str(event["payload"].get("reason", ""))
    return None, ""


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
    # 并发线程数：慢对局（每步几秒）会占满一个线程，多开几个保证新场次能立刻开始。
    concurrency: int = 4

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        from .settings import settings

        cfg = settings()
        return cls(
            kinds=tuple(cfg.worker_kind_list),
            interval_s=cfg.worker_interval,
            lease_s=cfg.worker_lease,
            enabled=cfg.worker,
            concurrency=max(1, cfg.workers),
        )


class Worker:
    def __init__(self, db_path: str | Path, config: WorkerConfig | None = None) -> None:
        self.db_path = db_path
        self.config = config or WorkerConfig()
        self.owner = owner_id()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 之前只记第一个线程：额外的并发线程没有引用，stop() 只能等到它们的
        # 当前任务自然结束，进程退出时它们还在写库。
        self._threads: list[threading.Thread] = []
        self.processed = 0
        self.failed = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if not self.config.enabled or self._thread is not None:
            return
        self._threads = []
        for index in range(self.config.concurrency):
            name = "melee-worker" if index == 0 else f"melee-worker-{index}"
            thread = threading.Thread(target=self._loop, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)
        self._thread = self._threads[0]
        logger.info("worker 已启动：%s（%d 线程）", self.owner, self.config.concurrency)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=3)

    def _loop(self) -> None:
        """轮询循环：任何异常都不能让线程退出。

        之前 `claim()` 撞上 `database is locked` 会让 4 个线程全部死掉，
        进程还在、/health 还 200，但再也没有任务被执行——这种"假活"最危险。
        """
        while not self._stop.is_set():
            try:
                job = self.run_once()
            # aqg: top-level boundary
            except Exception as exc:  # noqa: BLE001 - 轮询线程必须自愈  # aqg: top-level boundary
                self.last_error = str(exc)
                logger.warning("worker 轮询异常，1s 后继续：%s", exc)
                self._stop.wait(max(1.0, self.config.interval_s))
                continue
            if job is None:
                self._stop.wait(self.config.interval_s)

    def run_once(self) -> workqueue.Job | None:
        """领一个任务并执行。返回领到的任务（没有则 None）。"""
        workqueue.reap(self.db_path)
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
            # 锁冲突时不能等满一个 interval 再试：短租约下那已经过期，
            # 别的副本会把同一场领走并重跑（实测出现两条 match.ended）。
            # 这里改成在租约窗口内快速重试，仍失败才放弃这一轮。
            retry_delay = max(0.02, interval / 5)
            while not stop_renew.wait(interval):
                deadline = time.monotonic() + interval * 2
                while not stop_renew.is_set():
                    try:
                        still_mine = workqueue.renew(
                            self.db_path, job.job_id, self.owner, self.config.lease_s
                        )
                    # aqg: top-level boundary
                    except Exception as exc:  # noqa: BLE001 - 一次锁冲突不该让续租线程退出  # aqg: top-level boundary
                        if time.monotonic() >= deadline:
                            logger.warning("续租连续失败（%s），本轮放弃，下个周期重试", exc)
                            break
                        stop_renew.wait(retry_delay)
                        continue
                    if not still_mine:
                        logger.warning("任务 %s 租约已被接手，当前执行继续但不再续租", job.job_id)
                        return
                    break

        renewer = threading.Thread(target=renew_loop, name="melee-lease-renew", daemon=True)
        renewer.start()

        handler = HANDLERS.get(job.kind)
        try:
            if handler is None:
                raise RuntimeError(f"未知任务类型：{job.kind}")
            handler(self.db_path, job.payload)
        # aqg: top-level boundary
        except Exception as exc:  # noqa: BLE001 - 任务失败要落库，不能杀死轮询
            self.failed += 1
            self.last_error = str(exc)
            status = workqueue.fail(self.db_path, job.job_id, str(exc), owner=self.owner)
            logger.warning("任务 %s 失败（%s）：%s", job.job_id, status, exc)
        else:
            self.processed += 1
            if not workqueue.complete(self.db_path, job.job_id, owner=self.owner):
                logger.warning("任务 %s 完成时租约已不属于本 worker，跳过状态更新", job.job_id)
        finally:
            stop_renew.set()
        return job


_QUEUE_WORKER: Worker | None = None


def ensure_inline_worker(db_path: str | Path) -> Worker | None:
    """单机模式下让 API 进程自己当 worker。

    绑定传入的库路径：换库（测试或切环境）时必须换 worker，
    否则旧 worker 会继续消费旧库，新库的任务永远没人跑。
    """
    global _QUEUE_WORKER
    config = WorkerConfig.from_env()
    if not config.enabled:
        return None
    if _QUEUE_WORKER is not None and Path(_QUEUE_WORKER.db_path) == Path(db_path):
        return _QUEUE_WORKER
    if _QUEUE_WORKER is not None:
        _QUEUE_WORKER.stop()
    _QUEUE_WORKER = Worker(db_path, config)
    _QUEUE_WORKER.start()
    return _QUEUE_WORKER


def stop_inline_worker() -> None:
    global _QUEUE_WORKER
    if _QUEUE_WORKER is not None:
        _QUEUE_WORKER.stop()
        _QUEUE_WORKER = None


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
