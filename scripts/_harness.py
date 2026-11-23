"""演示与门禁脚本共用的最小工具：端口、健康等待、JSON 客户端、浏览器。

七个脚本各自抄过这些代码；超时口径分开后，同一个服务在 check_i18n
里 60 秒算超时、在 check_acceptance 里 90 秒，很难解释。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def free_port() -> int:
    """让内核分配一个当前空闲的本地端口。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(api: str, timeout: float = 90.0, probe_timeout: float = 3.0) -> None:
    """轮询 /health 直到 200；启动期连不上是预期内的。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{api}/health", timeout=probe_timeout) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001 - startup race is expected
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{api}/health not ready in {timeout:.0f}s")


def get_json(url: str, timeout: float = 15.0) -> dict:
    """读一个 JSON 端点；截图脚本与巡检脚本共用同一超时。"""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def launch_browser(playwright):
    """优先 Playwright 自带内核；开发机上回退系统 Chromium / Chrome / Edge。

    `playwright install` 只在 CI 里必跑；开发机常常已经装了系统浏览器。
    以前 Linux 上直接抛 "Executable doesn't exist"，截图和双语门禁就变成
    只能看不能跑的脚本。
    """
    try:
        return playwright.chromium.launch()
    except Exception:
        candidates = (
            "/usr/local/bin/chromium",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/opt/google/chrome/chrome",
            "/opt/browser/chrome",
        )
        for path in candidates:
            if Path(path).exists():
                return playwright.chromium.launch(executable_path=path)
        if sys.platform == "win32":
            return playwright.chromium.launch(channel="msedge")
        raise


def api_get(api: str, path: str, *, lang: str = "", timeout: float = 30.0) -> dict:
    """读一个 API 端点；带 lang 时把 Accept-Language 一起发过去。"""
    headers = {"Accept-Language": lang} if lang else {}
    request = urllib.request.Request(f"{api}{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def api_post(
    api: str,
    path: str,
    body: dict,
    *,
    attempts: int = 8,
    timeout: float = 30.0,
    on_retry=None,
    lang: str = "",
) -> dict:
    """POST JSON；撞上限流时按 Retry-After 退避，而不是让脚本第 11 场就死。

    建场请求的 Accept-Language 决定比赛内部词表与解说语言；脚本切了
    选手名却不传 lang，就会出现「中文界面 + 英文题面」的半本地化。
    """
    headers = {"Content-Type": "application/json"}
    if lang:
        headers["Accept-Language"] = lang
    request = urllib.request.Request(
        f"{api}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            wait = min(int(exc.headers.get("Retry-After") or 60), 65)
            if on_retry:
                on_retry(attempt + 1, wait)
            time.sleep(wait)
    raise RuntimeError("retries exhausted")


def wait_finished(
    api: str,
    match_id: str,
    timeout: float = 120.0,
    *,
    raise_on_timeout: bool = True,
) -> bool:
    """轮询事件日志直到 match.ended；批量造数据时可选择超时返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = api_get(api, f"/matches/{match_id}/events")["events"]
        if any(event["type"] == "match.ended" for event in events):
            return True
        time.sleep(0.5)
    if raise_on_timeout:
        raise TimeoutError(f"{match_id} did not finish in {timeout:.0f}s")
    return False


ROOT = Path(__file__).resolve().parents[1]


def start_server(port: int, db_path: str, *, extra_env: dict[str, str] | None = None) -> subprocess.Popen:
    """起一个隔离的 serve.py；调用方负责 wait_health 与 stop_process。"""
    env = {
        **os.environ,
        "MELEE_DB": str(db_path),
        "MELEE_PORT": str(port),
        **(extra_env or {}),
    }
    return subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen, timeout: float = 10.0) -> None:
    """先礼后兵：terminate，超时才 kill。"""
    process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()


def report(passed: list, failed: list) -> int:
    """统一 PASS/FAIL 尾部；返回进程退出码。"""
    print()
    print(f"RESULT {len(passed)} pass / {len(failed)} fail")
    for name, detail in failed:
        print("  FAILED:", name, "|", detail)
    return 1 if failed else 0
