"""规范化规则：op 表、``args`` 折法、``step_id`` / ``source``、``limits`` 填充与 ``LB-I0xx``。

反例都走「同一份 AST、只改一处」的路子，确保断言的是**规范化语义**而不是夹具有没有写对。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from liveblocks.ir import (
    DIAG_ARITY,
    DIAG_NON_LITERAL_ARG,
    DIAG_UNKNOWN_OP,
    DIAG_UNSUPPORTED_STATEMENT,
    IR_CODES,
    IR_VERSION,
    LIMIT_DEFAULTS,
    normalize,
    normalize_ast_document,
    resolve_limits,
    validate_ir,
)
from liveblocks.parser import parse_layout_document

REPO_ROOT = Path(__file__).resolve().parents[1]
AST_DIR = REPO_ROOT / "fixtures" / "ast"
IR_AST_DIR = REPO_ROOT / "fixtures" / "ir" / "asts"


def number(row: int, slot: int, value: int, candidate_id: str) -> dict:
    return {
        "node": "Number",
        "span": {"row": row, "slot_from": slot, "slot_to": slot},
        "value": value,
        "origin": {"candidate_id": candidate_id, "card_face": f"number-{value}"},
    }


def command(row: int, name: str, args: list[dict] | None = None, candidate_id: str | None = None) -> dict:
    arguments = list(args or [])
    return {
        "node": "Command",
        "span": {"row": row, "slot_from": 0, "slot_to": len(arguments)},
        "name": name,
        "args": arguments,
        "origin": {"candidate_id": candidate_id or f"c-{row}", "card_face": name},
    }


def ast_document(*statements: dict, frame_id: str = "f-normalize") -> dict:
    body = list(statements)
    ids = [statement["origin"]["candidate_id"] for statement in body]
    faces = [statement["origin"]["card_face"] for statement in body]
    slot_to = max((statement["span"]["slot_to"] for statement in body), default=0)
    return {
        "schema_version": "0.0.1",
        "kind": "Ast",
        "frame_id": frame_id,
        "grammar_version": "0.0.1",
        "root": {
            "node": "Program",
            "span": {
                "row_from": 0,
                "row_to": max(len(body) - 1, 0),
                "slot_from": 0,
                "slot_to": slot_to,
            },
            "body": body,
            "origin": {"candidate_ids": ids, "card_faces": faces},
        },
        "diagnostics": [],
    }


ONE_ARG = {
    "move-forward": ("distance", "cell"),
    "move-back": ("distance", "cell"),
    "turn-left": ("angle", "quarter_turn"),
    "turn-right": ("angle", "quarter_turn"),
    "wait": ("duration", "tick"),
}
NO_ARG = ("jump", "pick", "drop")


@pytest.mark.parametrize("face", sorted(ONE_ARG))
def test_one_argument_ops_fold_to_named_arg_and_unit(face: str) -> None:
    document = ast_document(command(0, face, [number(0, 1, 5, "c-1")]))
    ir = normalize_ast_document(document)
    assert ir.codes() == ()
    step = ir.steps[0]
    name, unit = ONE_ARG[face]
    assert step.op == face
    assert step.args == {name: 5, "unit": unit}
    assert step.source == {"row": 0, "slot": 0}
    assert step.step_id == "s0"


@pytest.mark.parametrize("face", NO_ARG)
def test_zero_argument_ops_have_empty_args(face: str) -> None:
    ir = normalize_ast_document(ast_document(command(0, face)))
    assert ir.codes() == ()
    assert ir.steps[0].op == face
    assert ir.steps[0].args == {}


def test_step_ids_are_dense_and_ordered_by_ast_body() -> None:
    document = ast_document(
        command(0, "move-forward", [number(0, 1, 1, "c-1")], "c-0"),
        command(1, "jump", [], "c-2"),
        command(2, "wait", [number(2, 1, 3, "c-4")], "c-3"),
    )
    ir = normalize_ast_document(document)
    assert [step.step_id for step in ir.steps] == ["s0", "s1", "s2"]
    assert [step.op for step in ir.steps] == ["move-forward", "jump", "wait"]
    assert [step.source["row"] for step in ir.steps] == [0, 1, 2]
    validate_ir(ir.to_dict())


def test_empty_program_still_has_explicit_limits() -> None:
    ir = normalize_ast_document(ast_document())
    assert ir.steps == ()
    assert ir.diagnostics == ()
    document = ir.to_dict()
    assert document["program"] == {
        "steps": [],
        "limits": {"max_steps": LIMIT_DEFAULTS["max_steps"], "timeout_ms": LIMIT_DEFAULTS["timeout_ms"]},
    }
    validate_ir(document)


def test_limits_defaults_come_from_section6_example() -> None:
    limits = resolve_limits()
    assert limits.to_dict() == {"max_steps": 1000, "timeout_ms": 5000}


def test_limits_can_be_overridden_and_are_written_into_ir() -> None:
    ir = normalize_ast_document(ast_document(command(0, "jump")), max_steps=7, timeout_ms=250)
    assert ir.limits.to_dict() == {"max_steps": 7, "timeout_ms": 250}
    validate_ir(ir.to_dict())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_steps": 0},
        {"timeout_ms": 0},
        {"max_steps": -3},
        {"timeout_ms": 1.5},
        {"max_steps": True},
    ],
)
def test_invalid_limits_raise(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        resolve_limits(**kwargs)


def test_unknown_op_reports_lb_i001_and_drops_the_statement() -> None:
    document = ast_document(
        command(0, "move-forward", [number(0, 1, 1, "c-1")], "c-0"),
        command(1, "teleport", [], "c-2"),
        command(2, "jump", [], "c-3"),
    )
    ir = normalize_ast_document(document)
    assert ir.codes() == (DIAG_UNKNOWN_OP,)
    assert [step.op for step in ir.steps] == ["move-forward", "jump"]
    # 丢掉的语句不占 step_id：编号仍然密集
    assert [step.step_id for step in ir.steps] == ["s0", "s1"]
    entry = ir.diagnostics[0]
    assert entry["severity"] == "error"
    assert "teleport" in entry["message"]
    assert entry["candidate_ids"] == ["c-2"]
    assert entry["span"] == {"row": 1, "slot_from": 0, "slot_to": 0}
    validate_ir(ir.to_dict())


def test_control_flow_card_is_an_unknown_op() -> None:
    """``repeat`` 是文法外的控制流卡：IR 里没有它的位置，按未知 op 处理。"""
    ir = normalize_ast_document(ast_document(command(0, "repeat", [number(0, 1, 2, "c-1")])))
    assert ir.codes() == (DIAG_UNKNOWN_OP,)
    assert ir.steps == ()
    assert "repeat" in ir.diagnostics[0]["message"]


@pytest.mark.parametrize(
    "face,argc",
    [
        ("move-forward", 0),
        ("move-forward", 2),
        ("jump", 1),
        ("wait", 0),
    ],
)
def test_arity_mismatch_reports_lb_i002(face: str, argc: int) -> None:
    args = [number(0, index + 1, 1, f"c-{index + 1}") for index in range(argc)]
    ir = normalize_ast_document(ast_document(command(0, face, args, "c-0")))
    assert ir.codes() == (DIAG_ARITY,)
    assert ir.steps == ()
    message = ir.diagnostics[0]["message"]
    assert face in message
    assert f"实际 {argc} 个" in message


def test_non_literal_argument_reports_lb_i004() -> None:
    text_arg = {
        "node": "String",
        "span": {"row": 0, "slot_from": 1, "slot_to": 1},
        "value": "one",
        "origin": {"candidate_id": "c-1", "card_face": "number-1"},
    }
    ir = normalize_ast_document(ast_document(command(0, "move-forward", [text_arg], "c-0")))
    assert ir.codes() == (DIAG_NON_LITERAL_ARG,)
    assert ir.steps == ()
    assert "move-forward" in ir.diagnostics[0]["message"]


def test_unsupported_statement_node_reports_lb_i003() -> None:
    loop = {
        "node": "Loop",
        "span": {"row": 1, "slot_from": 0, "slot_to": 0},
        "origin": {"candidate_id": "c-1", "card_face": "repeat"},
    }
    document = ast_document(command(0, "jump", [], "c-0"), loop)
    ir = normalize_ast_document(document)
    assert ir.codes() == (DIAG_UNSUPPORTED_STATEMENT,)
    assert [step.op for step in ir.steps] == ["jump"]
    assert "Loop" in ir.diagnostics[0]["message"]


def test_one_statement_yields_at_most_one_diagnostic() -> None:
    """未知 op 的卡上再多给两个参数也只报一条：先查 op 存不存在，不级联。"""
    document = ast_document(command(0, "teleport", [number(0, 1, 1, "c-1"), number(0, 2, 2, "c-2")], "c-0"))
    ir = normalize_ast_document(document)
    assert ir.codes() == (DIAG_UNKNOWN_OP,)


def test_every_ir_code_is_reachable() -> None:
    """四个诊断码都必须有真的触发路径（防止码表里挂着没人用的码）。"""
    triggered = set()
    documents = [
        ast_document(command(0, "teleport", [])),
        ast_document(command(0, "jump", [number(0, 1, 1, "c-1")])),
        ast_document(
            {
                "node": "Loop",
                "span": {"row": 0, "slot_from": 0, "slot_to": 0},
                "origin": {"candidate_id": "c-0", "card_face": "repeat"},
            }
        ),
        ast_document(
            command(
                0,
                "move-forward",
                [{"node": "String", "span": {"row": 0, "slot_from": 1, "slot_to": 1}, "value": "x",
                  "origin": {"candidate_id": "c-1", "card_face": "number-1"}}],
            )
        ),
    ]
    for document in documents:
        triggered.update(normalize_ast_document(document).codes())
    assert triggered == set(IR_CODES)


def test_upstream_parse_diagnostics_are_not_copied() -> None:
    """语法层的 ``LB-P0xx`` 留在 Ast 里：``Ir.diagnostics`` 只放 ``LB-I0xx``。"""
    layout = json.loads((AST_DIR / "invalid-number-first.json").read_text(encoding="utf-8"))
    ast = parse_layout_document(layout)
    assert ast.codes()  # 上游确实有诊断
    ir = normalize(ast)
    assert ir.codes() == ()


def test_normalize_is_byte_identical_across_calls_and_processes() -> None:
    """同一 AST 规范化两次（同进程 / 不同 ``PYTHONHASHSEED`` 的子进程）都必须字节一致。"""
    path = IR_AST_DIR / "all-ops.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    first = normalize_ast_document(document).canonical_text()
    second = normalize_ast_document(copy.deepcopy(document)).canonical_text()
    assert first == second

    outputs = []
    for seed in ("0", "1", "12345"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-m", "liveblocks", "ir", "normalize", "--ast", str(path)],
            capture_output=True,
            cwd=str(REPO_ROOT),
            env=environment,
            check=True,
        )
        outputs.append(completed.stdout)
    assert len(set(outputs)) == 1
    assert outputs[0].decode("utf-8") == first


def test_normalize_rejects_invalid_ast_document() -> None:
    document = ast_document(command(0, "jump"))
    del document["root"]["body"][0]["origin"]  # §5 要求每个节点都有 origin
    with pytest.raises(ValueError):
        normalize_ast_document(document)


def test_normalize_rejects_non_ast_kind() -> None:
    with pytest.raises(ValueError):
        normalize_ast_document({"kind": "Layout", "schema_version": "0.0.1"})


def test_ir_version_and_schema_version_are_stable() -> None:
    ir = normalize_ast_document(ast_document(command(0, "jump")))
    document = ir.to_dict()
    assert document["ir_version"] == IR_VERSION == "0.0.1"
    assert document["schema_version"] == "0.0.1"
