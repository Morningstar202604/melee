#!/usr/bin/env python
"""Run the externally-observable acceptance suite against a throwaway instance.

Why a wrapper: acceptance.py needs a running service, and the gate must not
touch whatever instance the developer already has on 8099. Start an isolated
one on a free port, run the checks, shut it down.
"""

from __future__ import annotations

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


def wait_health(api: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api}/health", timeout=3) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 - startup race is expected
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{api}/health not ready in {timeout:.0f}s")


def main() -> int:
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    db_dir = tempfile.mkdtemp(prefix="melee-acceptance-")
    env = {
        **os.environ,
        "MELEE_DB": str(Path(db_dir) / "acceptance.db"),
        # 验收要连着建十几场：提高限流上限，否则 10/minute 会把脚本卡住。
        # 这是验收环境的配置，不是产品默认值。
        "MELEE_QUOTA_MATCHES": "500",
        "MELEE_RATELIMIT": "memory",
    }
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port)],
        cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_health(api)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "acceptance.py"), "--api", api],
            cwd=str(ROOT),
        )
        return result.returncode
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
