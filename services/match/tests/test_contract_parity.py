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


def test_schema_accepts_every_action_kind_the_runner_writes():
    """runner 会写 forfeit（弃权），schema 必须接受，否则契约与实现漂移。

    这条曾经真实漂移：schema 只允许 ask/guess，runner 弃权时写 forfeit，
    而校验器从未接在写路径上，所以没有任何测试发现。
    """
    import json

    from services.match.contract import check
    from services.match.engine.base import Move

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    kinds: set[str] = set()
    for branch in schema["oneOf"]:
        props = branch["allOf"][1]["properties"]
        if props["type"]["const"] == "action.applied":
            kinds = set(props["payload"]["properties"]["action"]["properties"]["kind"]["enum"])
    assert kinds == {"ask", "guess", "forfeit"}, kinds

    event = {
        "seq": 0,
        "matchId": "m",
        "type": "action.applied",
        "at": "2026-10-27T00:00:00+00:00",
        "player": "A",
        "payload": {
            "action": {"kind": Move(kind="forfeit", value="").kind, "value": ""},
            "effects": [{"kind": "forfeit", "by": "A"}],
        },
    }
    check(event)


def test_codegen_check_detects_drift(monkeypatch, tmp_path):
    """--check 必须是真门禁：生成物与 schema 不一致时返回非零。"""
    import importlib.util

    script = ROOT / "packages" / "contracts" / "scripts" / "codegen.py"
    spec = importlib.util.spec_from_file_location("melee_codegen", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.check_generated() == []

    original = module.OUT_TS.read_text(encoding="utf-8")
    drifted = original.replace("kind: z.enum(['ask', 'guess', 'forfeit'])", "kind: z.enum(['ask', 'guess'])")
    assert drifted != original, "生成物里应有可被替换的枚举"
    monkeypatch.setattr(module, "OUT_TS", tmp_path / "battle-event.zod.ts")
    module.OUT_TS.write_text(drifted, encoding="utf-8")
    problems = module.check_generated()
    assert problems, "生成物与 schema 不一致时必须报告问题"


def test_contract_check_agrees_with_jsonschema_on_valid_and_invalid():
    """编译版校验必须与 jsonschema 判定一致：快不能以放松为代价。"""
    import json
    from pathlib import Path

    from jsonschema import Draft202012Validator, FormatChecker

    from services.match import contract

    schema = json.loads(Path(contract.SCHEMA_PATH).read_text(encoding="utf-8"))
    # 参考校验器必须与产品实现同一标准：schema 声明了 format: date-time。
    # 注意 jsonschema 的 FormatChecker 在缺少可选依赖 rfc3339-validator 时
    # 根本不注册 date-time，实测 `at: "not-a-date"` 会被判合法——所以这里
    # 用标准库 datetime 自己补一条，保证两边比较的是同一件事。
    reference = Draft202012Validator(schema, format_checker=FormatChecker())

    def reference_errors(case: dict) -> list:
        errors = list(reference.iter_errors(case))
        try:
            from datetime import datetime

            datetime.fromisoformat(str(case.get("at", "")).replace("Z", "+00:00"))
        except ValueError:
            errors.append(ValueError("at 不是合法的 date-time"))
        return errors
    valid = {
        "seq": 3,
        "matchId": "m_1",
        "type": "action.applied",
        "at": "2026-10-21T00:00:03+00:00",
        "player": "A",
        "payload": {
            "action": {"kind": "guess", "value": "[4]"},
            "effects": [{"kind": "action", "action": "[4]"}],
            "elapsedMs": 850,
        },
    }
    contract.check(valid)
    assert not reference_errors(valid)

    invalid_cases = [
        {**valid, "seq": -1},
        {**valid, "matchId": ""},
        {**valid, "type": "no.such.type"},
        {**valid, "at": "not-a-date"},
        {**valid, "payload": {"action": {"kind": "guess", "value": "[4]"}}},  # 缺 effects
        {**valid, "payload": {**valid["payload"], "elapsedMs": -5}},
    ]
    for case in invalid_cases:
        assert reference_errors(case), f"参考校验器应判非法：{case.get('type')}"
        try:
            contract.check(case)
        except contract.ContractViolation:
            continue
        raise AssertionError(f"编译版校验漏掉了非法事件：{case}")


def test_contract_check_error_message_keeps_path_and_reason():
    """错误信息要能定位：保留字段路径，不能退化成一句 "invalid"。"""
    from services.match import contract

    bad = {
        "seq": 1,
        "matchId": "m_1",
        "type": "action.applied",
        "at": "2026-10-21T00:00:01+00:00",
        "player": "A",
        "payload": {"action": {"kind": "guess", "value": "x"}, "effects": []},
    }
    bad["payload"]["action"]["kind"] = "not-a-kind"
    try:
        contract.check(bad)
    except contract.ContractViolation as exc:
        message = str(exc)
        assert "kind" in message, message
        assert "不符合契约" in message
    else:
        raise AssertionError("非法 kind 应被拒绝")


def test_codegen_check_detects_payload_field_drift():
    """payload 字段漂移也必须被抓住。

    旧 --check 只比对事件类型与 action.kind 枚举：schema 里给
    action.applied 加了 elapsedMs，生成物没跟上，门禁照样全绿。
    实测就是这么漏的——这里把"每个 payload 属性名都要出现在生成物里"
    变成断言，避免同类问题再次静默漂移。
    """
    import importlib.util
    import json

    script = ROOT / "packages" / "contracts" / "scripts" / "codegen.py"
    spec = importlib.util.spec_from_file_location("melee_codegen_fields", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    schema = json.loads(module.SCHEMA.read_text(encoding="utf-8"))
    ts = module.OUT_TS.read_text(encoding="utf-8")
    py = module.OUT_PY.read_text(encoding="utf-8")
    missing: list[str] = []
    for branch in schema["oneOf"]:
        props = branch["allOf"][1]["properties"]
        for field in (props.get("payload") or {}).get("properties") or {}:
            if field not in ts:
                missing.append(f"TS 缺 payload 字段 {field}")
            if field not in py:
                missing.append(f"Python 缺 payload 字段 {field}")
    assert not missing, missing
