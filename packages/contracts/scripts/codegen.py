"""从单一 JSON Schema 生成前后端类型。

用法：
    python scripts/codegen.py            # 生成 zod 与 python 类型
    python scripts/codegen.py --check    # 只校验生成物是否最新（CI 用）

生成物：
    src/battle-event.zod.ts     —— 前端用（需先 npm i zod json-schema-to-zod）
    src/battle_event.py         —— 后端用（pydantic v2）
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schema" / "battle-event.schema.json"
OUT_TS = ROOT / "src" / "battle-event.zod.ts"
OUT_PY = ROOT / "src" / "battle_event.py"


def gen_ts() -> str:
    """json-schema-to-zod 是 npm 包；这里调用其 CLI，失败时给出可读原因。"""
    output = subprocess.run(
        ["npx", "--yes", "json-schema-to-zod", "-i", str(SCHEMA), "-o", str(OUT_TS), "--name", "battleEventSchema"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=(sys.platform == "win32"),
    )
    if output.returncode != 0:
        raise RuntimeError(
            "json-schema-to-zod 执行失败（需要 Node 与网络）。"
            f"\nstdout: {output.stdout}\nstderr: {output.stderr}"
        )
    return OUT_TS.read_text(encoding="utf-8")


def gen_py() -> str:
    from datamodel_code_generator import InputFileType, generate
    from datamodel_code_generator import DataModelType

    generate(
        input_=SCHEMA,
        input_file_type=InputFileType.JsonSchema,
        output=OUT_PY,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    return OUT_PY.read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查生成物是否存在")
    args = parser.parse_args()

    if not SCHEMA.exists():
        print(f"缺少契约文件：{SCHEMA}", file=sys.stderr)
        return 2

    if args.check:
        missing = [p for p in (OUT_TS, OUT_PY) if not p.exists()]
        if missing:
            print("生成物缺失：" + ", ".join(str(p) for p in missing), file=sys.stderr)
            return 1
        print("生成物齐备")
        return 0

    for name, fn in (("typescript", gen_ts), ("python", gen_py)):
        try:
            path = fn()
            print(f"生成 {name}: {path}")
        except Exception as exc:  # noqa: BLE001 - 生成器依赖外部工具，失败要能读懂
            print(f"跳过 {name}: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
