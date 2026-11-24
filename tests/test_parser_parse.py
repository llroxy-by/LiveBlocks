"""解析器本体：Layout（§4）→ Ast（§5），合法路径与非法路径都要覆盖。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.layout import layout_from_document, validate_layout
from liveblocks.parser import (
    DIAG_BAD_ARGUMENT,
    DIAG_CONTROL_UNSUPPORTED,
    DIAG_EMPTY_SLOT,
    DIAG_MISSING_COMMAND,
    DIAG_OPERATOR_UNSUPPORTED,
    PARSER_CODES,
    card_role,
    parse_layout,
    parse_layout_document,
    validate_ast,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_FIXTURES = REPO_ROOT / "fixtures" / "layouts"


def slot(index: int, candidate_id: str, card_face: str) -> dict:
    return {
        "slot_index": index,
        "candidate_id": candidate_id,
        "card_face": card_face,
        "orientation_quadrant": 0,
        "x_px": 100 + 80 * index,
        "y_px": 200 + 80 * index,
        "confidence": 0.9,
    }


def layout(*rows: list[dict], frame_id: str = "f-unit") -> dict:
    return {
        "schema_version": "0.0.1",
        "kind": "Layout",
        "frame_id": frame_id,
        "reading_direction": "ltr_ttb",
        "rows": [{"row_index": index, "slots": slots} for index, slots in enumerate(rows)],
        "diagnostics": [],
    }


def test_valid_layout_becomes_program_with_spans_and_origins() -> None:
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "number-2")],
        [slot(0, "c-2", "jump")],
    )
    ast = parse_layout_document(document)
    validate_ast(ast.to_dict())

    assert ast.frame_id == "f-unit"
    assert ast.diagnostics == ()
    assert [command.name for command in ast.statements] == ["move-forward", "jump"]
    assert ast.root.span.to_dict() == {
        "row_from": 0,
        "row_to": 1,
        "slot_from": 0,
        "slot_to": 1,
    }

    first = ast.root.body[0]
    assert first.span.to_dict() == {"row": 0, "slot_from": 0, "slot_to": 1}
    assert first.origin.to_dict() == {"candidate_id": "c-0", "card_face": "move-forward"}
    assert [argument.value for argument in first.args] == [2]
    assert first.args[0].span.to_dict() == {"row": 0, "slot_from": 1, "slot_to": 1}
    assert first.args[0].origin.to_dict() == {"candidate_id": "c-1", "card_face": "number-2"}

    second = ast.root.body[1]
    assert second.args == ()
    assert second.span.to_dict() == {"row": 1, "slot_from": 0, "slot_to": 0}
    # Program 的 origin 覆盖参与语句的全部卡片，顺序 = 语句顺序
    assert ast.root.origin.to_dict() == {
        "candidate_ids": ["c-0", "c-1", "c-2"],
        "card_faces": ["move-forward", "number-2", "jump"],
    }


def test_arguments_keep_slot_order_and_accept_several_literals() -> None:
    document = layout([slot(0, "c-0", "turn-left"), slot(1, "c-1", "number-5"),
                       slot(2, "c-2", "number-1"), slot(3, "c-3", "number-3")])
    ast = parse_layout_document(document)
    assert [argument.value for argument in ast.root.body[0].args] == [5, 1, 3]
    assert ast.root.body[0].span.to_dict() == {"row": 0, "slot_from": 0, "slot_to": 3}


def test_wait_is_a_statement_with_a_literal_argument() -> None:
    ast = parse_layout_document(layout([slot(0, "c-0", "wait"), slot(1, "c-1", "number-3")]))
    assert ast.diagnostics == ()
    assert ast.root.body[0].name == "wait"
    assert [argument.value for argument in ast.root.body[0].args] == [3]


def test_no_arity_check_in_p0() -> None:
    """P0 不检查参数个数（§5 与 D-005 都没定义 arity，留给 LB-006）。"""
    ast = parse_layout_document(
        layout([slot(0, "c-0", "jump"), slot(1, "c-1", "number-1"), slot(2, "c-2", "number-2"),
                slot(3, "c-3", "number-3")])
    )
    assert ast.diagnostics == ()
    assert len(ast.root.body[0].args) == 3
    ast = parse_layout_document(layout([slot(0, "c-0", "pick")]))
    assert ast.diagnostics == ()
    assert ast.root.body[0].args == ()


def test_empty_layout_is_a_legal_empty_program() -> None:
    ast = parse_layout_document(layout())
    validate_ast(ast.to_dict())
    assert ast.root.body == ()
    assert ast.diagnostics == ()
    assert ast.root.span.to_dict() == {"row": 0, "slot_from": 0, "slot_to": 0}
    assert ast.root.origin.to_dict() == {"candidate_ids": [], "card_faces": []}


def test_number_first_row_yields_p001_and_keeps_other_statements() -> None:
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "number-1")],
        [slot(0, "c-2", "number-2"), slot(1, "c-3", "number-5")],
        [slot(0, "c-4", "pick")],
    )
    ast = parse_layout_document(document)
    validate_ast(ast.to_dict())
    assert [command.name for command in ast.statements] == ["move-forward", "pick"]
    entries = ast.diagnostics_with(DIAG_MISSING_COMMAND)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["severity"] == "error"
    assert entry["span"] == {"row": 1, "slot_from": 0, "slot_to": 0}
    assert entry["origin"] == {"candidate_id": "c-2", "card_face": "number-2"}
    assert entry["candidate_ids"] == ["c-2"]
    assert "命令卡" in entry["message"]


def test_command_in_argument_position_yields_p002() -> None:
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "number-1"), slot(2, "c-2", "jump")]
    )
    ast = parse_layout_document(document)
    assert ast.root.body == ()  # 整行丢弃
    entry = ast.diagnostics[0]
    assert entry["code"] == DIAG_BAD_ARGUMENT
    assert entry["span"] == {"row": 0, "slot_from": 2, "slot_to": 2}
    assert entry["origin"] == {"candidate_id": "c-2", "card_face": "jump"}


def test_control_flow_cards_yield_p004_at_statement_head() -> None:
    document = layout(
        [slot(0, "c-0", "repeat"), slot(1, "c-1", "number-2")],
        [slot(0, "c-2", "if")],
        [slot(0, "c-3", "end")],
    )
    ast = parse_layout_document(document)
    validate_ast(ast.to_dict())
    assert ast.root.body == ()
    assert ast.codes() == (
        DIAG_CONTROL_UNSUPPORTED,
        DIAG_CONTROL_UNSUPPORTED,
        DIAG_CONTROL_UNSUPPORTED,
    )
    assert [entry["span"]["row"] for entry in ast.diagnostics] == [0, 1, 2]
    assert [entry["origin"]["card_face"] for entry in ast.diagnostics] == ["repeat", "if", "end"]


def test_control_flow_card_in_argument_position_yields_p004() -> None:
    ast = parse_layout_document(layout([slot(0, "c-0", "move-forward"), slot(1, "c-1", "repeat")]))
    assert ast.codes() == (DIAG_CONTROL_UNSUPPORTED,)
    assert ast.diagnostics[0]["span"] == {"row": 0, "slot_from": 1, "slot_to": 1}


def test_operator_card_yields_p005() -> None:
    head = parse_layout_document(layout([slot(0, "c-0", "operator-add"), slot(1, "c-1", "number-1")]))
    assert head.codes() == (DIAG_OPERATOR_UNSUPPORTED,)

    argument = parse_layout_document(
        layout([slot(0, "c-0", "move-forward"), slot(1, "c-1", "operator-add")])
    )
    assert argument.codes() == (DIAG_OPERATOR_UNSUPPORTED,)
    assert argument.diagnostics[0]["span"] == {"row": 0, "slot_from": 1, "slot_to": 1}
    assert "表达式" in argument.diagnostics[0]["message"]


def test_empty_and_unknown_card_faces_yield_p003() -> None:
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "")],
        [slot(0, "c-2", "hologram")],
    )
    ast = parse_layout_document(document)
    assert ast.codes() == (DIAG_EMPTY_SLOT, DIAG_EMPTY_SLOT)
    assert ast.diagnostics[0]["origin"] == {"candidate_id": "c-1", "card_face": ""}
    assert ast.diagnostics[1]["origin"] == {"candidate_id": "c-2", "card_face": "hologram"}


def test_row_reports_only_the_first_problem_slot() -> None:
    """一行只报一条诊断（整行丢弃），不级联：后面的错卡不会各自再刷一条。"""
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "repeat"), slot(2, "c-2", "jump")]
    )
    ast = parse_layout_document(document)
    assert len(ast.diagnostics) == 1
    assert ast.diagnostics[0]["span"] == {"row": 0, "slot_from": 1, "slot_to": 1}


def test_every_diagnostic_is_located_to_row_slot_and_card() -> None:
    document = layout(
        [slot(0, "c-0", "repeat")],
        [slot(0, "c-1", "hologram")],
        [slot(0, "c-2", "number-1")],
        [slot(0, "c-3", "move-forward"), slot(1, "c-4", "jump")],
        [slot(0, "c-5", "move-forward"), slot(1, "c-6", "operator-add")],
    )
    ast = parse_layout_document(document)
    assert len(ast.diagnostics) == len(document["rows"])
    for entry in ast.diagnostics:
        assert entry["code"] in PARSER_CODES
        assert set(entry["span"]) == {"row", "slot_from", "slot_to"}
        assert isinstance(entry["span"]["row"], int)
        assert entry["span"]["slot_from"] == entry["span"]["slot_to"]
        assert entry["origin"]["candidate_id"] in {
            slot_document["candidate_id"]
            for row in document["rows"]
            for slot_document in row["slots"]
        }
        assert entry["message"]


def test_layout_diagnostics_are_not_copied_into_the_ast() -> None:
    """排列层的 LB-L0xx 属于 Layout；语法树只放 LB-P0xx（层次分明，运行记录里各层都在）。"""
    document = layout([slot(0, "c-0", "move-forward"), slot(1, "c-1", "number-1")])
    document["diagnostics"] = [
        {"code": "LB-L001", "severity": "warning", "message": "间距存疑", "candidate_ids": ["c-0", "c-1"]}
    ]
    ast = parse_layout_document(document)
    assert ast.diagnostics == ()


def test_parse_is_deterministic() -> None:
    document = layout(
        [slot(0, "c-0", "move-forward"), slot(1, "c-1", "number-1")],
        [slot(0, "c-2", "repeat")],
    )
    first = json.dumps(parse_layout_document(document).to_dict(), ensure_ascii=False)
    second = json.dumps(parse_layout_document(document).to_dict(), ensure_ascii=False)
    assert first == second


def test_parse_layout_accepts_lb004_layout_objects() -> None:
    """LB-004 的 ``Layout`` 对象可以直连解析器（同一个 Layout 走两条入口结果一致）。"""
    document = json.loads((LAYOUT_FIXTURES / "multi-row-cards.json").read_text(encoding="utf-8"))
    layout_object = layout_from_document(document)
    from_object = parse_layout(layout_object).to_dict()
    from_document = parse_layout_document(layout_object.to_dict()).to_dict()
    assert from_object == from_document
    assert from_object["root"]["body"] == []
    assert [entry["code"] for entry in from_object["diagnostics"]] == ["LB-P002", "LB-P004"]


@pytest.mark.parametrize(
    "broken",
    [
        {"kind": "Layout"},
        {"kind": "CardCandidateList", "schema_version": "0.0.1", "frame_id": "f", "rows": []},
        {
            "schema_version": "0.0.1",
            "kind": "Layout",
            "frame_id": "f",
            "reading_direction": "ltr_ttb",
            "rows": [{"row_index": 1, "slots": []}],
        },
        {
            "schema_version": "0.0.1",
            "kind": "Layout",
            "frame_id": "f",
            "reading_direction": "ltr_ttb",
            "rows": [
                {
                    "row_index": 0,
                    "slots": [
                        {
                            "slot_index": 3,
                            "candidate_id": "c-0",
                            "card_face": "move-forward",
                            "orientation_quadrant": 0,
                            "x_px": 1,
                            "y_px": 1,
                            "confidence": 0.5,
                        }
                    ],
                }
            ],
        },
    ],
)
def test_broken_layout_documents_raise_valueerror_instead_of_diagnostics(broken: dict) -> None:
    """输入本身不是合法 §4 文档 → ``ValueError``；语法层不为上游数据错误背锅。"""
    with pytest.raises(ValueError):
        parse_layout_document(broken)


def _layouts_from_candidate_fixtures() -> list[dict]:
    """用 LB-004 把 ``fixtures/layouts`` 的候选夹具过一遍，得到真实的 Layout 文档。"""
    layouts: list[dict] = []
    for path in sorted(LAYOUT_FIXTURES.glob("*.json")):
        candidates = json.loads(path.read_text(encoding="utf-8"))
        layouts.append(layout_from_document(candidates).to_dict())
    return layouts


def test_valid_documents_from_fixture_directory_are_accepted_by_validator() -> None:
    """解析器的输入校验口径与 LB-004 的 ``validate_layout`` 完全一致。"""
    for document in _layouts_from_candidate_fixtures():
        validate_layout(document)
        ast = parse_layout_document(document)
        validate_ast(ast.to_dict())


def test_card_role_covers_every_face_used_in_fixtures() -> None:
    """夹具里出现的卡面都必须有确定的文法角色（防止夹具偷偷引入未定义卡面）。"""
    seen: set[str] = set()
    for document in _layouts_from_candidate_fixtures():
        for row in document["rows"]:
            for slot_document in row["slots"]:
                seen.add(slot_document["card_face"])
    assert seen
    for face in seen:
        assert card_role(face) in {"command", "literal", "control", "operator", "unknown"}
