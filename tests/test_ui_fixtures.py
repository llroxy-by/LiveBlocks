"""``fixtures/runs`` 的运行记录夹具：golden 哈希、上游快照一致、报告能抓出坏数据。

三类检查：

1. **golden**：每份记录的 HTML / 文本 / 叠加图哈希与 ``index.json`` 一致（改一行就失败）；
2. **上游快照**：样例记录的层文档必须等于「用当前各层代码重跑一遍」的结果
   （LB-003/004/005/006/007 任何一层变了都会立刻失败，和 LB-006/LB-007 的夹具测试同一思路）；
3. **反证**：把 golden 改一个字符、或把截图移走，报告必须判失败——证明检查不是永真式。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from liveblocks.ir import normalize_document
from liveblocks.layout import layout_from_card_list
from liveblocks.parser import parse_layout_document
from liveblocks.runtime import SimExecutor, ir_from_document
from liveblocks.ui.report import EVIDENCE_FILES, build_report, render_fixture
from liveblocks.ui.record import parse_record_file
from liveblocks.vision import recognize_path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "fixtures" / "runs"
INDEX = RUN_DIR / "index.json"


def _load_builder():
    spec = importlib.util.spec_from_file_location("lb_build_run_records", REPO_ROOT / "scripts/build_run_records.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


# --- 清单本身 -----------------------------------------------------------------


def test_index_lists_every_fixture_and_none_is_missing() -> None:
    manifest = _manifest()
    entries = manifest["fixtures"]
    assert manifest["kind"] == "RunRecordIndex"
    assert len(entries) == 9
    files = {entry["file"] for entry in entries}
    assert "sample-clipped.jsonl" in files
    assert "sample-multi-row.jsonl" in files
    assert any(entry["family"] == "corrupt" for entry in entries)
    for entry in entries:
        assert (RUN_DIR / entry["file"]).is_file(), entry["file"]
        assert entry["expect"]["hashes"], entry["name"]


def test_samples_are_five_layers_and_corrupt_ones_have_codes() -> None:
    for entry in _manifest()["fixtures"]:
        expect = entry["expect"]
        if entry["family"] == "sample":
            assert expect["layers"] == ["vision", "layout", "ast", "ir", "exec"], entry["name"]
            assert expect["events"] >= 1
        elif entry["family"] == "corrupt":
            assert expect["issue_codes"], entry["name"]


# --- golden 哈希 --------------------------------------------------------------


@pytest.mark.parametrize("entry", _manifest()["fixtures"], ids=[e["name"] for e in _manifest()["fixtures"]])
def test_rendered_artifacts_match_the_golden_hashes(entry: dict) -> None:
    fixture = render_fixture(
        RUN_DIR / entry["file"], expect=entry["expect"], name=entry["name"], family=entry["family"]
    )
    assert not fixture.error
    assert fixture.hashes_ok, fixture.hash_mismatches
    assert fixture.determinism[0], fixture.determinism[1]
    assert fixture.step_probe[0], fixture.step_probe[1]


# --- 上游快照 -----------------------------------------------------------------


def test_chain_samples_match_a_fresh_pipeline_run() -> None:
    builder = _load_builder()
    for scene, name, _note in builder.CHAIN_SAMPLES:
        fresh, _timings, _stats = builder.build_chain_lines(scene)
        recorded = [
            json.loads(line)
            for line in (RUN_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        body = [document for document in recorded if document.get("kind") != "RunHeader"]
        assert body == fresh, f"{name} 的层文档与重跑结果不同（上游层变了？）"


def test_exec_only_sample_matches_a_fresh_simulation_run() -> None:
    builder = _load_builder()
    for ir_name, name, _note in builder.EXEC_ONLY_SAMPLES:
        fresh, _timings, _stats = builder.build_exec_only_lines(ir_name)
        recorded = [
            json.loads(line)
            for line in (RUN_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        body = [document for document in recorded if document.get("kind") != "RunHeader"]
        assert body == fresh, f"{name} 的事件流/结果与重跑不同"


def test_record_layers_are_the_upstream_layer_outputs() -> None:
    """不经过脚本，直接用各层 API 重算一遍，确认记录里就是它们的产物。"""
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    image = REPO_ROOT / "fixtures" / "scenes" / "clipped.png"
    recognition = recognize_path(image, frame_id="f-clipped", source="synthetic")
    assert [card["candidate_id"] for card in record.cards] == [
        card.candidate_id for card in recognition.cards.cards
    ]
    fresh_layout = layout_from_card_list(recognition.cards)
    assert [slot["card_face"] for slot in record.rows[0]["slots"]] == [
        slot.card_face for slot in fresh_layout.rows[0].slots
    ]
    fresh_ast = parse_layout_document(fresh_layout.to_dict())
    assert record.ast_root == fresh_ast.to_dict()["root"]
    fresh_ir = normalize_document(fresh_ast.to_dict()).to_dict()
    assert [step["op"] for step in record.steps] == [step["op"] for step in fresh_ir["program"]["steps"]]
    fresh_run = SimExecutor(run_id="run-clipped").run(ir_from_document(fresh_ir))
    assert [event["phase"] for event in record.events] == [event["phase"] for event in fresh_run.events]


def test_builder_check_mode_accepts_the_committed_fixtures() -> None:
    builder = _load_builder()
    assert builder.check(RUN_DIR) == 0


def test_builder_check_mode_catches_a_tampered_record(tmp_path: Path) -> None:
    builder = _load_builder()
    copy = tmp_path / "runs"
    shutil.copytree(RUN_DIR, copy)
    target = copy / "sample-clipped.jsonl"
    lines = target.read_text(encoding="utf-8").splitlines()
    document = json.loads(lines[-1])
    document["steps_executed"] = 99
    lines[-1] = json.dumps(document, ensure_ascii=False)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert builder.check(copy) == 1


# --- 报告与四条验收 ------------------------------------------------------------


def test_report_passes_and_stays_within_the_time_bound() -> None:
    report = build_report(RUN_DIR)
    assert report.manifest_error == ""
    assert len(report.fixtures) == 9
    assert report.passes(), [(criterion, evidence) for ok, criterion, evidence in report.acceptance() if not ok]
    assert report.wall_ms < 60000.0
    assert report.doc_gaps == ()


def test_report_acceptance_covers_the_four_issue_criteria() -> None:
    report = build_report(RUN_DIR)
    criteria = [criterion for _ok, criterion, _evidence in report.acceptance()]
    assert len(criteria) == 4
    assert "五层" in criteria[0]
    assert "回放" in criteria[1]
    assert "可见提示" in criteria[2]
    assert "截图" in criteria[3]


def test_report_detects_a_broken_golden(tmp_path: Path) -> None:
    copy = tmp_path / "runs"
    shutil.copytree(RUN_DIR, copy)
    manifest_path = copy / "index.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["fixtures"]:
        if entry["name"] == "sample-clipped":
            entry["expect"]["hashes"]["html_final"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = build_report(copy)
    assert not report.passes()
    broken = [item for item in report.fixtures if item.name == "sample-clipped"]
    assert broken and broken[0].hash_mismatches


def test_report_detects_a_missing_screenshot(tmp_path: Path) -> None:
    from liveblocks.ui import report as report_module

    copy = tmp_path / "runs"
    shutil.copytree(RUN_DIR, copy)
    original = report_module.EVIDENCE_FILES
    report_module.EVIDENCE_FILES = (Path("docs/evidence/definitely-not-there.png"),)
    try:
        report = report_module.build_report(copy)
    finally:
        report_module.EVIDENCE_FILES = original
    assert report.evidence_problems
    assert not report.passes()


def test_committed_screenshots_exist_and_are_not_blank() -> None:
    from liveblocks.ui.evidence import screenshot_stats

    assert EVIDENCE_FILES, "证据清单不能为空"
    for relative in EVIDENCE_FILES:
        path = REPO_ROOT / relative
        assert path.is_file(), f"缺少截图证据 {relative}"
        stats = screenshot_stats(path.read_bytes())
        assert stats.non_blank, (relative, stats.describe())
        assert stats.width >= 1000 and stats.height >= 600


def test_evidence_stats_handle_a_blank_image() -> None:
    from liveblocks.synth.png import encode_gray
    from liveblocks.ui.evidence import screenshot_stats

    blank = encode_gray(64, 48, bytes([255] * (64 * 48)))
    stats = screenshot_stats(blank)
    assert not stats.non_blank
    assert stats.ink_ratio == 0.0
    assert stats.levels == 1
