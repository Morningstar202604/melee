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
    ("POST", "/matches/import-episode"),
    ("POST", "/matches/import-episode/live"),
    ("POST", "/auth/ticket"),
    ("GET", "/embed/{match_id}"),
    ("GET", "/matches/{match_id}/scene"),
    ("GET", "/director/matches"),
    ("GET", "/director/{match_id}"),
    ("POST", "/director/{match_id}/cue"),
    ("GET", "/games"),
    ("GET", "/health"),
    ("GET", "/jobs"),
    ("GET", "/jobs/{job_id}"),
    ("GET", "/matches"),
    ("POST", "/matches"),
    ("POST", "/matches/{match_id}/coach"),
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
    # 1.9.2：webhook 投递日志与手动重发。读记录需要鉴权（含部署基础设施信息）。
    ("GET", "/webhooks/deliveries"),
    ("POST", "/webhooks/redispatch"),
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
    """版本号只允许一个字面量：`services/match/version.py`。

    API、CLI、A2A 卡与两个打包脚本都引用它；前端 package.json 由发布
    流程同步。以前每处各写一份，A2A 卡与安装器已经分别落后到 1.8.0 / 1.9.2。
    """
    import json
    import re
    from pathlib import Path

    from services.match.version import APP_VERSION

    root = Path(__file__).resolve().parents[3]
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    expected = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE).group(1)

    assert APP_VERSION == expected, f"version.py={APP_VERSION} 与 CHANGELOG 顶部 {expected} 不一致"
    web = json.loads((root / "apps" / "web" / "package.json").read_text(encoding="utf-8"))
    assert web["version"] == expected, f"package.json={web['version']} 与 CHANGELOG 顶部 {expected} 不一致"

    for rel in ("scripts/build_exe.py", "scripts/build_installer.py"):
        source = (root / rel).read_text(encoding="utf-8")
        assert "from _version import read_version" in source, f"{rel} 没有走共享版本读取器"
        assert not re.search(r'^APP_VERSION\s*=\s*"', source, re.MULTILINE), f"{rel} 仍在硬编码版本号"
    reader = (root / "scripts" / "_version.py").read_text(encoding="utf-8")
    assert "version.py" in reader, "构建脚本的版本读取器没有指向单一版本源"

    a2a = (root / "services" / "match" / "a2a.py").read_text(encoding="utf-8")
    assert "from .version import APP_VERSION" in a2a, "a2a.py 没有引用单一版本源"
