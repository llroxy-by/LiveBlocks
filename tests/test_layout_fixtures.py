"""``fixtures/layouts`` 的卡候选夹具 + Issue #11 的四条可观察验收（真跑一遍）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.layout import (
    DIAG_DUPLICATE_REMOVED,
    DIAG_IDENTITY_UNKNOWN,
    DIAG_SPACING_AMBIGUOUS,
    build_report,
    layout_from_card_list,
    layout_from_document,
    validate_layout,
)
from liveblocks.vision import validate_card_candidate_list

REPO_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_FIXTURES = REPO_ROOT / "fixtures" / "layouts"
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"

#: 入库的候选夹具（前三个覆盖验收点，第四个是真实识别输出里的「身份未知」路径）
REQUIRED_FIXTURES = (
    "multi-row-cards.json",
    "spacing-ambiguity.json",
    "duplicates.json",
    "overlap-cards.json",
)


def load(name: str) -> dict:
    return json.loads((LAYOUT_FIXTURES / name).read_text(encoding="utf-8"))


def test_required_layout_fixtures_exist_and_are_card_candidate_lists() -> None:
    for name in REQUIRED_FIXTURES:
        path = LAYOUT_FIXTURES / name
        assert path.is_file(), f"缺少候选夹具 {name}"
        document = load(name)
        validate_card_candidate_list(document)
        assert document["kind"] == "CardCandidateList"
        assert document["cards"], f"{name} 里没有候选"


def test_two_row_fixture_is_split_into_two_rows() -> None:
    layout = layout_from_document(load("multi-row-cards.json"))
    validate_layout(layout.to_dict())
    assert [row.candidate_ids for row in layout.rows] == [
        ("c-2", "c-1", "c-0"),
        ("c-4", "c-5", "c-3"),
    ]
    assert [slot.card_face for slot in layout.rows[0].slots] == ["move-forward", "number-1", "jump"]
    assert [slot.card_face for slot in layout.rows[1].slots] == ["repeat", "number-2", "end"]
    # 6 个候选一个不少，且没有多余诊断
    assert len(layout.slots) == 6
    assert layout.diagnostics == ()
    assert layout.basis["counts"]["placed"] == 6
    assert layout.reading_direction == "ltr_ttb"


def test_spacing_ambiguity_fixture_yields_l001() -> None:
    layout = layout_from_document(load("spacing-ambiguity.json"))
    entries = layout.diagnostics_with(DIAG_SPACING_AMBIGUOUS)
    assert len(entries) == 1
    assert entries[0]["code"] == "LB-L001"
    assert entries[0]["candidate_ids"] == ["c-1", "c-2"]
    assert "间距" in entries[0]["message"] and "存疑" in entries[0]["message"]
    # 间距歧义不改变排序，只标注存疑；四项都进槽位
    assert layout.candidate_order == ("c-0", "c-1", "c-2", "c-3")


def test_duplicate_fixture_dedupes_with_l002_trace() -> None:
    document = load("duplicates.json")
    layout = layout_from_document(document)
    assert layout.candidate_order == ("c-0", "c-2", "c-3")
    assert "c-1" not in layout.candidate_order

    entries = layout.diagnostics_with(DIAG_DUPLICATE_REMOVED)
    assert len(entries) == 1
    assert entries[0]["code"] == "LB-L002"
    assert entries[0]["candidate_ids"] == ["c-0", "c-1"]
    assert "移除 c-1" in entries[0]["message"]
    # 保留的是置信度更高的候选（0.92 > 0.61），且槽位里的置信度来自原候选
    assert layout.rows[0].slots[0].candidate_id == "c-0"
    assert layout.rows[0].slots[0].confidence == pytest.approx(0.92)
    assert layout.basis["counts"] == {
        "input": 4,
        "placed": 3,
        "removed_duplicates": 1,
        "excluded_unknown_identity": 0,
    }


def test_unknown_identity_fixture_is_traced_with_l003() -> None:
    """真实识别输出里「检出形状但码字不可读」的候选：不进排列，但必须留痕。"""
    layout = layout_from_document(load("overlap-cards.json"))
    assert layout.candidate_order == ("c-1",)
    entries = layout.diagnostics_with(DIAG_IDENTITY_UNKNOWN)
    assert len(entries) == 1
    assert entries[0]["candidate_ids"] == ["c-0"]
    assert "occluded" in entries[0]["message"]
    # 只剩一个可用候选，没有尺度可估——但它依然成了一个合法的槽位
    assert layout.basis["pitch_px"] is None
    validate_layout(layout.to_dict())


@pytest.mark.parametrize("name", REQUIRED_FIXTURES)
def test_every_fixture_layout_is_traceable(name: str) -> None:
    """「所有排序结论可追溯到候选 id」：槽位 id ⊆ 输入 id，且每个输入候选都有归属。"""
    document = load(name)
    layout = layout_from_document(document)
    input_ids: list[str] = [str(card["candidate_id"]) for card in document["cards"]]
    placed = list(layout.candidate_order)
    flagged = [
        candidate_id
        for entry in layout.diagnostics
        for candidate_id in entry.get("candidate_ids", [])
    ]
    assert set(placed) <= set(input_ids), "槽位里出现了输入中不存在的候选"
    assert set(input_ids) <= set(placed) | set(flagged), "有候选既没进槽位也没被诊断点名"
    # 每个槽位都带着自己的来源候选 id 与坐标
    for row in layout.rows:
        for slot in row.slots:
            assert slot.candidate_id in input_ids
            assert slot.slot_index < len(row.slots)
    validate_layout(layout.to_dict())


@pytest.mark.parametrize(
    ("scene", "fixture"),
    [("multi-row", "multi-row-cards.json"), ("overlap", "overlap-cards.json")],
)
def test_recognition_snapshots_match_fresh_recognition(recognized, scene: str, fixture: str) -> None:
    """入库的候选夹具就是 LB-003 的真跑输出（上游行为变了这里会失败）。"""
    result, _truth = recognized[scene]
    assert result.cards.to_dict() == load(fixture)


def test_end_to_end_from_image_to_layout(recognized) -> None:
    """「给定 fixtures 的卡片候选，输出 Layout」：识别 → 排列一条链跑通。"""
    result, _truth = recognized["multi-row"]
    layout = layout_from_card_list(result.cards)
    assert layout.frame_id == "f-multi-row"
    assert [row.candidate_ids for row in layout.rows] == [
        ("c-2", "c-1", "c-0"),
        ("c-4", "c-5", "c-3"),
    ]
    assert layout.diagnostics == ()
    validate_layout(layout.to_dict())


def test_report_acceptance_all_pass() -> None:
    report = build_report(LAYOUT_FIXTURES)
    assert len(report.fixtures) == len(REQUIRED_FIXTURES)
    results = report.acceptance()
    assert [ok for ok, _criterion, _evidence in results] == [True, True, True, True]
    text = report.format()
    for keyword in ("验收对照", "LB-L001", "LB-L002", "行 0"):
        assert keyword in text
    assert report.to_dict()["acceptance"][0]["ok"] is True
