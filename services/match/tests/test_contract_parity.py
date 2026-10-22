"""契约一致性：schema、pydantic 模型、TS 常量三处的事件类型必须完全一致。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from services.match.contract import SCHEMA_PATH

ROOT = Path(__file__).resolve().parents[3]
TS_SRC = ROOT / "packages" / "contracts" / "src" / "battle-event.zod.ts"
PY_SRC = ROOT / "packages" / "contracts" / "src" / "battle_event.py"


def schema_types() -> set[str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {
        branch["allOf"][1]["properties"]["type"]["const"]
        for branch in schema["oneOf"]
    }


def ts_types() -> set[str]:
    source = TS_SRC.read_text(encoding="utf-8")
    block = re.search(r"EVENT_TYPES = \[(.*?)\] as const", source, re.S)
    assert block, "EVENT_TYPES 常量缺失"
    return set(re.findall(r"'([a-z.]+)'", block.group(1)))


def test_schema_and_ts_agree():
    assert schema_types() == ts_types()


def test_ts_covers_every_schema_branch():
    source = TS_SRC.read_text(encoding="utf-8")
    for event_type in schema_types():
        assert f"'{event_type}'" in source, f"TS 侧缺少 {event_type}"


def test_python_models_are_generated():
    assert PY_SRC.exists(), "运行 packages/contracts/scripts/codegen.py 生成 pydantic 模型"
    source = PY_SRC.read_text(encoding="utf-8")
    for event_type in schema_types():
        assert event_type in source, f"pydantic 模型缺少 {event_type}"
