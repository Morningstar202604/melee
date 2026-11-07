"""用 JSON Schema 校验每一条落库事件。

契约的唯一真相源是 schema/battle-event.schema.json；这里只用它，不重复定义字段。

两道实现：
- `fastjsonschema` 在首次使用时把 schema 编译成 Python 函数，之后每次校验
  约快 20 倍。事件日志的写路径每场几百条事件、导播台还在 1.5s 轮询，
  这一步曾经占掉可观的时间。
- 编译失败（或未安装 fastjsonschema）时退回 jsonschema，功能不降级。

格式校验必须开：schema 写了 `format: date-time`，但 jsonschema 默认不检查 format，
于是 `at: "not-a-date"` 能一路写进事件日志——回放按时间排序时才炸。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


def _schema_path() -> Path:
    """契约文件路径：源码树与 PyInstaller 冻结包都成立。

    冻结时 datas 把 schema 放进 sys._MEIPASS/packages/...，
    源码树里 parents[2] 就是仓库根。
    """
    import sys

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / "packages" / "contracts" / "schema" / "battle-event.schema.json"


SCHEMA_PATH = _schema_path()


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def validator() -> Draft202012Validator:
    """参考实现（带 format 检查）。编译版不可用时用它。"""
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@lru_cache(maxsize=1)
def compiled() -> Any:
    """编译版校验函数；未安装 fastjsonschema 时返回 None。"""
    try:
        import fastjsonschema
    except ImportError:
        return None
    try:
        return fastjsonschema.compile(_load_schema())
    except Exception:  # noqa: BLE001 - 编译失败就退回参考实现，不能影响启动
        return None


@lru_cache(maxsize=1)
def event_types() -> frozenset[str]:
    """schema 里声明的全部事件类型。

    事件类型之前硬编码在 eventlog.EVENT_TYPES，与 schema 是两份真相；
    新增一种事件只要漏改一处就静默漂移。现在只有一个来源。
    """
    schema = _load_schema()
    return frozenset(
        branch["allOf"][1]["properties"]["type"]["const"] for branch in schema["oneOf"]
    )


class ContractViolation(ValueError):
    pass


def check(event: dict[str, Any]) -> None:
    fast = compiled()
    if fast is not None:
        try:
            fast(event)
            return
        except Exception:  # noqa: BLE001 - 失败路径改用参考实现，报错才带字段路径
            # 快路径只负责"通过/不通过"；一旦不通过，用参考校验器重新生成
            # 带字段路径的错误，否则调用方只看到一句 "0 matches found"。
            _check_with_reference(event)
            raise ContractViolation(f"事件 {event.get('seq')} 不符合契约") from None
    _check_with_reference(event)


def _check_with_reference(event: dict[str, Any]) -> None:
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
