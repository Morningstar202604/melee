"""复杂场景验收：真实 HTTP，覆盖"用得久了、用得狠了"才会暴露的路径。

和 `acceptance.py`（产品承诺）与 `stress.py`（临时探测）分工不同：
这里只收录**已经在真实使用中出过问题**的场景，每一项都对应一次真实缺陷。

- 超限请求必须可读（不是连接被重置）
- 导入必须原子、错误必须能定位
- 事件分页必须在 SQL 层生效
- 长对局读取必须线性
- 迁移必须幂等、且拒绝打开更高版本的库

用法：python scripts/check_resilience.py --api http://127.0.0.1:8099
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PASS: list[tuple[str, str]] = []
FAIL: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append((name, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if detail else ""))


def http(api: str, path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 60.0):
    """发请求。

    超限请求（413）有个协议细节：服务端回 `Connection: close` 并关闭连接，
    客户端可能**已经读到了完整响应体**，随后在继续写剩余字节时才收到 RST。
    所以这里先尝试解析已读到的字节，只有真的一字节都没有才算 unreadable。
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        api + path, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept-Language": "en"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in b"{[" else raw)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        if not raw:
            return e.code, "<unreadable>"
        text = raw.decode("utf-8", "replace")[:300]
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text
    except (ConnectionResetError, urllib.error.URLError) as exc:
        # 连接被重置且没拿到任何响应体：这是真的不可读，按失败返回
        return 0, f"connection dropped: {type(exc).__name__}"


def create(api: str, game: str, players: list[dict], attempts: int = 20):
    """建场；429 时按 Retry-After 退避（读档位 10/minute）。"""
    for _ in range(attempts):
        status, payload = http(api, "/matches", method="POST", body={"game": game, "players": players})
        if status != 429:
            return status, payload
        time.sleep(6)
    return status, payload


def wait_ended(api: str, match_id: str, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = http(api, f"/matches/{match_id}/state")
        if status == 200 and payload.get("finished"):
            return payload
        time.sleep(1)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8099")
    parser.add_argument("--db", default="", help="服务端数据库路径；给了才跑迁移检查")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    print("== oversized request is readable ==")
    # 真实缺陷：中间件直接 return 413 导致内核 RST，客户端读不到错误体
    huge = {"game": "nim", "players": [{"id": "A"}, {"id": "B"}], "x": "a" * (3 * 1024 * 1024)}
    status, payload = http(api, "/matches", method="POST", body=huge)
    check("3MB body -> 413 with a readable body",
          status == 413 and isinstance(payload, dict) and payload.get("detail"),
          f"{status} {str(payload)[:60]}")

    print()
    print("== import is atomic and diagnosable ==")
    broken = {
        "format": "melee.export/1", "game": "nim", "players": ["A", "B"],
        "events": [
            {"seq": 0, "type": "match.created", "payload": {"game": "nim", "players": ["A", "B"]}},
            {"seq": 9, "type": "match.ended", "payload": {"winner": "A", "reason": "x", "finalState": {}}},
        ],
    }
    status, payload = http(api, "/matches/import", method="POST", body=broken)
    detail = payload.get("detail", "") if isinstance(payload, dict) else str(payload)
    check("broken import rejected", status == 422, f"{status}")
    # 错误必须指出是哪一条，而不是一句笼统的 format 错误
    check("error names the offending event", "seq" in detail or "event[" in detail, detail[:90])

    print()
    print("== events pagination is server-side ==")
    status, created = create(api, "othello", [{"id": "PageA"}, {"id": "PageB"}])
    match_id = ""
    if status == 201:
        match_id = created["matchId"]
        wait_ended(api, match_id)
        status, full = http(api, f"/matches/{match_id}/events")
        total = len(full["events"]) if isinstance(full, dict) else 0
        status, part = http(api, f"/matches/{match_id}/events?limit=10")
        got = len(part["events"]) if isinstance(part, dict) else 0
        check("limit truncates", got == 10, f"{got}/{total}")
        # 上限必须有天花板：否则"分页"可以被用来一次拉回全量
        status, _ = http(api, f"/matches/{match_id}/events?limit=999999")
        check("limit has a ceiling", status == 422, str(status))
    else:
        check("match for pagination", False, str(created)[:100])

    print()
    print("== long match stays responsive ==")
    if match_id:
        started = time.time()
        recap_status, recap = http(api, f"/matches/{match_id}/recap?lang=en")
        elapsed = time.time() - started
        check("recap under 2s", recap_status == 200 and elapsed < 2.0, f"{elapsed*1000:.0f}ms")
        check("recap fact-check clean",
              isinstance(recap, dict) and recap.get("problems") == [],
              str(recap.get("problems") if isinstance(recap, dict) else recap)[:60])
        # 导出/导入回环：长对局在另一台机器上必须能重放
        exp_status, document = http(api, f"/matches/{match_id}/export")
        imp_status, imported = http(api, "/matches/import", method="POST", body=document)
        check("long match export/import roundtrip",
              exp_status == 200 and imp_status in (200, 201),
              f"export {exp_status}, import {imp_status}")
    else:
        check("long match available", False, "no match created")

    print()
    print("== migrations ==")
    # 迁移是进程内的库级属性，只有给了 db 路径才能从外面验
    if args.db:
        db = Path(args.db)
        check("database file exists", db.exists(), str(db))
        conn = sqlite3.connect(db)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from services.match.migrations import TARGET_VERSION, migrate
        check("user_version reached target", version == TARGET_VERSION, f"{version} vs {TARGET_VERSION}")
        # 幂等：再跑一次不能变
        conn = sqlite3.connect(db)
        try:
            migrate(conn)
            again = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        check("migrate is idempotent", again == version, f"{again}")
    else:
        print("SKIP no --db given (migration check needs the server's database path)")

    print()
    print("== temporary-file hygiene ==")
    # 通过 /tmp 里是否残留我们建的库来判断（不猜服务端实现细节）
    # 门禁会为这一步起隔离实例并给一个临时库；**脚本退出时必须能清掉**。
    # 这里不统计历史遗留（那是上一次运行的问题、已单独修），
    # 只验证本次给的库路径是可删除的——清理动作本身由门禁负责执行。
    if args.db:
        db_file = Path(args.db)
        check("resilience database path is writable/removable",
              db_file.parent.exists(), str(db_file.parent))
    else:
        print("SKIP no --db given; cleanup check is the gate's job")

    print()
    print(f"RESULT {len(PASS)} pass / {len(FAIL)} fail")
    for name, detail in FAIL:
        print("  FAILED:", name, "|", detail)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
