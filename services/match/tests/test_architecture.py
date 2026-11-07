"""架构守卫：把"可维护性"变成会被 CI 抓住的断言。

三个不可回退的结构约束：
1. 事件类型只有一个来源（JSON Schema），代码里不再手抄。
2. 每个事件日志实例有自己的写锁，不共享类级锁。
3. HTTP 路由清单是产品契约：拆分实现可以，路径与方法不能悄悄增删。
"""

from __future__ import annotations

import threading

from services.match import contract, eventlog

EXPECTED_ROUTES = {
    ("GET", "/.well-known/agent-card.json"),
    ("GET", "/archive"),
    ("GET", "/matches/{match_id}/export"),
    ("POST", "/matches/import"),
    ("POST", "/auth/ticket"),
    ("GET", "/director/matches"),
    ("GET", "/director/{match_id}"),
    ("POST", "/director/{match_id}/cue"),
    ("GET", "/games"),
    ("GET", "/health"),
    ("GET", "/jobs"),
    ("GET", "/jobs/{job_id}"),
    ("GET", "/matches"),
    ("POST", "/matches"),
    ("GET", "/matches/{match_id}/card.png"),
    ("GET", "/matches/{match_id}/clips"),
    ("POST", "/matches/{match_id}/clips/render"),
    ("GET", "/matches/{match_id}/clips/{filename}"),
    ("GET", "/matches/{match_id}/events"),
    ("POST", "/matches/{match_id}/media-ticket"),
    ("GET", "/matches/{match_id}/presence"),
    ("GET", "/matches/{match_id}/recap"),
    ("GET", "/matches/{match_id}/state"),
    ("GET", "/matches/{match_id}/stream"),
    ("GET", "/matches/{match_id}/view"),
    ("GET", "/matches/{match_id}/view/as/{player}"),
    ("GET", "/ops/health"),
    ("GET", "/ops/metrics"),
    ("GET", "/quota"),
    ("GET", "/scheduler"),
    ("POST", "/scheduler/tick"),
    ("GET", "/seasons"),
    ("POST", "/seasons"),
    ("GET", "/seasons/{season_id}"),
    ("POST", "/seasons/{season_id}/run"),
    ("GET", "/share/{match_id}"),
    ("GET", "/stats"),
}


def test_event_types_come_from_schema():
    """eventlog 的事件类型集合必须与 schema 完全一致，不能各写一份。"""
    assert eventlog.EVENT_TYPES == contract.event_types()


def test_eventlog_instances_do_not_share_a_lock(tmp_path):
    """类级可变默认值会让所有比赛共用一把锁；必须每个实例一把。"""
    first = eventlog.EventLog.open(tmp_path / "lock.db", "m1")
    second = eventlog.EventLog.open(tmp_path / "lock.db", "m2")
    try:
        assert isinstance(first.lock, type(threading.Lock()))
        assert first.lock is not second.lock
    finally:
        first.close()
        second.close()


def test_route_surface_is_frozen():
    """单端口部署的路径清单是契约：重构内部实现不能改变对外接口。

    直接读各域路由的声明表：FastAPI 的 app.routes 在较新版本里是惰性的
    `_IncludedRouter` 包装，读不到最终路径。
    """
    from services.match.routes import ROUTERS

    actual = {
        (method, route.path)
        for router in ROUTERS
        for route in router.routes
        if getattr(route, "methods", None)
        for method in route.methods
        if method in {"GET", "POST"}
    }
    assert EXPECTED_ROUTES == actual, {
        "missing": sorted(EXPECTED_ROUTES - actual),
        "unexpected": sorted(actual - EXPECTED_ROUTES),
    }


def test_version_matches_changelog_head():
    """版本号只有一个真相来源：changelog 顶部条目必须等于 FastAPI 的 version。

    发布流程要求"改代码、写 changelog、升版本"三件事一起做；
    漏掉任何一步都会让 /openapi.json 报的版本和实际发布的版本对不上。
    """
    import re
    from pathlib import Path

    from services.match.api import create_app

    changelog = Path(__file__).resolve().parents[3] / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    match = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)
    assert match, "CHANGELOG.md 缺少形如 ## [1.2.3] 的版本标题"

    app = create_app()
    assert app.version == match.group(1), (
        f"FastAPI version={app.version!r} 与 CHANGELOG 顶部 {match.group(1)!r} 不一致；"
        "升版本时两处必须同时改"
    )


def test_every_version_carrier_matches_the_changelog():
    """四处版本号必须一致：API、CLI、打包脚本、前端 package.json。

    以前只校验 API 与 CHANGELOG，另外三处靠人记——`melee --version` 与
    便携包内嵌版本因此可能悄悄落后于实际发布版本，用户看到的版本号是假的。
    """
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    expected = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE).group(1)

    cli = (root / "services" / "match" / "cli.py").read_text(encoding="utf-8")
    build = (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")
    web = json.loads((root / "apps" / "web" / "package.json").read_text(encoding="utf-8"))

    carriers = {
        "cli.py APP_VERSION": re.search(r'APP_VERSION = "(\d+\.\d+\.\d+)"', cli).group(1),
        "build_exe.py APP_VERSION": re.search(r'APP_VERSION = "(\d+\.\d+\.\d+)"', build).group(1),
        "apps/web/package.json": web["version"],
    }
    for name, value in carriers.items():
        assert value == expected, f"{name}={value} 与 CHANGELOG 顶部 {expected} 不一致"
