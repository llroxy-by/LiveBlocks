"""§6 ``Ir`` 的数据模型、确定性序列化、schema 校验与语义校验。

覆盖三件事：

1. ``Ir.to_dict()`` / ``canonical_text()`` 的形状与字节稳定性（同一次构造两次序列化字节一致）；
2. 入库 schema（``src/liveblocks/data/ir_schema.json``）+ 零依赖校验器的正例/反例矩阵，
   并（装得上 ``jsonschema`` 时）用它当**独立实现**交叉核对同一批文档；
3. ``validate_ir`` 的语义检查（op 表、args 形状、``step_id`` 密集、诊断码区间）。
"""

from __future__ import annotations

import copy
import json

import pytest

from liveblocks.ir import (
    IR_CODES,
    IR_VERSION,
    SCHEMA_FILENAME,
    Ir,
    IrLimits,
    IrStep,
    SchemaError,
    canonical_text,
    document_sha256,
    load_schema,
    schema_unsupported_keywords,
    validate_document,
    validate_ir,
)
from liveblocks.ir.schema import SUPPORTED_KEYWORDS


def sample_ir() -> Ir:
    """``move-forward 1`` + ``jump`` 两步，人工构造（不经规范化器）的最小 IR。"""
    return Ir(
        steps=(
            IrStep(
                step_id="s0",
                op="move-forward",
                args={"distance": 1, "unit": "cell"},
                source={"row": 0, "slot": 0},
            ),
            IrStep(step_id="s1", op="jump", args={}, source={"row": 1, "slot": 0}),
        ),
        limits=IrLimits(max_steps=1000, timeout_ms=5000),
    )


def sample_document() -> dict:
    return sample_ir().to_dict()


def test_ir_document_matches_section6_shape() -> None:
    document = sample_document()
    assert list(document) == ["schema_version", "kind", "ir_version", "program", "diagnostics"]
    assert document["schema_version"] == "0.0.1"
    assert document["kind"] == "Ir"
    assert document["ir_version"] == IR_VERSION
    assert list(document["program"]) == ["steps", "limits"]
    assert document["program"]["limits"] == {"max_steps": 1000, "timeout_ms": 5000}
    assert document["diagnostics"] == []
    validate_ir(document)


def test_step_fields_are_in_section6_order() -> None:
    step = sample_document()["program"]["steps"][0]
    assert list(step) == ["step_id", "op", "args", "source"]
    assert step["args"] == {"distance": 1, "unit": "cell"}
    assert step["source"] == {"row": 0, "slot": 0}


def test_canonical_text_is_byte_stable_and_deterministic() -> None:
    first = sample_ir().canonical_text()
    second = sample_ir().canonical_text()
    assert first == second
    assert first.endswith("}\n")
    assert document_sha256(first) == sample_ir().sha256()
    # 同一份文档走 canonical_text 与对象走 canonical_text 结果一致
    assert canonical_text(json.loads(first)) == first


def test_canonical_text_keeps_non_ascii_readable() -> None:
    document = sample_document()
    document["diagnostics"] = [
        {
            "code": "LB-I001",
            "severity": "error",
            "message": "行 0 槽位 0 的命令卡 'teleport' 不在 P0 的 op 表里；该语句不进 IR",
        }
    ]
    text = canonical_text(document)
    assert "不在 P0 的 op 表里" in text  # ensure_ascii=False
    assert "\\u" not in text


def test_validate_ir_accepts_section6_example_step_shape() -> None:
    # §6 示例（op 用示例里的 "move" 之外的合法 op：本实现 op 取卡面，见 docs/IR.md §9 缺口 1）
    document = {
        "schema_version": "0.0.1",
        "kind": "Ir",
        "ir_version": IR_VERSION,
        "program": {
            "steps": [
                {
                    "step_id": "s0",
                    "op": "move-forward",
                    "args": {"distance": 1, "unit": "cell"},
                    "source": {"row": 0, "slot": 0},
                }
            ],
            "limits": {"max_steps": 1000, "timeout_ms": 5000},
        },
        "diagnostics": [],
    }
    validate_ir(document)
    validate_document(document, load_schema())


def test_validate_ir_tolerates_unknown_fields() -> None:
    """§0：消费者必须容忍未知字段，因此多出来的字段不该让 IR 变成非法文档。"""
    document = copy.deepcopy(sample_document())
    document["future_field"] = {"note": "将来加的"}
    document["program"]["future_field"] = [1, 2, 3]
    document["program"]["steps"][0]["future_field"] = "x"
    validate_ir(document)


BAD_DOCUMENTS: list[tuple[str, dict]] = []


def _bad(name: str, mutate) -> None:
    document = copy.deepcopy(sample_document())
    mutate(document)
    BAD_DOCUMENTS.append((name, document))


_bad("kind 不是 Ir", lambda d: d.update(kind="Ast"))
_bad("缺少 ir_version", lambda d: d.pop("ir_version"))
_bad("ir_version 不对", lambda d: d.update(ir_version="0.0.2"))
_bad("schema_version 不是版本号", lambda d: d.update(schema_version="v1"))
_bad("缺少 program", lambda d: d.pop("program"))
_bad("program 不是对象", lambda d: d.update(program=[]))
_bad("steps 不是数组", lambda d: d["program"].update(steps={}))
_bad("step 缺少 op", lambda d: d["program"]["steps"][0].pop("op"))
_bad("step 缺少 args", lambda d: d["program"]["steps"][0].pop("args"))
_bad("step 缺少 source", lambda d: d["program"]["steps"][0].pop("source"))
_bad("source 缺 row", lambda d: d["program"]["steps"][0]["source"].pop("row"))
_bad("source.row 为负", lambda d: d["program"]["steps"][0]["source"].update(row=-1))
_bad("source.slot 是浮点", lambda d: d["program"]["steps"][0]["source"].update(slot=1.0))
_bad("step_id 形状不对", lambda d: d["program"]["steps"][0].update(step_id="step-0"))
_bad("step_id 不密集", lambda d: d["program"]["steps"][1].update(step_id="s7"))
_bad("op 未知", lambda d: d["program"]["steps"][0].update(op="teleport"))
_bad("op 为空", lambda d: d["program"]["steps"][0].update(op=""))
_bad("args 少字段", lambda d: d["program"]["steps"][0].update(args={"distance": 1}))
_bad("args 多字段", lambda d: d["program"]["steps"][0].update(args={"distance": 1, "unit": "cell", "speed": 2}))
_bad("args 单位错", lambda d: d["program"]["steps"][0].update(args={"distance": 1, "unit": "mm"}))
_bad("args 取值是字符串", lambda d: d["program"]["steps"][0].update(args={"distance": "1", "unit": "cell"}))
_bad("args 取值为负", lambda d: d["program"]["steps"][0].update(args={"distance": -1, "unit": "cell"}))
_bad("零元 op 却给了 args", lambda d: d["program"]["steps"][1].update(args={"distance": 1, "unit": "cell"}))
_bad("args 不是对象", lambda d: d["program"]["steps"][0].update(args=[1]))
_bad("limits 缺 max_steps", lambda d: d["program"]["limits"].pop("max_steps"))
_bad("limits.max_steps 为 0", lambda d: d["program"]["limits"].update(max_steps=0))
_bad("limits.timeout_ms 是浮点", lambda d: d["program"]["limits"].update(timeout_ms=1.5))
_bad("limits 不是对象", lambda d: d["program"].update(limits=[]))
_bad("diagnostics 不是数组", lambda d: d.update(diagnostics={}))
_bad("诊断码不在 LB-I0xx", lambda d: d.update(diagnostics=[{"code": "LB-P001", "severity": "error", "message": "x"}]))
_bad("诊断码形状不对", lambda d: d.update(diagnostics=[{"code": "I001", "severity": "error", "message": "x"}]))
_bad("诊断 severity 非法", lambda d: d.update(diagnostics=[{"code": "LB-I001", "severity": "fatal", "message": "x"}]))
_bad("诊断缺 message", lambda d: d.update(diagnostics=[{"code": "LB-I001", "severity": "error"}]))
_bad("诊断 span 不是对象", lambda d: d.update(diagnostics=[{"code": "LB-I001", "severity": "error", "message": "x", "span": 3}]))


@pytest.mark.parametrize("name,document", BAD_DOCUMENTS, ids=[name for name, _ in BAD_DOCUMENTS])
def test_validate_ir_rejects(name: str, document: dict) -> None:
    with pytest.raises(ValueError):
        validate_ir(document)


def test_validate_ir_accepts_every_ir_code() -> None:
    for code in IR_CODES:
        document = copy.deepcopy(sample_document())
        document["diagnostics"] = [
            {
                "code": code,
                "severity": "error",
                "message": "示例诊断",
                "candidate_ids": ["c-0"],
                "span": {"row": 0, "slot_from": 0, "slot_to": 0},
            }
        ]
        validate_ir(document)


def test_schema_is_within_supported_keywords() -> None:
    schema = load_schema()
    assert schema["$id"].endswith("ir-0.0.1.json")
    assert schema_unsupported_keywords(schema) == []


def test_schema_unsupported_keyword_is_detected() -> None:
    """反向验证：用了本校验器不支持的关键字必须报出来，否则「按 schema 校验」是假通过。"""
    schema = copy.deepcopy(load_schema())
    schema["properties"]["program"] = {"oneOf": [{"type": "object"}]}
    assert "oneOf" in schema_unsupported_keywords(schema)
    # oneOf 之外的关键字仍然都在支持范围内
    assert set(schema_unsupported_keywords(schema)) == {"oneOf"}


def test_schema_is_not_a_hollow_shell() -> None:
    """schema 必须真的描述了 §6 的必填字段，否则「通过 schema 校验」是空转。"""
    schema = load_schema()
    assert set(schema["required"]) == {"schema_version", "kind", "ir_version", "program", "diagnostics"}
    step = schema["$defs"]["step"]
    assert set(step["required"]) == {"step_id", "op", "args", "source"}
    assert set(schema["$defs"]["limits"]["required"]) == {"max_steps", "timeout_ms"}
    assert SCHEMA_FILENAME == "ir_schema.json"
    assert set(schema["$defs"]) <= SUPPORTED_KEYWORDS | {"program", "step", "source", "limits", "diagnostic"}


def test_dependency_free_validator_reports_path() -> None:
    document = copy.deepcopy(sample_document())
    document["program"]["steps"][0]["source"].pop("slot")
    with pytest.raises(SchemaError) as error:
        validate_document(document, load_schema())
    assert "program.steps[0].source" in str(error.value)


def test_cross_check_with_jsonschema_if_available() -> None:
    """装得上 ``jsonschema`` 时用它当独立实现，逐条核对「按 schema 校验」的结论。

    比的是**同一层**的东西：入库 schema + 本实现的零依赖校验器 vs ``jsonschema`` 的
    ``Draft202012Validator``。语义检查（``validate_ir``）schema 表达不了，不参与这项对比。
    CI 只装 ``.[dev]``，这条自动跳过。
    """
    jsonschema = pytest.importorskip("jsonschema", reason="需要可选的 jsonschema 才做这项交叉核对")
    schema = load_schema()
    validator = jsonschema.Draft202012Validator(schema)

    documents = [("sample", sample_document())] + list(BAD_DOCUMENTS)
    checked = 0
    for name, document in documents:
        ours_error = ""
        try:
            validate_document(document, schema)
        except ValueError as error:
            ours_error = str(error)
        theirs_ok = validator.is_valid(document)
        assert (ours_error == "") == theirs_ok, (
            f"{name}: 本实现 {'接受' if not ours_error else f'拒绝（{ours_error}）'}，"
            f"jsonschema {'接受' if theirs_ok else '拒绝'}"
        )
        checked += 1
    assert checked == len(documents) > 0
