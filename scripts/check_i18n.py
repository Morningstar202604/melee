#!/usr/bin/env python
"""双语界面门禁：起一个隔离服务、跑一场真实比赛、巡检两种语言的每个页面。

为什么是独立脚本而不是塞进 verify_all.sh：
- 需要"起服务 → 建场 → 等终局 → 巡检 → 收尾"这一串步骤，写成 shell 会嵌套
  heredoc 与引号，实测把门禁脚本本身写坏过一次；
- Python 里用 try/finally 收尾比 shell 的 trap 更不容易漏掉进程。

用法：
    python scripts/check_i18n.py            # 门禁模式，失败返回非零
    python scripts/check_i18n.py --api http://127.0.0.1:8099   # 对着已有服务跑
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(api: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 - 启动期连不上是正常的
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{api}/health 在 {timeout:.0f}s 内没有就绪")


def create_match(api: str) -> str:
    """造一场能跑完的比赛：页面没有对局数据时，巡检看不到任何文案。"""
    body = {"game": "tic-tac-toe", "seed": 7, "players": [{"id": "A"}, {"id": "B"}]}
    request = urllib.request.Request(
        f"{api}/matches",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        match_id = json.loads(response.read().decode("utf-8"))["matchId"]

    deadline = time.time() + 120
    while time.time() < deadline:
        with urllib.request.urlopen(f"{api}/matches/{match_id}/events", timeout=20) as response:
            events = json.loads(response.read().decode("utf-8"))["events"]
        if any(event["type"] == "match.ended" for event in events):
            return match_id
        time.sleep(1)
    raise RuntimeError(f"比赛 {match_id} 在 120s 内没有结束")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="", help="已有服务地址；留空则自己起一个隔离实例")
    parser.add_argument("--out", default="", help="截图输出目录")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="melee-i18n-"))
    out_dir.mkdir(parents=True, exist_ok=True)

    process: subprocess.Popen[bytes] | None = None
    try:
        if args.api:
            api = args.api.rstrip("/")
        else:
            port = free_port()
            api = f"http://127.0.0.1:{port}"
            db_path = Path(tempfile.mkdtemp(prefix="melee-i18n-db-")) / "i18n.db"
            env = {
                **os.environ,
                "MELEE_DB": str(db_path),
                "MELEE_QUOTA_MATCHES": "200",
                "MELEE_PORT": str(port),
            }
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port)],
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            wait_health(api)

        match_id = create_match(api)
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "audit_i18n.py"),
                "--api", api,
                "--out", str(out_dir),
                "--match", match_id,
            ],
            cwd=str(ROOT),
        )
        return result.returncode
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
