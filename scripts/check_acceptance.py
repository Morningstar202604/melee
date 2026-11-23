#!/usr/bin/env python
"""Run the externally-observable acceptance suite against a throwaway instance.

Why a wrapper: acceptance.py needs a running service, and the gate must not
touch whatever instance the developer already has on 8099. Start an isolated
one on a free port, run the checks, shut it down.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from _harness import free_port, start_server, stop_process, wait_health  # noqa: E402


def main() -> int:
    port = free_port()
    api = f"http://127.0.0.1:{port}"
    db_dir = tempfile.mkdtemp(prefix="melee-acceptance-")
    # 验收要连着建十几场：提高限流上限，否则 10/minute 会把脚本卡住。
    # 这是验收环境的配置，不是产品默认值。
    process = start_server(
        port,
        str(Path(db_dir) / "acceptance.db"),
        extra_env={"MELEE_QUOTA_MATCHES": "500", "MELEE_RATELIMIT": "memory"},
    )
    try:
        wait_health(api)
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "acceptance.py"), "--api", api],
            cwd=str(ROOT),
        )
        return result.returncode
    finally:
        stop_process(process, timeout=15)


if __name__ == "__main__":
    sys.exit(main())
