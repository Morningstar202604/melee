"""从单一 JSON Schema 生成前后端类型。

用法：
    python scripts/codegen.py            # 生成 zod 与 python 类型
    python scripts/codegen.py --check    # 校验生成物与 schema 同步（CI 用）

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
    from datamodel_code_generator import DataModelType, InputFileType, generate

    generate(
        input_=SCHEMA,
        input_file_type=InputFileType.JsonSchema,
        output=OUT_PY,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    return OUT_PY.read_text(encoding="utf-8")


def _schema_fingerprint() -> str:
    """schema 的内容指纹，用于 --check 判定生成物是否过期。"""
    import hashlib

    return hashlib.sha256(SCHEMA.read_bytes()).hexdigest()[:16]


def _schema_event_types() -> set[str]:
    import json

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return {branch["allOf"][1]["properties"]["type"]["const"] for branch in schema["oneOf"]}


def _schema_action_kinds() -> set[str]:
    """schema 里 action.kind 的枚举（proposed 与 applied 两处必须一致）。"""
    import json

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    kinds: set[str] = set()
    for branch in schema["oneOf"]:
        props = branch["allOf"][1]["properties"]
        if props["type"]["const"] not in ("action.proposed", "action.applied"):
            continue
        enum = props["payload"]["properties"]["action"]["properties"]["kind"]["enum"]
        if kinds and set(enum) != kinds:
            raise ValueError(f"action.proposed 与 action.applied 的 kind 枚举不一致：{kinds} vs {set(enum)}")
        kinds = set(enum)
    return kinds


def check_generated() -> list[str]:
    """真正的漂移检测：事件类型与 action.kind 枚举必须三处一致。

    之前 `--check` 只判断文件存在——schema 改了、生成物没跟上，门禁照样全绿。
    实测就漏了 `forfeit`：schema 只允许 ask/guess，runner 弃权时写 forfeit，
    两边漂了很久才被发现。这里逐项比对，不再看文件在不在。
    """
    problems: list[str] = []
    for path in (OUT_TS, OUT_PY):
        if not path.exists():
            problems.append(f"生成物缺失：{path}")
    if problems:
        return problems

    ts = OUT_TS.read_text(encoding="utf-8")
    py = OUT_PY.read_text(encoding="utf-8")
    event_types = _schema_event_types()
    kinds = _schema_action_kinds()

    for event_type in sorted(event_types):
        if f"'{event_type}'" not in ts:
            problems.append(f"TS 生成物缺少事件类型 {event_type}")
        if event_type not in py:
            problems.append(f"Python 生成物缺少事件类型 {event_type}")

    # action.kind 枚举：TS 的 z.enum([...]) 与 Python 的 class Kind(Enum)。
    import re

    ts_enum = re.search(r"kind:\s*z\.enum\(\[(.*?)\]\)", ts, re.S)
    if ts_enum is None:
        problems.append("TS 生成物找不到 action.kind 的 z.enum")
    else:
        ts_kinds = set(re.findall(r"'([a-z]+)'", ts_enum.group(1)))
        if ts_kinds != kinds:
            problems.append(f"TS 的 action.kind 与 schema 不一致：{sorted(ts_kinds)} vs {sorted(kinds)}")

    py_enum = re.search(r"class Kind\(Enum\):(.*?)(?=\n\nclass |\n\n\n)", py, re.S)
    if py_enum is None:
        problems.append("Python 生成物找不到 class Kind(Enum)")
    else:
        py_kinds = set(re.findall(r"^\s+([a-z]+)\s*=\s*'", py_enum.group(1), re.M))
        if py_kinds != kinds:
            problems.append(f"Python 的 action.kind 与 schema 不一致：{sorted(py_kinds)} vs {sorted(kinds)}")

    # payload 字段漂移：旧检查只看事件类型与 action.kind 枚举，
    # schema 给某个 payload 加字段、生成物没跟上时门禁照样全绿（实测漏过 elapsedMs）。
    # 这里要求每个 payload 属性名都出现在两份生成物里。
    for branch in _load_schema()["oneOf"]:
        props = branch["allOf"][1]["properties"]
        event_type = props["type"]["const"]
        for field in (props.get("payload") or {}).get("properties") or {}:
            if field not in ts:
                problems.append(f"TS 生成物缺少 {event_type}.{field}")
            if field not in py:
                problems.append(f"Python 生成物缺少 {event_type}.{field}")

    return problems


def _load_schema() -> dict:
    import json

    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="校验生成物与 schema 同步（逐项比对枚举，不只看文件存在）")
    args = parser.parse_args()

    if not SCHEMA.exists():
        print(f"缺少契约文件：{SCHEMA}", file=sys.stderr)
        return 2

    if args.check:
        problems = check_generated()
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            return 1
        print("生成物与 schema 同步")
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
