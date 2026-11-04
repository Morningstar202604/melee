"""许可证门禁的回归锁：NOTICE 必须与已安装依赖一致，且禁止 GPL 进入运行时。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_licenses.py"


def test_notice_matches_installed_dependencies():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_license_gate_rejects_gpl_license():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import check_licenses

        for bad in ("GPL-3.0", "AGPL-3.0-only", "LGPL-2.1"):
            assert any(token in check_licenses.normalize(bad).upper() for token in check_licenses.FORBIDDEN)
        for good in ("MIT", "BSD-3-Clause", "Apache-2.0"):
            assert not any(token in check_licenses.normalize(good).upper() for token in check_licenses.FORBIDDEN)
    finally:
        sys.path.pop(0)


def test_notice_covers_every_requirement():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import check_licenses

        rows = check_licenses.notice_versions()
        missing = [pkg for pkg in check_licenses.parse_requirements() if pkg.lower().replace("_", "-") not in rows]
        assert not missing, f"NOTICE 缺行：{missing}"
    finally:
        sys.path.pop(0)
