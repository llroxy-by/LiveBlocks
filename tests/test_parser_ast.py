"""§5 ``Ast`` 的数据模型、``span`` / ``origin`` 约定与最小校验器。"""

from __future__ import annotations

import copy
import json

import pytest

from liveblocks.parser import (
    Ast,
    Command,
    Number,
    Origin,
    Program,
    Span,
    node_count,
    validate_ast,
)
from liveblocks.parser.grammar import GRAMMAR_VERSION


def sample_ast() -> Ast:
    """``move-forward 1`` + ``jump`` 两条语句，人工构造（不经解析器）的最小语法树。"""
    first = Command(
        span=Span.single(0, 0, 1),
        origin=Origin.single("c-0", "move-forward"),
        name="move-forward",
        args=(
            Number(
                span=Span.single(0, 1, 1),
                origin=Origin.single("c-1", "number-1"),
                value=1,
            ),
        ),
    )
    second = Command(
        span=Span.single(1, 0, 0),
        origin=Origin.single("c-2", "jump"),
        name="jump",
    )
    root = Program(
        span=Span.across(0, 0, 1, 1),
        origin=Origin.many([("c-0", "move-forward"), ("c-1", "number-1"), ("c-2", "jump")]),
        body=(first, second),
    )
    return Ast(root=root, frame_id="f-unit")


def test_ast_document_matches_section5_shape() -> None:
    document = sample_ast().to_dict()
    assert document["schema_version"] == "0.0.1"
    assert document["kind"] == "Ast"
    assert document["grammar_version"] == GRAMMAR_VERSION
    assert document["frame_id"] == "f-unit"
    assert document["root"]["node"] == "Program"
    # §5 要求：每个节点都带 span 与 origin
    for node in sample_ast().nodes():
        assert set(node) >= {"node", "span", "origin"}
    validate_ast(document)


def test_command_node_keeps_section5_field_order_and_names() -> None:
    document = sample_ast().to_dict()["root"]["body"][0]
    assert list(document) == ["node", "span", "name", "args", "origin"]
    assert document["name"] == "move-forward"
    assert document["args"][0]["node"] == "Number"
    assert document["args"][0]["value"] == 1
    assert document["origin"] == {"candidate_id": "c-0", "card_face": "move-forward"}


def test_span_serialisation_single_row_and_across_rows() -> None:
    assert Span.single(2, 1, 3).to_dict() == {"row": 2, "slot_from": 1, "slot_to": 3}
    assert Span.across(0, 0, 2, 1).to_dict() == {
        "row_from": 0,
        "row_to": 2,
        "slot_from": 0,
        "slot_to": 1,
    }
    # 首末行相同 → 按单行形状输出（§5 示例的样子）
    assert Span.across(1, 0, 1, 2).is_single_row
    assert Span(1, 0, 2, row_to=1).to_dict() == {"row": 1, "slot_from": 0, "slot_to": 2}
    assert Span.single(0, 0, 1).describe() == "0:0-1"
    assert Span.across(0, 0, 2, 1).describe() == "0..2:0-1"


def test_origin_single_and_many() -> None:
    assert Origin.single("c-7", "wait").to_dict() == {"candidate_id": "c-7", "card_face": "wait"}
    many = Origin.many([("c-1", "number-1"), ("c-2", "number-2")])
    assert many.to_dict() == {
        "candidate_ids": ["c-1", "c-2"],
        "card_faces": ["number-1", "number-2"],
    }
    assert many.candidate_ids == ("c-1", "c-2")
    assert many.card_faces == ("number-1", "number-2")
    # 空程序：两个空数组，仍然有 origin（§5 要求每个节点都带）
    assert Origin.many([]).to_dict() == {"candidate_ids": [], "card_faces": []}


def test_node_count_walks_program_command_and_numbers() -> None:
    ast = sample_ast()
    assert node_count(ast) == 4  # Program + Command + Number + Command
    kinds = [node["node"] for node in ast.nodes()]
    assert kinds == ["Program", "Command", "Number", "Command"]


def test_ast_helpers_expose_statements_and_codes() -> None:
    ast = sample_ast()
    assert [command.name for command in ast.statements] == ["move-forward", "jump"]
    assert ast.codes() == ()
    assert ast.diagnostics_with("LB-P001") == ()


def test_validate_ast_accepts_section5_example_shape() -> None:
    """把 §5 的示例按本实现的约定补上 origin，应能通过校验。"""
    document = {
        "schema_version": "0.0.1",
        "kind": "Ast",
        "grammar_version": GRAMMAR_VERSION,
        "root": {
            "node": "Program",
            "span": {"row": 0, "slot_from": 0, "slot_to": 2},
            "body": [
                {
                    "node": "Command",
                    "span": {"row": 0, "slot_from": 0, "slot_to": 0},
                    "name": "move-forward",
                    "args": [
                        {
                            "node": "Number",
                            "value": 1,
                            "span": {"row": 0, "slot_from": 1, "slot_to": 1},
                            "origin": {"candidate_id": "c-8", "card_face": "number-1"},
                        }
                    ],
                    "origin": {"candidate_id": "c-7", "card_face": "move-forward"},
                }
            ],
            "origin": {"candidate_ids": ["c-7", "c-8"], "card_faces": ["move-forward", "number-1"]},
        },
        "frame_id": "f-000123",
        "diagnostics": [],
    }
    validate_ast(document)


def test_validate_ast_tolerates_unknown_fields_and_unknown_node_kind() -> None:
    """§0：未知字段必须被容忍；§5：``node`` 是开放枚举。"""
    document = sample_ast().to_dict()
    document["extra_layer_field"] = {"whatever": True}
    document["root"]["note"] = "未知字段"
    document["root"]["body"].append(
        {
            "node": "Loop",
            "span": {"row": 2, "slot_from": 0, "slot_to": 1},
            "origin": {"candidate_ids": ["c-3"], "card_faces": ["repeat"]},
            "extra": 1,
        }
    )
    validate_ast(document)


def _drop(mutator) -> dict:
    document = sample_ast().to_dict()
    mutator(document)
    return document


BAD_ASTS = {
    "kind 不对": _drop(lambda d: d.update(kind="Layout")),
    "schema_version 缺失": _drop(lambda d: d.pop("schema_version")),
    "schema_version 形状不对": _drop(lambda d: d.update(schema_version="v1")),
    "grammar_version 缺失": _drop(lambda d: d.pop("grammar_version")),
    "frame_id 缺失": _drop(lambda d: d.pop("frame_id")),
    "root 缺失": _drop(lambda d: d.pop("root")),
    "root 不是 Program": _drop(lambda d: d["root"].update(node="Command")),
    "节点缺 span": _drop(lambda d: d["root"]["body"][0].pop("span")),
    "节点缺 origin": _drop(lambda d: d["root"]["body"][0].pop("origin")),
    "span 缺 slot_to": _drop(lambda d: d["root"]["body"][0]["span"].pop("slot_to")),
    "span 同时给 row 与 row_from": _drop(
        lambda d: d["root"]["body"][0]["span"].update(row_from=0, row_to=0)
    ),
    "span.slot_from > slot_to": _drop(
        lambda d: d["root"]["body"][0]["span"].update(slot_from=3, slot_to=1)
    ),
    "span 有负数": _drop(lambda d: d["root"]["body"][0]["span"].update(row=-1)),
    "span 是字符串": _drop(lambda d: d["root"].update(span="0:0-2")),
    "origin 两个形状都没有": _drop(lambda d: d["root"].update(origin={"card_face": "jump"})),
    "origin.candidate_id 是空串": _drop(
        lambda d: d["root"]["body"][0].update(origin={"candidate_id": "", "card_face": "jump"})
    ),
    "origin.card_faces 与 candidate_ids 长度不符": _drop(
        lambda d: d["root"].update(origin={"candidate_ids": ["c-0"], "card_faces": []})
    ),
    "Command.name 缺失": _drop(lambda d: d["root"]["body"][0].pop("name")),
    "Command.args 不是数组": _drop(lambda d: d["root"]["body"][0].update(args={})),
    "Number.value 不是整数": _drop(lambda d: d["root"]["body"][0]["args"][0].update(value="1")),
    "Program.body 不是数组": _drop(lambda d: d["root"].update(body={})),
    "diagnostics 不是数组": _drop(lambda d: d.update(diagnostics={})),
    "诊断 severity 非法": _drop(
        lambda d: d.update(
            diagnostics=[{"code": "LB-P001", "severity": "fatal", "message": "x"}]
        )
    ),
    "诊断 span 形状不对": _drop(
        lambda d: d.update(
            diagnostics=[
                {
                    "code": "LB-P001",
                    "severity": "error",
                    "message": "x",
                    "span": {"row": "0", "slot_from": 0, "slot_to": 0},
                }
            ]
        )
    ),
    "诊断 origin 形状不对": _drop(
        lambda d: d.update(
            diagnostics=[
                {"code": "LB-P001", "severity": "error", "message": "x", "origin": {"card_face": "x"}}
            ]
        )
    ),
}


@pytest.mark.parametrize("name", sorted(BAD_ASTS))
def test_validate_ast_rejects_broken_documents(name: str) -> None:
    document = copy.deepcopy(BAD_ASTS[name])
    with pytest.raises(ValueError):
        validate_ast(document)


def test_validate_ast_rejects_non_object_input() -> None:
    for value in ([], "Ast", 3, None):
        with pytest.raises(ValueError):
            validate_ast(value)  # type: ignore[arg-type]


def test_ast_document_is_json_serialisable_and_stable() -> None:
    document = sample_ast().to_dict()
    first = json.dumps(document, ensure_ascii=False, sort_keys=True)
    second = json.dumps(sample_ast().to_dict(), ensure_ascii=False, sort_keys=True)
    assert first == second
    assert json.loads(first)["kind"] == "Ast"
