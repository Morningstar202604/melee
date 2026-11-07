"""Match-end webhooks: HMAC-signed POSTs with retry, dispatched off-thread.

Industry-standard shape (GitHub/Stripe style):
- header X-Melee-Signature: sha256=<hex> over "timestamp.body"
- header X-Melee-Timestamp for replay rejection by the receiver
- 2 retries with backoff (1s, 2s); outcomes are logged, observable

Receiver-side verification (documented for integrators):
    expected = sha256(secret, f"{ts}.{raw_body}")   # constant-time compare
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from .outbound import OutboundAddressError
from .settings import settings

logger = logging.getLogger("melee.webhooks")

_RETRY_DELAYS = (1.0, 2.0)


def _targets() -> list:
    """Webhook URLs from settings, SSRF-guarded by the shared outbound module.

    webhook 与 BYOK baseUrl 用同一套校验（DNS 解析 + 分类），
    而不是自写一份较弱的 loopback 名单——安全边界必须只有一处。
    """
    cfg = settings()
    raw = cfg.webhook_urls
    urls = [item.strip() for item in (raw or "").split(",") if item.strip()]
    checked = []
    for url in urls:
        try:
            from .outbound import check_base_url

            check_base_url(url)
        except OutboundAddressError as exc:
            logger.warning("webhook target %s rejected: %s", url, exc)
            continue
        checked.append(url)
    return checked


def _match_payload(db_path, match_id: str) -> dict | None:
    """终局载荷：直接读事件流，不依赖调用方传参——单一事实源是事件流。"""
    from .eventlog import read_match

    events = read_match(db_path, match_id)
    ended = [e for e in events if e.get("type") == "match.ended"]
    if not ended:
        return None
    payload = ended[-1]["payload"]
    return {
        "event": "match.ended",
        "matchId": match_id,
        "game": events[0]["payload"].get("game") if events else "",
        "winner": payload.get("winner"),
        "reason": payload.get("reason", ""),
        "eventCount": len(events),
        "at": ended[-1]["at"],
    }


def _sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _post(url: str, body: bytes, headers: dict, timeout: float) -> int:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - 目标已在 _targets() 经 check_base_url 校验
        return response.status


def _deliver(url: str, body: bytes, headers: dict, timeout: float, retries: int = 2) -> bool:
    last_error = ""
    for attempt in range(retries + 1):
        try:
            status = _post(url, body, headers, timeout)
            logger.info("webhook %s -> %s", url, status)
            return True
        except urllib.error.HTTPError as exc:
            last_error = "http %s" % exc.code
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        if attempt < len(_RETRY_DELAYS):
            time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
    logger.error("webhook %s failed after %d attempts: %s", url, retries + 1, last_error)
    return False


def dispatch(db_path, match_id: str, *, async_mode: bool = True) -> list:
    """向所有配置的 URL 分发终局通知；返回每个 URL 的结果列表。

    async_mode=True（默认）在后台线程分发，worker 不被慢接收方拖住；
    测试传 async_mode=False 同步等待，断言才可确定。
    """
    urls = _targets()
    if not urls:
        return []
    payload = _match_payload(db_path, match_id)
    if payload is None:
        return []
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cfg = settings()
    secret = cfg.webhook_secret
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Melee-Event": "match.ended",
        "X-Melee-Timestamp": timestamp,
        "User-Agent": "melee-webhook/1.8.0",
    }
    if secret:
        headers["X-Melee-Signature"] = _sign(secret, timestamp, body)
    results = []
    for url in urls:
        if async_mode:
            threading.Thread(
                target=_deliver, args=(url, body, headers, cfg.webhook_timeout), daemon=True
            ).start()
            results.append({"url": url, "dispatched": True})
        else:
            ok = _deliver(url, body, headers, cfg.webhook_timeout)
            results.append({"url": url, "delivered": ok})
    return results
