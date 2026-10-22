#!/usr/bin/env python
"""生产启动入口：把 uvicorn 的代理信任设置与 MELEE_TRUSTED_PROXY_HOPS 对齐。

为什么需要它：uvicorn 默认 `proxy_headers=True` 且信任 `127.0.0.1`，
于是本机直连也能用 `X-Forwarded-For` 改写来源 IP——实测每次换一个头就能
绕过每 IP 限流。直接用 `uvicorn services.match.api:app` 启动会踩这个坑，
必须显式 `--no-proxy-headers`，或在反代后面明确可信层数。

用法：
    python scripts/serve.py                 # 默认 127.0.0.1:8099，不信任代理头
    MELEE_TRUSTED_PROXY_HOPS=1 python scripts/serve.py --host 0.0.0.0 --port 8099
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 允许从任意目录执行：把仓库根放进 sys.path，否则 `python scripts/serve.py`
# 会以 scripts/ 为起点找不到 services 包。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn


def main() -> int:
    parser = argparse.ArgumentParser(description="melee 比赛服务")
    parser.add_argument("--host", default=os.environ.get("MELEE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MELEE_PORT", "8099")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    hops = int(os.environ.get("MELEE_TRUSTED_PROXY_HOPS", "0"))
    if hops > 0 and not os.environ.get("MELEE_FORWARDED_ALLOW_IPS"):
        print("MELEE_TRUSTED_PROXY_HOPS>0 时必须同时设置 MELEE_FORWARDED_ALLOW_IPS（可信代理地址）", file=sys.stderr)
        return 2

    config = uvicorn.Config(
        "services.match.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        # 只有显式声明"在反代后面"时才让 uvicorn 改写 client。
        proxy_headers=hops > 0,
        forwarded_allow_ips=os.environ.get("MELEE_FORWARDED_ALLOW_IPS", ""),
        timeout_graceful_shutdown=10,
        access_log=True,
    )
    uvicorn.Server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
