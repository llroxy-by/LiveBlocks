"""``fixtures/ast`` 的语法夹具 + Issue #14 的三条可观察验收（真跑一遍）。

夹具清单在 ``fixtures/ast/index.json``：每项声明「期望合法 / 非法、几条语句、哪些诊断码」。
两个 ``from-*.json`` 是 LB-004 在 ``fixtures/layouts`` 上的**真实输出快照**，本文件会用当前排列层
重跑并逐字段比对——上游变了会立刻失败，不会悄悄过期。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.layout import layout_from_document, validate_layout
from liveblocks.parser import (
    GRAMMAR_VERSION,
    PARSER_CODES,
    build_report,
    load_manifest,
    node_count,
    parse_layout_document,
    validate_ast,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AST_DIR = REPO_ROOT / "fixtures" / "ast"
LAYOUT_FIXTURES = REPO_ROOT / "fixtures" / "layouts"

#: ``from-<候选夹具主干>`` → 生成它的候选夹具（真实上游快照）
PIPELINE_SOURCES = {
    "from-multi-row-cards.json": "multi-row-cards.json",
    "from-overlap-cards.json": "overlap-cards.json",
}


def manifest() -> dict:
    return load_manifest(AST_DIR)


def entries() -> list[dict]:
    return manifest()["fixtures"]


def load(name: str) -> dict:
    return json.loads((AST_DIR / name).read_text(encoding="utf-8"))


def test_manifest_is_complete_and_matches_files_on_disk() -> None:
    document = manifest()
    assert document["schema_version"] == "0.0.1"
    assert document["kind"] == "AstFixtureIndex"
    assert document["grammar_version"] == GRAMMAR_VERSION
    on_disk = {path.name for path in AST_DIR.glob("*.json")} - {"index.json"}
    listed = {entry["name"] for entry in entries()}
    assert on_disk == listed, f"磁盘与清单不一致：多 {sorted(on_disk - listed)} / 少 {sorted(listed - on_disk)}"
    for entry in entries():
        assert entry["expect"] in ("valid", "invalid")
        assert isinstance(entry["codes"], list)
        assert isinstance(entry["statements"], int)
        assert entry.get("source"), f"{entry['name']} 缺少 source（夹具必须可追溯）"


def test_manifest_covers_both_paths_and_the_grammar_codes() -> None:
    kinds = {entry["expect"] for entry in entries()}
    assert kinds == {"valid", "invalid"}, "合法与非法两条路径都要有夹具"
    covered = {code for entry in entries() for code in entry["codes"]}
    assert covered == set(PARSER_CODES), f"诊断码覆盖不全：缺 {sorted(set(PARSER_CODES) - covered)}"


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_fixture_input_is_a_valid_layout_document(entry: dict) -> None:
    document = load(entry["name"])
    validate_layout(document)  # 夹具本身必须是合法 §4 文档，否则测的就不是语法错误
    assert document["kind"] == "Layout"


@pytest.mark.parametrize("entry", entries(), ids=[entry["name"] for entry in entries()])
def test_fixture_parses_to_the_declared_outcome(entry: dict) -> None:
    ast = parse_layout_document(load(entry["name"]))
    document = ast.to_dict()
    validate_ast(document)

    assert len(document["root"]["body"]) == entry["statements"], entry["name"]
    assert [item["code"] for item in document["diagnostics"]] == entry["codes"], entry["name"]
    assert all(code in PARSER_CODES for code in ast.codes()), entry["name"]

    # 每个节点都带 span 与 origin（§5）
    for node in ast.nodes():
        assert set(node) >= {"node", "span", "origin"}, (entry["name"], node)
        assert set(node["span"]) == {"row", "slot_from", "slot_to"} or set(node["span"]) == {
            "row_from",
            "row_to",
            "slot_from",
            "slot_to",
        }
    # 每条诊断都定位到行 / 槽位 / 卡片
    for item in document["diagnostics"]:
        assert set(item["span"]) == {"row", "slot_from", "slot_to"}
        assert item["span"]["slot_from"] <= item["span"]["slot_to"]
        assert item["origin"], item["code"]
        assert item["severity"] == "error"

    if entry["expect"] == "valid":
        assert document["diagnostics"] == [], entry["name"]


@pytest.mark.parametrize("name", sorted(PIPELINE_SOURCES))
def test_pipeline_fixtures_match_fresh_arrangement(name: str) -> None:
    """``from-*.json`` 是 LB-004 的真实输出快照：用当前排列层重跑必须逐字段一致。"""
    candidates = json.loads((LAYOUT_FIXTURES / PIPELINE_SOURCES[name]).read_text(encoding="utf-8"))
    fresh = layout_from_document(candidates).to_dict()
    assert load(name) == fresh, f"{name} 与当前 LB-004 输出不一致，请重新生成夹具"


def test_valid_basic_fixture_covers_zero_one_and_two_arguments() -> None:
    ast = parse_layout_document(load("valid-basic.json"))
    assert [command.name for command in ast.statements] == ["move-forward", "turn-left", "jump"]
    assert [len(command.args) for command in ast.statements] == [1, 2, 0]
    assert [argument.value for command in ast.statements for argument in command.args] == [1, 2, 3]


def test_invalid_fixtures_never_raise() -> None:
    """「且不崩溃」：非法夹具只产出诊断，不抛异常。"""
    for entry in entries():
        if entry["expect"] != "invalid":
            continue
        ast = parse_layout_document(load(entry["name"]))  # 不抛异常即通过
        assert ast.diagnostics, entry["name"]


def test_report_passes_every_acceptance_criterion() -> None:
    report = build_report(AST_DIR)
    results = report.acceptance()
    assert len(results) == 3
    for ok, criterion, evidence in results:
        assert ok, f"验收未通过：{criterion} —— {evidence}"
    assert report.manifest_error == ""
    assert report.grammar_gaps == ()
    assert len(report.valid_fixtures) >= 2
    assert len(report.invalid_fixtures) >= 3
    assert sum(fixture.nodes for fixture in report.fixtures) > 0


def test_report_entries_are_traceable_to_cards() -> None:
    """每条诊断点名的候选 id 都能在输入 Layout 的槽位里找到（不凭空造卡）。"""
    for entry in entries():
        document = load(entry["name"])
        known = {
            slot_document["candidate_id"]
            for row in document["rows"]
            for slot_document in row["slots"]
        }
        ast = parse_layout_document(document)
        for item in ast.diagnostics:
            for candidate_id in item["candidate_ids"]:
                assert candidate_id in known, (entry["name"], candidate_id)
            assert item["origin"]["candidate_id"] in known, (entry["name"], item["origin"])


def test_report_json_payload_is_serialisable() -> None:
    payload = build_report(AST_DIR).to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert json.loads(text)["fixtures"]
    for fixture in payload["fixtures"]:
        assert fixture["ast"] is None or fixture["ast"]["kind"] == "Ast"
        assert isinstance(fixture["nodes"], int)


def test_node_count_matches_walking_the_tree() -> None:
    ast = parse_layout_document(load("valid-basic.json"))
    assert node_count(ast) == 1 + 3 + 3  # Program + 3 条 Command + 3 个 Number
