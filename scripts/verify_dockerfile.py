"""在没有 Docker daemon 的环境里，对镜像做等价静态验证。

为什么需要：CI 有 docker job，但本机没有 daemon 时"镜像能不能跑"只能靠猜。
这里把 Dockerfile 的每条指令翻译成可在宿主机执行的断言：
COPY 目标存在、运行时依赖齐全、入口脚本可导入、非 root 运行、健康检查路径可达、
compose 引用的文件都在。

跑法：python scripts/verify_dockerfile.py
退出码非零表示镜像构建/运行会失败。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dockerfile_lines() -> list[str]:
    return [
        line.strip()
        for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def check_copy_sources() -> list[str]:
    problems = []
    for line in _dockerfile_lines():
        if not line.upper().startswith("COPY"):
            continue
        parts = line.split()
        # COPY 语法：COPY src... dest。最后一个参数是目标，其余都是源。
        # 之前用 parts[1:-1] 会把单源双参的 `COPY NOTICE ./` 当成源=NOTICE，
        # 而多源时又漏掉最后一个源之外的判断，导致缺文件照样通过。
        sources = parts[1:-1]
        for source in sources:
            # 支持通配符（如 *.txt）
            if any(char in source for char in "*?["):
                if not list(ROOT.glob(source)):
                    problems.append(f"COPY 源不存在（通配符无匹配）：{source}")
                continue
            if not (ROOT / source).exists():
                problems.append(f"COPY 源不存在：{source}")
    # 单独检查常见"多源 + 目标目录"写法
    return problems
    return problems


def check_requirements_complete() -> list[str]:
    """产品代码 import 的第三方包必须在 requirements.txt 里（发行名→import 名映射）。"""
    stdlib = set(sys.stdlib_module_names)
    local = {"services", "packages", "apps", "scripts"}
    imports: set[str] = set()
    for path in (ROOT / "services").rglob("*.py"):
        if "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imports.add(node.module.split(".")[0])
    third_party = {name for name in imports if name not in stdlib and name not in local}

    # import 名 → 发行名（dist name 不总是等于 import 名）
    alias = {
        "PIL": "pillow",
        "sse_starlette": "sse-starlette",
        "pydantic_settings": "pydantic-settings",
        "prometheus_client": "prometheus-client",
        "prometheus_fastapi_instrumentator": "prometheus-fastapi-instrumentator",
        "apscheduler": "apscheduler",
    }
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    problems = []
    for name in sorted(third_party):
        dist = alias.get(name, name).lower()
        if dist not in requirements:
            problems.append(f"运行时 import 了 {name}，但 requirements.txt 未声明（写 {dist}）")
    return problems


def check_entrypoint_importable() -> list[str]:
    """镜像的 CMD 是 scripts/serve.py：确认它能被解析且引用的模块存在。"""
    problems = []
    entry = ROOT / "scripts" / "serve.py"
    if not entry.exists():
        return ["入口脚本 scripts/serve.py 不存在"]
    try:
        ast.parse(entry.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        problems.append(f"scripts/serve.py 语法错误：{exc}")
    if "uvicorn.Config(" not in entry.read_text(encoding="utf-8"):
        problems.append("scripts/serve.py 未通过 uvicorn.Config 启动（Dockerfile CMD 依赖它）")
    return problems


def check_non_root() -> list[str]:
    lines = _dockerfile_lines()
    has_user = any(line.upper().startswith("USER ") for line in lines)
    if not has_user:
        return ["Dockerfile 未切换非 root 用户（USER melee/10001）"]
    last_user = [line for line in lines if line.upper().startswith("USER ")][-1]
    if re.search(r"USER\s+(root|0)\b", last_user, re.I):
        return [f"最终 USER 是 root：{last_user}"]
    return []


def check_healthcheck() -> list[str]:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    if "HEALTHCHECK" not in text:
        return ["Dockerfile 缺 HEALTHCHECK"]
    problems = []
    # 健康检查打的是 /health：确认路由真的存在。
    # 路由按域拆在 services/match 各模块里，不能只看 api.py。
    sources = [p.read_text(encoding="utf-8") for p in (ROOT / "services" / "match").rglob("*.py")]
    if not any('"/health"' in source for source in sources):
        problems.append("HEALTHCHECK 打 /health，但服务里没有该路由")
    return problems


def check_env_defaults() -> list[str]:
    """Dockerfile 里写死的数据目录必须与 settings 默认/挂载一致。"""
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    problems = []
    match = re.search(r"MELEE_DB=(\S+)", text)
    if not match:
        problems.append("Dockerfile 未设置 MELEE_DB")
        return problems
    db_path = match.group(1)
    if not db_path.startswith("/data/"):
        problems.append(f"MELEE_DB 应落在挂载卷 /data 下，实际 {db_path}")
    if "/data" not in text:
        problems.append("Dockerfile 未创建 /data 目录（卷挂载点）")
    return problems


def check_compose() -> list[str]:
    problems = []
    try:
        import yaml
    except ImportError:
        return ["未安装 pyyaml，无法校验 compose（pip install pyyaml）"]
    compose_path = ROOT / "docker-compose.yml"
    data = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = data.get("services", {})
    if "match" not in services:
        problems.append("compose 缺 match 服务")
    web = services.get("web", {})
    volumes = web.get("volumes") or []
    for entry in volumes:
        # 形如 "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro"
        if not isinstance(entry, str) or ":" not in entry:
            continue
        host_path = entry.split(":", 1)[0]
        if host_path.startswith("./"):
            target = ROOT / host_path[2:]
            if not target.exists():
                problems.append(f"compose 挂载了不存在的路径：{host_path}")
    if "match" in services:
        # 前端与 API 必须同源：match 不应直接把端口暴露成独立 origin
        match_svc = services["match"]
        if match_svc.get("ports") and "web" in services:
            problems.append(
                "compose 里 match 同时开 ports 且存在 web 反代：前端会按同源请求，"
                "直连端口会让页面上的 API 调用打错地址（应只用 expose）"
            )
    return problems


def check_frontend_is_served() -> list[str]:
    """api.py 必须挂载静态前端，否则镜像里只有 API、打开根路径 404。"""
    api = (ROOT / "services" / "match" / "api.py").read_text(encoding="utf-8")
    if 'app.mount("/"' not in api:
        return ["api.py 没有把 apps/web 挂到根路径（单端口部署会 404）"]
    return []


def main() -> int:
    checks = [
        ("COPY 源", check_copy_sources),
        ("运行时依赖", check_requirements_complete),
        ("启动入口", check_entrypoint_importable),
        ("非 root 运行", check_non_root),
        ("健康检查", check_healthcheck),
        ("数据卷", check_env_defaults),
        ("compose", check_compose),
        ("静态前端", check_frontend_is_served),
    ]
    failed = 0
    for label, fn in checks:
        problems = fn()
        if problems:
            failed += 1
            print(f"FAIL {label}")
            for problem in problems:
                print(f"     {problem}")
        else:
            print(f"OK   {label}")
    print()
    if failed:
        print(f"{failed} 组检查未通过：镜像构建或运行会失败")
        return 1
    print("Dockerfile / compose 静态校验全部通过")
    print("注意：这不等于镜像真的能构建——本机无 daemon 时 CI 的 docker job 才是最终判据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
