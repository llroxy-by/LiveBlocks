"""P0 文法（``grammar_version`` 0.0.1）的定义本身：卡面角色、字面量、诊断码与文档一致性。

这一组用例是「文法定义有独立文档并可被测试引用」的落点：它把 ``docs/GRAMMAR.md`` 当成**被测
对象**——文档漏写一条产生式、一个诊断码、一个卡面角色，测试就失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from liveblocks.parser import (
    BNF,
    DIAGNOSTICS_BY_CODE,
    DIAG_BAD_ARGUMENT,
    DIAG_CONTROL_UNSUPPORTED,
    DIAG_EMPTY_SLOT,
    DIAG_MISSING_COMMAND,
    DIAG_OPERATOR_UNSUPPORTED,
    DOC_PATH,
    GRAMMAR_VERSION,
    NODE_COMMAND,
    NODE_KINDS,
    NODE_NUMBER,
    NODE_PROGRAM,
    PARSER_CODES,
    ROLES,
    ROLE_BY_CARD_TYPE,
    ROLE_COMMAND,
    ROLE_CONTROL,
    ROLE_LITERAL,
    ROLE_OPERATOR,
    ROLE_UNKNOWN,
    card_role,
    card_types,
    faces_with_role,
    grammar_document_gaps,
    literal_value,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAMMAR_DOC = REPO_ROOT / DOC_PATH


def grammar_text() -> str:
    return GRAMMAR_DOC.read_text(encoding="utf-8")


def test_grammar_document_exists_and_is_reachable() -> None:
    assert GRAMMAR_DOC.is_file(), f"缺少文法文档 {GRAMMAR_DOC}"
    text = grammar_text()
    # 文档必须自带状态块与版本号：文法不是「藏在代码注释里的约定」。
    assert GRAMMAR_VERSION in text
    assert "草案" in text


def test_grammar_document_matches_machine_readable_definition() -> None:
    gaps = grammar_document_gaps(grammar_text())
    assert gaps == [], "文法文档与 src/liveblocks/parser/grammar.py 不一致：" + "；".join(gaps)


def test_grammar_document_gaps_reports_missing_code() -> None:
    """反向验证：删掉一个诊断码后核对函数必须报缺口（否则「一致」是假通过）。"""
    text = grammar_text().replace(DIAG_BAD_ARGUMENT, "")
    gaps = grammar_document_gaps(text)
    assert any(DIAG_BAD_ARGUMENT in gap for gap in gaps), gaps


def test_grammar_document_gaps_reports_missing_bnf_line() -> None:
    text = grammar_text().replace("Statement ::= Command Literal*", "Statement ::= ???")
    gaps = grammar_document_gaps(text)
    assert any("产生式" in gap for gap in gaps), gaps


def test_grammar_document_gaps_reports_missing_card_face() -> None:
    text = grammar_text().replace("| `number-5`", "| `n04`")
    gaps = grammar_document_gaps(text)
    assert any("number-5" in gap for gap in gaps), gaps


def test_bnf_matches_the_documented_productions() -> None:
    normalised = "\n".join(line.strip() for line in BNF.strip().splitlines())
    assert normalised.splitlines() == [
        "Program   ::= Statement*",
        "Statement ::= Command Literal*",
        "Command   ::= command_card",
        "Literal   ::= number_card",
    ]


def test_node_kinds_are_the_three_documented_ones() -> None:
    assert NODE_KINDS == (NODE_PROGRAM, NODE_COMMAND, NODE_NUMBER)
    for node in NODE_KINDS:
        assert node in grammar_text(), node


def test_every_dictionary_card_has_exactly_one_role() -> None:
    """卡片字典是唯一身份来源：每张卡都要有确定的文法角色。"""
    types = card_types()
    assert types, "卡片字典是空的"
    classified = {face for role in ROLES for face in faces_with_role(role)}
    assert set(types) == set(classified)
    for face, expected_type in types.items():
        assert ROLE_BY_CARD_TYPE[expected_type] in ROLES
        assert card_role(face) in ROLES


def test_command_and_literal_vocabulary() -> None:
    assert faces_with_role(ROLE_COMMAND) == (
        "drop",
        "jump",
        "move-back",
        "move-forward",
        "pick",
        "turn-left",
        "turn-right",
        "wait",
    )
    assert faces_with_role(ROLE_LITERAL) == ("number-1", "number-2", "number-3", "number-5")
    assert faces_with_role(ROLE_CONTROL) == ("end", "if", "repeat")
    assert faces_with_role(ROLE_OPERATOR) == ("operator-add",)
    assert {face: literal_value(face) for face in faces_with_role(ROLE_LITERAL)} == {
        "number-1": 1,
        "number-2": 2,
        "number-3": 3,
        "number-5": 5,
    }


@pytest.mark.parametrize("card_face", ["", "hologram", "number-", "Number-1", "operator-add-x"])
def test_unknown_faces_have_no_role(card_face: str) -> None:
    assert card_role(card_face) == ROLE_UNKNOWN
    assert literal_value(card_face) is None


def test_wait_is_a_statement_not_a_control_flow_card() -> None:
    """``wait`` 是 control 卡，但不引入分支/循环，P0 按普通语句处理（GRAMMAR.md §2.2）。"""
    assert card_types()["wait"] == "control"
    assert card_role("wait") == ROLE_COMMAND
    for face in ("repeat", "if", "end"):
        assert card_role(face) == ROLE_CONTROL


def test_parser_codes_are_in_the_p0xx_range() -> None:
    assert PARSER_CODES == tuple(sorted(DIAGNOSTICS_BY_CODE))
    assert PARSER_CODES == (
        DIAG_MISSING_COMMAND,
        DIAG_BAD_ARGUMENT,
        DIAG_EMPTY_SLOT,
        DIAG_CONTROL_UNSUPPORTED,
        DIAG_OPERATOR_UNSUPPORTED,
    )
    for code in PARSER_CODES:
        assert re.fullmatch(r"LB-P\d{3}", code), code
    # §9 的示例码 LB-P003 必须被复用，而不是新造一个同义码
    assert DIAG_EMPTY_SLOT == "LB-P003"


def test_every_code_has_a_title_and_a_meaning() -> None:
    for code, (title, meaning) in DIAGNOSTICS_BY_CODE.items():
        assert title and meaning
        assert code in grammar_text()
        assert title in grammar_text()
