#!/usr/bin/env python
"""构建 Windows exe（onedir）：PyInstaller 打包 + 便携 zip 分发。

用法：
    .venv/Scripts/python.exe scripts/build_exe.py

产物：
    dist/melee/melee.exe          # CLI + 服务一体的可执行文件
    dist/melee-portable-win64.zip # 安装即用：解压、双击 start.bat
    dist/INSTALL.txt              # 用户拿到的第一份说明
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD_DIR = ROOT / "build"

APP_VERSION = "1.8.0"

README_SHORT = r"""\
melee broadcast studio — portable distribution
================================================

Quick start
-----------
1. Double-click  start.bat   (runs: melee.exe serve --port 8099)
2. Open  http://127.0.0.1:8099/
3. CLI:  melee.exe --help

Requirements
------------
- Windows 10/11 x64.
- Highlight mp4 rendering needs ffmpeg.exe next to melee.exe (or on PATH):
  download from https://www.gyan.dev/ffmpeg/builds/ (essentials build).
- Data is stored in .\data\ next to the exe; delete it to reset.
"""

INSTALL_TXT = r"""\
melee {version} — Windows portable build ({date})
===================================================

Contents
--------
  melee.exe        one binary = CLI + web service + admin backend
  apps/web/        the five broadcast pages (no build step, no node_modules)
  start.bat        double-click to serve on http://127.0.0.1:8099
  INSTALL.txt      this file
  LICENSE, NOTICE  licenses & third-party notices

Install
-------
  1. Unzip anywhere (no admin rights needed, no registry writes).
   single-file note: highlight mp4 rendering needs ffmpeg.exe next to melee.exe
     (optional, download: https://www.gyan.dev/ffmpeg/builds/).
  2. Double-click start.bat.
  3. Open http://127.0.0.1:8099/

CLI examples
------------
  melee.exe games
  melee.exe match create --game tic-tac-toe --seed 7 --player A --player B --open
  melee.exe match events <matchId> --follow
  melee.exe season-run <seasonId>
  melee.exe serve --port 8099 --db data\match.db
"""

START_BAT = """\
@echo off
rem melee serve launcher: the exe defaults to 127.0.0.1:8099.
cd /d "%~dp0"
melee.exe serve --port 8099
"""


def main() -> int:
    import PyInstaller.__main__

    spec = Path(__file__).resolve().parent / "melee.spec"
    PyInstaller.__main__.run(
        [
            str(spec),
            "--distpath", str(DIST),
            "--workpath", str(BUILD_DIR),
            "--noconfirm",
        ]
    )

    out = DIST / "melee"
    if not (out / "melee.exe").exists():
        print("FAIL: melee.exe missing after build", file=sys.stderr)
        return 1

    # 分发目录里带前端静态文件副本已在 _internal/apps/web（spec datas）。
    start_bat = out / "start.bat"
    start_bat.write_text(START_BAT, encoding="utf-8")
    (out / "INSTALL.txt").write_text(
        INSTALL_TXT.format(version=APP_VERSION, date=__import__("datetime").date.today().isoformat()),
        encoding="utf-8",
    )
    (out / "README.txt").write_text(README_SHORT, encoding="utf-8")

    zip_path = DIST / "melee-portable-win64.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(out.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(out.parent))

    size_mb = zip_path.stat().st_size / 1_000_000
    print(f"OK: {zip_path.name} ({size_mb:.0f} MB) from {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
