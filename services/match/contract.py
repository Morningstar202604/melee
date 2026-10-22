"""用 JSON Schema 校验每一条落库事件。

契约的唯一真相源是 schema/battle-event.schema.json；这里只用它，不重复定义字段。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "schema" / "battle-event.schema.json"


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


class ContractViolation(ValueError):
    pass


def check(event: dict[str, Any]) -> None:
    errors = sorted(validator().iter_errors(event), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    parts = []
    for error in errors:
        path = "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in error.absolute_path)
        parts.append(f"{path}: {error.message}")
    raise ContractViolation(f"事件 {event.get('seq')} 不符合契约：" + "；".join(parts))


def check_all(events: list[dict[str, Any]]) -> None:
    for event in events:
        check(event)
