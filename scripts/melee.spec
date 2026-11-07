# -*- mode: python ; coding: utf-8 -*-
# melee 的 PyInstaller 打包配置。构建：python scripts/build_exe.py
#
# 为什么 onedir 而不是 onefile：onefile 每次启动解压到临时目录，
# 服务型进程要常驻，启动快与防误删 _internal 都更重要。

# mypyc 化的库（openskill 等）带哈希前缀的编译模块，PyInstaller 静态分析
# 看不到它们的 import，必须在构建时按文件系统形态点名。
# 模块名必须剥掉扩展 ABI 后缀（import 系统自己按 EXTENSION_SUFFIXES 搜索）。
import glob as _glob
import os as _os

import openskill as _openskill

# pathex 必须基于 spec 文件定位，而不是调用时的工作目录：
# 从仓库根跑 build_exe.py 时 ".." 会解析到仓库的上级，
# services 包整包漏打，exe 启动即 ModuleNotFoundError（实测发生过）。
# SPECPATH 由 PyInstaller 注入，恒为 spec 文件所在目录。
SPEC_DIR = _os.path.abspath(SPECPATH)
ROOT_DIR = _os.path.dirname(SPEC_DIR)

# TextArena 的 env 是运行时动态装配的（ta.make 按 env_id 找入口），
# 静态分析只会带顶层包；必须显式收集全部子模块，否则 exe 里
# 建场成功、worker 却 ImportError。纯 Python，体积代价小。
from PyInstaller.utils.hooks import collect_submodules as _collect_submodules

textarena_hidden = _collect_submodules("textarena")

_sp = _os.path.dirname(_os.path.dirname(_openskill.__file__))
mypyc_hidden = []
for _pyd in _glob.glob(_os.path.join(_sp, "*__mypyc*.pyd")):
    _base = _os.path.basename(_pyd)
    mypyc_hidden.append(_base.split(".cp")[0])

a = Analysis(
    [_os.path.join(SPEC_DIR, "melee_entry.py")],
    pathex=[ROOT_DIR],
    binaries=[],
    datas=[
        (_os.path.join(ROOT_DIR, "apps", "web"), "apps/web"),
        (_os.path.join(ROOT_DIR, "packages", "contracts", "schema", "battle-event.schema.json"), "packages/contracts/schema"),
        (_os.path.join(ROOT_DIR, "LICENSE"), "LICENSE"),
        (_os.path.join(ROOT_DIR, "NOTICE"), "NOTICE"),
    ],
    hiddenimports=mypyc_hidden + textarena_hidden + [
        # uvicorn 按需动态导入的三个装配点：不点名的 PyInstaller 会漏。
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "services.match.cli",
        "services.match.api",
        "services.match.worker",
        "services.match.scheduler",
        "services.match.messages",
        "services.match.engine.textarena_probe",
        "services.match.mcp_server",
    ],
    excludes=[
        # 开发工具不进产品包； fakeredis 只在 Redis 模式延迟导入，排除后
        # 选用 Redis 时需自带，见 DEPLOY.md。
        "tkinter", "matplotlib", "pytest", "fakeredis", "PyInstaller",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="melee",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="melee",
)
