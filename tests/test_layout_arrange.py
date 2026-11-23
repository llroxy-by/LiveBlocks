"""排列算法的单元测试：分簇、排序方向、朝向归一、去重、歧义诊断、确定性。"""

from __future__ import annotations

import json

import pytest

from liveblocks.layout import (
    DIAG_DUPLICATE_REMOVED,
    DIAG_IDENTITY_UNKNOWN,
    DIAG_NOT_UPRIGHT,
    DIAG_ROW_BOUNDARY,
    DIAG_SPACING_AMBIGUOUS,
    Candidate,
    LayoutOptions,
    READING_FRAMES,
    build_layout,
    dedupe,
    estimate_pitch,
    validate_layout,
)


def cand(
    candidate_id: str,
    x: float,
    y: float,
    *,
    face: str = "move-forward",
    card_id: str | None = None,
    quadrant: int | None = 0,
    confidence: float = 0.9,
    status: str = "ok",
) -> Candidate:
    entry: dict = {
        "candidate_id": candidate_id,
        "card_face": face,
        "center_px": [x, y],
        "confidence": confidence,
        "status": status,
    }
    if card_id is not None:
        entry["card_id"] = card_id
    if quadrant is not None:
        entry["orientation_quadrant"] = quadrant
    return Candidate.from_dict(entry)


def rows_of(layout) -> list[list[str]]:
    return [[slot.candidate_id for slot in row.slots] for row in layout.rows]


def codes(layout) -> list[str]:
    return list(layout.codes())


def test_pitch_is_median_nearest_neighbour_distance() -> None:
    candidates = [cand("c-0", 0, 0), cand("c-1", 100, 0), cand("c-2", 200, 0)]
    assert estimate_pitch(candidates) == 100.0
    assert estimate_pitch(candidates[:1]) is None
    assert estimate_pitch([cand("a", 0, 0), cand("b", 0, 0)]) is None


def test_single_row_is_sorted_left_to_right() -> None:
    layout = build_layout(
        [cand("c-0", 300, 200, face="jump"), cand("c-1", 100, 200), cand("c-2", 200, 202)],
        frame_id="f-unit",
    )
    assert rows_of(layout) == [["c-1", "c-2", "c-0"]]
    assert layout.candidate_order == ("c-1", "c-2", "c-0")
    assert set(layout.candidate_order) == {"c-0", "c-1", "c-2"}
    assert codes(layout) == []  # 间距正常时不应有歧义诊断
    validate_layout(layout.to_dict())


def test_two_rows_are_split_and_ordered_top_to_bottom() -> None:
    candidates = [
        cand("c-0", 100, 300, face="repeat"),
        cand("c-1", 200, 305, face="number-2"),
        cand("c-2", 100, 100),
        cand("c-3", 200, 102, face="number-1"),
    ]
    layout = build_layout(candidates)
    assert rows_of(layout) == [["c-2", "c-3"], ["c-0", "c-1"]]
    assert [row.row_index for row in layout.rows] == [0, 1]
    # 每行都带候选 id 与几何依据，可追溯
    assert layout.basis["rows"][0]["candidate_ids"] == ["c-2", "c-3"]
    assert layout.basis["rows"][1]["order_span_px"] == [100.0, 200.0]
    assert layout.basis["counts"] == {
        "input": 4,
        "placed": 4,
        "removed_duplicates": 0,
        "excluded_unknown_identity": 0,
    }


def test_rtl_ttb_reverses_slot_order_but_keeps_rows() -> None:
    candidates = [cand("c-0", 100, 100), cand("c-1", 200, 100), cand("c-2", 300, 100)]
    layout = build_layout(candidates, options=LayoutOptions(reading_direction="rtl_ttb"))
    assert layout.reading_direction == "rtl_ttb"
    assert rows_of(layout) == [["c-2", "c-1", "c-0"]]
    # 行内仍是同一行，且各项坐标保留
    assert [slot.x_px for slot in layout.rows[0].slots] == [300, 200, 100]


def test_ttb_ltr_reads_columns_top_to_bottom() -> None:
    """``ttb_ltr``：一「行」在画面上是一列，行内沿 y 推进，行与行沿 x 推进。"""
    candidates = [
        cand("c-0", 300, 100, face="jump", quadrant=1),
        cand("c-1", 300, 200, face="end", quadrant=1),
        cand("c-2", 100, 120, quadrant=1),
        cand("c-3", 100, 220, face="number-1", quadrant=1),
    ]
    layout = build_layout(candidates, options=LayoutOptions(reading_direction="ttb_ltr"))
    assert rows_of(layout) == [["c-2", "c-3"], ["c-0", "c-1"]]
    assert layout.basis["reading_frame"]["line_axis"] == "x"
    assert layout.basis["reading_frame"]["order_axis"] == "y"
    # 竖直阅读下「正立」= 卡片顺时针转 90°（注册象限 1），此时不该报 LB-L005
    assert codes(layout) == []


def test_orientation_is_normalised_against_reading_direction() -> None:
    # 水平阅读：正立象限 0，注册象限 2 的卡在排列层是 2（非正立）
    flat = build_layout([cand("c-0", 100, 100, quadrant=2)])
    assert flat.rows[0].slots[0].orientation_quadrant == 2
    assert codes(flat) == [DIAG_NOT_UPRIGHT]
    assert flat.diagnostics_with(DIAG_NOT_UPRIGHT)[0]["candidate_ids"] == ["c-0"]

    # 竖直阅读：正立象限 1，因此注册象限 1 → 0；注册象限 0 → 3
    upright = build_layout(
        [cand("c-0", 100, 100, quadrant=1)], options=LayoutOptions(reading_direction="ttb_ltr")
    )
    assert upright.rows[0].slots[0].orientation_quadrant == 0
    assert codes(upright) == []
    turned = build_layout(
        [cand("c-0", 100, 100, quadrant=0)], options=LayoutOptions(reading_direction="ttb_ltr")
    )
    assert turned.rows[0].slots[0].orientation_quadrant == 3
    assert codes(turned) == [DIAG_NOT_UPRIGHT]


def test_missing_orientation_falls_back_to_zero_but_only_for_known_identity() -> None:
    layout = build_layout([cand("c-0", 100, 100, quadrant=None)])
    assert layout.rows[0].slots[0].orientation_quadrant == 0


def test_duplicate_candidates_are_removed_with_l002() -> None:
    candidates = [
        cand("c-0", 100, 100, card_id="C01", confidence=0.72),
        cand("c-1", 104, 103, card_id="C01", confidence=0.93),  # 同一张卡的重叠检出
        cand("c-2", 300, 100, card_id="N01", face="number-1"),
        cand("c-3", 500, 100, card_id="C05", face="jump"),
    ]
    layout = build_layout(candidates)
    assert layout.candidate_order == ("c-1", "c-2", "c-3")
    assert layout.basis["counts"]["removed_duplicates"] == 1
    entries = layout.diagnostics_with(DIAG_DUPLICATE_REMOVED)
    assert len(entries) == 1
    assert entries[0]["candidate_ids"] == ["c-1", "c-0"]
    assert "c-0" in entries[0]["message"] and "c-1" in entries[0]["message"]
    # 保留的是置信度更高的那个，且置信度写进了槽位
    assert layout.rows[0].slots[0].confidence == pytest.approx(0.93)


def test_same_face_far_apart_is_not_a_duplicate() -> None:
    """同一种卡面在桌面上摆两张是合法的（不重合就不去重）。"""
    candidates = [
        cand("c-0", 100, 100, card_id="N01", face="number-1"),
        cand("c-1", 300, 100, card_id="N01", face="number-1"),
        cand("c-2", 500, 100, card_id="C01"),
    ]
    layout = build_layout(candidates)
    assert layout.candidate_order == ("c-0", "c-1", "c-2")
    assert layout.basis["counts"]["removed_duplicates"] == 0
    assert DIAG_DUPLICATE_REMOVED not in codes(layout)


def test_dedupe_keeps_input_order_and_reports_diagnostics() -> None:
    kept, diagnostics = dedupe(
        [cand("c-0", 0, 0, card_id="C01"), cand("c-1", 1, 1, card_id="C01")], duplicate_px=10.0
    )
    assert [c.candidate_id for c in kept] == ["c-0"]
    assert diagnostics[0]["code"] == DIAG_DUPLICATE_REMOVED
    assert diagnostics[0]["candidate_ids"] == ["c-0", "c-1"]


def test_spacing_ambiguity_emits_l001_with_both_ids() -> None:
    candidates = [
        cand("c-0", 100, 100),
        cand("c-1", 200, 100, face="number-1"),
        cand("c-2", 235, 100, face="jump"),  # 与 c-1 只差 35px（卡距 100）
        cand("c-3", 400, 100, face="repeat"),
    ]
    layout = build_layout(candidates)
    entries = layout.diagnostics_with(DIAG_SPACING_AMBIGUOUS)
    assert len(entries) == 1
    assert entries[0]["candidate_ids"] == ["c-1", "c-2"]
    assert entries[0]["severity"] == "warning"
    assert "间距" in entries[0]["message"]
    # 低间距不影响排序（仍是左→右），但结论因此被标注为存疑
    assert layout.candidate_order == ("c-0", "c-1", "c-2", "c-3")


def test_explicit_pitch_controls_thresholds() -> None:
    candidates = [cand("c-0", 100, 100), cand("c-1", 170, 100, face="number-1")]
    # 自适应尺度只有这一对（pitch = 70），间距 70 不小于 0.6×70，故无诊断
    assert codes(build_layout(candidates)) == []
    # 显式给「预期卡距 200px」后，70px 就明显偏小 → LB-L001
    layout = build_layout(candidates, options=LayoutOptions(pitch_px=200.0))
    assert codes(layout) == [DIAG_SPACING_AMBIGUOUS]
    assert layout.basis["thresholds_px"]["slot_gap"] == 120.0


def test_unknown_identity_candidates_are_excluded_but_traced() -> None:
    candidates = [
        cand("c-0", 100, 100, face="unknown", card_id="unknown", quadrant=None, status="occluded"),
        cand("c-1", 200, 100, face="unknown", card_id="unknown", quadrant=None, status="ambiguous"),
        cand("c-2", 300, 100, face="move-forward", card_id="C01"),
    ]
    layout = build_layout(candidates)
    assert layout.candidate_order == ("c-2",)
    entries = layout.diagnostics_with(DIAG_IDENTITY_UNKNOWN)
    assert len(entries) == 1
    assert entries[0]["candidate_ids"] == ["c-0", "c-1"]
    assert "occluded" in entries[0]["message"] and "ambiguous" in entries[0]["message"]
    assert layout.basis["counts"] == {
        "input": 3,
        "placed": 1,
        "removed_duplicates": 0,
        "excluded_unknown_identity": 2,
    }


def test_clipped_cards_still_take_part_in_the_arrangement() -> None:
    candidates = [
        cand("c-0", 100, 100, card_id="C01", status="clipped"),
        cand("c-1", 300, 100, card_id="N01", face="number-1"),
    ]
    layout = build_layout(candidates)
    assert layout.candidate_order == ("c-0", "c-1")
    assert codes(layout) == []


def test_row_boundary_near_threshold_emits_l004() -> None:
    """两行的间隙刚过切分阈值 → 行分组存疑（``LB-L004``）。"""
    candidates = [
        cand("c-0", 100, 0, card_id="C01"),
        cand("c-1", 300, 0, card_id="N01", face="number-1"),
        cand("c-2", 100, 160, card_id="C05", face="jump"),
        cand("c-3", 300, 160, card_id="C08", face="repeat"),
    ]
    options = LayoutOptions(pitch_px=200.0)  # row_gap = 150，row_ambiguous = 180
    layout = build_layout(candidates, options=options)
    assert len(layout.rows) == 2
    entries = layout.diagnostics_with(DIAG_ROW_BOUNDARY)
    assert len(entries) == 1
    assert entries[0]["candidate_ids"] == ["c-2", "c-0"] or entries[0]["candidate_ids"] == [
        "c-0",
        "c-2",
    ]
    assert "行分组存疑" in entries[0]["message"]


def test_merged_rows_span_emits_l004() -> None:
    """一行内的换行轴跨度超过典型卡距 → 可能把两行并成了一行（``LB-L004``）。"""
    candidates = [
        cand("c-0", 100, 0, card_id="C01"),
        cand("c-1", 100, 100, card_id="N01", face="number-1"),
        cand("c-2", 100, 210, card_id="C05", face="jump"),
    ]
    options = LayoutOptions(pitch_px=200.0)  # row_gap = 150（相邻差 ≤ 150 不切行），row_span = 200
    layout = build_layout(candidates, options=options)
    assert len(layout.rows) == 1
    entries = layout.diagnostics_with(DIAG_ROW_BOUNDARY)
    assert len(entries) == 1
    assert set(entries[0]["candidate_ids"]) == {"c-0", "c-2"}
    assert "并成了一行" in entries[0]["message"]


def test_empty_input_produces_empty_layout() -> None:
    layout = build_layout([], frame_id="f-empty")
    document = layout.to_dict()
    validate_layout(document)
    assert document["rows"] == []
    assert document["diagnostics"] == []
    assert layout.basis["pitch_px"] is None
    assert layout.basis["counts"]["input"] == 0


def test_single_candidate_has_no_scale_and_no_ambiguity() -> None:
    layout = build_layout([cand("c-0", 100, 100)], frame_id="f-one")
    assert rows_of(layout) == [["c-0"]]
    assert layout.basis["pitch_px"] is None
    assert layout.basis["thresholds_px"] == {}
    assert codes(layout) == []


def test_layout_is_deterministic() -> None:
    candidates = [
        cand("c-0", 100, 100, card_id="C01"),
        cand("c-1", 104, 101, card_id="C01", confidence=0.5),
        cand("c-2", 300, 100, card_id="N01", face="number-1"),
        cand("c-3", 100, 260, card_id="C05", face="jump"),
        cand("c-4", 300, 262, card_id="C08", face="repeat"),
    ]
    first = json.dumps(build_layout(candidates, frame_id="f-order").to_dict(), ensure_ascii=False)
    second = json.dumps(build_layout(candidates, frame_id="f-order").to_dict(), ensure_ascii=False)
    assert first == second


def test_layout_options_are_validated() -> None:
    with pytest.raises(ValueError):
        LayoutOptions(reading_direction="diagonal").validate()
    with pytest.raises(ValueError):
        LayoutOptions(pitch_px=0.0).validate()
    with pytest.raises(ValueError):
        LayoutOptions(slot_gap_ratio=-1.0).validate()
    with pytest.raises(ValueError):
        build_layout([cand("c-0", 0, 0)], options=LayoutOptions(reading_direction="diagonal"))


def test_reading_frames_cover_every_direction() -> None:
    assert set(READING_FRAMES) == {"ltr_ttb", "rtl_ttb", "ttb_ltr"}
    for frame in READING_FRAMES.values():
        assert frame.line_axis != frame.order_axis
        assert frame.order_sign in (-1, 1)
        assert 0 <= frame.upright_quadrant <= 3
