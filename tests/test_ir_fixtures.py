"""``fixtures/ir`` 的 IR 夹具 + Issue #16 的可观察验收（真跑一遍）。

夹具清单在 ``fixtures/ir/index.json``：每项声明「几步、哪些 ``op``、哪些诊断码、``limits`` 是多少」，
golden ``Ir`` 由 ``liveblocks ir normalize --ast … --out …`` 生成。三个 ``from-*.json`` 的上游是
``fixtures/ast`` 的真实解析输出：本文件会用当前语法层重跑并逐字段比对——上游变了会立刻失败，
不会悄悄过期。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.ir import (
    IR_VERSION,
    build_report,
    load_manifest,
    load_schema,
    normalize_ast_document,
    validate_document,
    validate_ir,
)
from liveblocks.parser import parse_layout_document, validate_ast

REPO_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = REPO_ROOT / "fixtures" / "ir"
AST_DIR = REPO_ROOT / "fixtures" / "ast"

#: ``from-<语法夹具主干>.json`` → 生成它的语法夹具（真实上游快照）
PIPELINE_SOURCES = {
    "from-valid-basic.json": "valid-basic.json",
    "from-valid-empty.json": "valid-empty.json",
    "from-overlap-cards.json": "from-overlap-cards.json",
}


def manifest() -> dict:
    return load_manifest(IR_DIR)


def entries() -> list[dict]:
    return manifest()["fixtures"]


def load_ast(name: str) -> dict:
    return json.loads((IR_DIR / "asts" / f"{name}.json").read_text(encoding="utf-8"))


def load_golden(name: str) -> str:
    return (IR_DIR / "golden" / f"{name}.json").read_text(encoding="utf-8")


def test_manifest_is_complete_and_matches_files_on_disk() -> None:
    document = manifest()
    assert document["schema_version"] == "0.0.1"
    assert document["kind"] == "IrFixtureIndex"
    assert document["ir_version"] == IR_VERSION
    on_disk = {path.name for path in (IR_DIR / "asts").glob("*.json")}
    listed = {Path(entry["ast"]).name for entry in entries()}
    assert on_disk == listed, f"Ast 夹具与清单不一致：多 {sorted(on_disk - listed)} / 少 {sorted(listed - on_disk)}"
    goldens = {Path(entry["golden"]).name for entry in entries()}
    on_disk_golden = {path.name for path in (IR_DIR / "golden").glob("*.json")}
    assert on_disk_golden == goldens, "golden 目录与清单不一致"
    for entry in entries():
        assert isinstance(entry["steps"], int)
        assert all(isinstance(op, str) and op for op in entry["ops"])
        assert len(entry["ops"]) == entry["steps"], "ops 的条数必须与步骤数一致"
        assert isinstance(entry["limits"], dict)
        assert set(entry["limits"]) == {"max_steps", "timeout_ms"}


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_ast_fixtures_are_valid_section5_documents(entry: dict) -> None:
    validate_ast(load_ast(entry["name"]))


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_golden_snapshot_matches_fresh_normalization(entry: dict) -> None:
    """golden 文件快照：重跑规范化后必须与入库的 golden 逐字节一致。"""
    text = normalize_ast_document(load_ast(entry["name"])).canonical_text()
    assert text == load_golden(entry["name"])


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_normalization_is_stable_per_fixture(entry: dict) -> None:
    document = load_ast(entry["name"])
    first = normalize_ast_document(document).canonical_text()
    second = normalize_ast_document(document).canonical_text()
    assert first == second


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_declared_expectations_match(entry: dict) -> None:
    ir = normalize_ast_document(load_ast(entry["name"]))
    assert len(ir.steps) == entry["steps"]
    assert list(ir.ops) == list(entry["ops"])
    assert list(ir.codes()) == list(entry["codes"])
    assert ir.limits.to_dict() == entry["limits"]


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_golden_documents_pass_schema_and_semantic_validation(entry: dict) -> None:
    golden = json.loads(load_golden(entry["name"]))
    validate_document(golden, load_schema())
    validate_ir(golden)


def test_every_ir_code_appears_in_some_fixture() -> None:
    codes = {code for entry in entries() for code in entry["codes"]}
    assert codes == {"LB-I001", "LB-I002", "LB-I003", "LB-I004"}


def test_all_ops_fixture_covers_the_whole_op_table() -> None:
    from liveblocks.ir import OP_SPECS

    entry = next(item for item in entries() if item["name"] == "all-ops")
    assert list(entry["ops"]) == [spec.op for spec in OP_SPECS]


@pytest.mark.parametrize("name", sorted(PIPELINE_SOURCES))
def test_pipeline_fixtures_match_fresh_parse(name: str) -> None:
    """``from-*.json`` 的上游快照：用当前语法层重跑，逐字段比对入库的 Ast 夹具。"""
    layout = json.loads((AST_DIR / PIPELINE_SOURCES[name]).read_text(encoding="utf-8"))
    fresh = parse_layout_document(layout).to_dict()
    assert fresh == load_ast(name.replace(".json", ""))


def test_report_acceptance_is_all_green() -> None:
    report = build_report(IR_DIR)
    assert report.manifest_error == ""
    assert report.schema_unsupported == ()
    assert report.op_gaps == ()
    assert report.doc_gaps == ()
    failures = [
        f"{criterion}：{evidence}"
        for ok, criterion, evidence in report.acceptance()
        if not ok
    ]
    assert failures == []
    assert len(report.fixtures) == len(entries())


def test_report_serialises_to_json() -> None:
    report = build_report(IR_DIR)
    payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert len(payload["fixtures"]) == len(entries())
    assert all(item["actual"]["stable"] for item in payload["fixtures"])
    assert all(item["actual"]["golden_match"] for item in payload["fixtures"])
