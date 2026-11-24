"""``docs/IR.md`` 与实现是否一致（含反向验证），以及文档/夹具索引的互相引用。

正向：``ir_document_gaps`` 对入库文档返回空列表。
反向：删掉一行 op 表 / 一个诊断码 / 一个标题 / ``limits`` 缺省值后必须报缺口——否则「文档与实现
一致」只是假通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.ir import (
    DIAGNOSTICS_BY_CODE,
    DOC_PATH,
    LIMIT_DEFAULTS,
    OP_SPECS,
    SCHEMA_FILENAME,
    ir_document_gaps,
    op_table_gaps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
IR_DOC = REPO_ROOT / DOC_PATH


def doc_text() -> str:
    return IR_DOC.read_text(encoding="utf-8")


def test_ir_doc_exists_and_is_linked() -> None:
    assert IR_DOC.is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    decisions = (REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    fixtures_readme = (REPO_ROOT / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "docs/IR.md" in readme
    assert "docs/IR.md" in decisions
    assert "docs/IR.md" in progress
    assert "fixtures/ir" in fixtures_readme


def test_ir_doc_has_no_gaps() -> None:
    assert ir_document_gaps(doc_text()) == []


def test_op_table_matches_card_dictionary() -> None:
    assert op_table_gaps() == []


def test_op_table_gap_is_detected_when_a_face_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import liveblocks.ir.ops as ops

    shortened = {face: spec for face, spec in ops.OP_BY_FACE.items() if face != "wait"}
    monkeypatch.setattr(ops, "OP_BY_FACE", shortened)
    gaps = ops.op_table_gaps()
    assert any("wait" in gap for gap in gaps)


def test_op_table_gap_is_detected_for_an_extra_face(monkeypatch: pytest.MonkeyPatch) -> None:
    import liveblocks.ir.ops as ops

    extended = dict(ops.OP_BY_FACE)
    extended["teleport"] = ops.OpSpec(card_face="teleport", op="teleport")
    monkeypatch.setattr(ops, "OP_BY_FACE", extended)
    gaps = ops.op_table_gaps()
    assert any("teleport" in gap for gap in gaps)


@pytest.mark.parametrize("spec", OP_SPECS, ids=[spec.op for spec in OP_SPECS])
def test_deleting_an_op_row_is_reported(spec) -> None:
    text = doc_text()
    # 找出该 op 在文档里的那一行并删掉
    lines = text.splitlines()
    kept = [
        line
        for line in lines
        if not (line.startswith("| `") and f"`{spec.card_face}`" in line and f"`{spec.op}`" in line)
    ]
    assert len(kept) < len(lines), f"{spec.op} 在文档里找不到对应表格行"
    gaps = ir_document_gaps("\n".join(kept))
    assert any(spec.card_face in gap for gap in gaps)


def test_deleting_a_diagnostic_code_is_reported() -> None:
    text = doc_text().replace(DIAGNOSTICS_BY_CODE["LB-I003"][0], "占位标题")
    gaps = ir_document_gaps(text)
    assert any("LB-I003" in gap for gap in gaps)


def test_deleting_a_limit_default_is_reported() -> None:
    text = doc_text().replace("1000", "999999", 1)
    gaps = ir_document_gaps(text)
    assert any("max_steps" in gap for gap in gaps)


def test_deleting_the_schema_reference_is_reported() -> None:
    text = doc_text().replace(SCHEMA_FILENAME, "某处/schema.json")
    gaps = ir_document_gaps(text)
    assert any("schema" in gap for gap in gaps)


def test_deleting_step_id_shape_is_reported() -> None:
    text = doc_text()
    stripped = "\n".join(line for line in text.splitlines() if "s0" not in line)
    gaps = ir_document_gaps(stripped)
    assert any("step_id" in gap for gap in gaps)


def test_limits_defaults_in_doc_match_code() -> None:
    text = doc_text()
    for key, value in LIMIT_DEFAULTS.items():
        assert f"`{key}`" in text
        assert str(value) in text


def test_fixture_manifest_and_report_agree_on_fixture_count() -> None:
    manifest = json.loads((REPO_ROOT / "fixtures" / "ir" / "index.json").read_text(encoding="utf-8"))
    goldens = {path.name for path in (REPO_ROOT / "fixtures" / "ir" / "golden").glob("*.json")}
    assert {Path(entry["golden"]).name for entry in manifest["fixtures"]} == goldens
