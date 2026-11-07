"""比赛服务的 HTTP 组合根：中间件、生命周期、路由挂载。

路由实现按业务域拆在 `routes/` 下：
- `matches.py`   建场 / 事件 / 回放 / 战报 / 赛事库 / 统计 / SSE
- `season_routes.py` 赛季
- `media.py`     剪辑 / 分享卡 / 媒体票据
- `director.py`  导播台
- `share.py`     分享落地页
- `system.py`    健康探针 / 赛制清单 / 任务 / 调度

这里只保留跨域的东西：鉴权中间件、安全响应头、CORS、生命周期。
任何一个端点都不应该在这个文件里实现。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthError, authenticate
from .observability import install_metrics
from .routes import ROUTERS
from .routes.common import auth_config, visible
from .scheduler import start_from_env, stop_current
from .settings import settings
from .worker import ensure_inline_worker, stop_inline_worker

logger = logging.getLogger("melee.match")

_SENSITIVE_FIELDS = {"apikey", "api_key", "authorization", "secret", "password", "token", "credential"}


def _safe_validation_value(value: Any, *, field: str = "") -> Any:
    field_name = field.lower()
    if any(marker in field_name for marker in _SENSITIVE_FIELDS):
        return "[redacted]"
    if isinstance(value, dict):
        return {key: _safe_validation_value(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_validation_value(item, field=field) for item in value]
    if isinstance(value, tuple):
        return tuple(_safe_validation_value(item, field=field) for item in value)
    if isinstance(value, str):
        if "@" in value or "?" in value or "#" in value:
            from urllib.parse import urlsplit

            try:
                parsed = urlsplit(value)
            except ValueError:
                return "[redacted]"
            if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
                return "[redacted]"
    return value


def _safe_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for error in exc.errors():
        item = dict(error)
        loc = tuple(item.get("loc") or ())
        field = str(loc[-1]) if loc else ""
        if any(marker in field.lower() for marker in _SENSITIVE_FIELDS):
            item["input"] = "[redacted]"
        elif "baseurl" in field.lower() or "endpoint" in field.lower():
            item["input"] = _safe_validation_value(item.get("input"), field=field)
        elif "input" in item:
            item["input"] = _safe_validation_value(item["input"], field=field)
        # Pydantic v2 stores the original ValueError in ctx.error; returning it
        # directly makes JSON serialization fail and may expose validator input.
        item.pop("ctx", None)
        item["msg"] = _safe_validation_value(item.get("msg"), field=field)
        safe.append(item)
    return safe


def _validation_error_response(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _safe_validation_errors(exc)})


# 兼容旧调用点：DB_PATH 由 create_app 决定，模块级默认值来自配置。
DB_PATH = Path(settings().db)

# 事件可见性过滤对外仍以 api.visible 暴露（测试直接引用）。
__all__ = ["DB_PATH", "create_app", "app", "visible"]


def _apply_security_headers(app: FastAPI) -> None:
    origins = settings().origin_list

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Any:
        """安全响应头 + 请求体上限。

        没有这些头时，任何同源注入都能被浏览器当作脚本执行；
        没有体积上限时，一个 2.2MB 的 script 列表会被原样接收并落库。
        """
        limit = settings().max_body_bytes
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > limit:
            return JSONResponse(status_code=413, content={"detail": f"请求体超过 {limit} 字节上限"})
        response = await call_next(request)
        # 限流头由 guard() 挂在 request.state 上，这里统一落到响应：
        # 逐个端点写会漏（33 个端点，新增一个就漏一个）。
        for name, value in getattr(request.state, "rate_limit", {}).items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        # 前端是零构建的原生 ES module，没有内联脚本；CSP 只放行同源资源与内联样式。
        # connect-src 跟随 CORS 配置：分端口部署时前端要能直连 API。
        connect = " ".join(["'self'", *[origin for origin in origins if origin != "*"]])
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; "
            f"connect-src {connect}; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if auth_config().mode != "off":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _apply_metrics_auth(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard_metrics(request: Request, call_next: Any) -> Any:
        """指标端点在 token 模式下同样要鉴权：请求量与错误率也是情报。"""
        if request.url.path in {"/metrics", "/metrics/business"} and auth_config().mode != "off":
            try:
                authenticate(auth_config(), request.headers.get("authorization"))
            except AuthError as exc:
                return JSONResponse(status_code=exc.status, content={"detail": str(exc)})
        return await call_next(request)


def create_app(db_path: Path | None = None) -> FastAPI:
    """构造 HTTP 应用。`db_path` 默认沿用模块级 `DB_PATH`（测试会 patch 它）。"""
    resolved = Path(db_path) if db_path is not None else Path(DB_PATH)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        start_from_env(resolved)
        if settings().inline_worker:
            ensure_inline_worker(resolved)
        try:
            yield
        finally:
            stop_inline_worker()
            stop_current()

    app = FastAPI(
        title="melee match service",
        version="1.9.0",
        lifespan=lifespan,
        # token 模式下不公开接口表：/docs 与 /openapi.json 之前无需鉴权即可读。
        docs_url=None if auth_config().mode != "off" else "/docs",
        redoc_url=None if auth_config().mode != "off" else "/redoc",
        openapi_url=None if auth_config().mode != "off" else "/openapi.json",
    )
    app.add_exception_handler(RequestValidationError, _validation_error_response)
    app.state.db_path = resolved

    # 观众端与比赛服务分开部署（静态站点 + API），本地开发跨端口。
    # 上线时用 MELEE_CORS_ORIGINS 收窄；默认 * 只适合本机演示。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings().origin_list or ["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    _apply_security_headers(app)
    install_metrics(app, resolved)
    _apply_metrics_auth(app)

    for router in ROUTERS:
        app.include_router(router)

    # 静态前端挂到根路径：单端口即可打开观战页，不用再起第二个 http.server。
    # 必须放在所有 API 路由之后，否则 "/" 的挂载会吞掉它们。
    web_dir = Path(__file__).resolve().parents[2] / "apps" / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app


app = create_app()
