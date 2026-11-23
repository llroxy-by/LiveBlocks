"""§4 ``Layout`` 的数据模型、诊断码与最小校验。"""

from __future__ import annotations

import json

import pytest

from liveblocks.layout import (
    DIAG_DUPLICATE_REMOVED,
    DIAG_IDENTITY_UNKNOWN,
    DIAG_NOT_UPRIGHT,
    DIAG_ROW_BOUNDARY,
    DIAG_SPACING_AMBIGUOUS,
    LAYOUT_CODES,
    READING_DIRECTIONS,
    Layout,
    LayoutRow,
    LayoutSlot,
    validate_layout,
)

SLOT_KEYS = {
    "slot_index",
    "candidate_id",
    "card_face",
    "orientation_quadrant",
    "x_px",
    "y_px",
    "confidence",
}


def sample_layout() -> Layout:
    return Layout(
        frame_id="f-unit",
        reading_direction="ltr_ttb",
        rows=(
            LayoutRow(
                row_index=0,
                slots=(
                    LayoutSlot(0, "c-0", "move-forward", 0, 100, 200, 0.9),
                    LayoutSlot(1, "c-1", "number-1", 0, 180, 202, 0.8),
                ),
            ),
            LayoutRow(row_index=1, slots=(LayoutSlot(0, "c-2", "jump", 0, 100, 280, 0.7),)),
        ),
        diagnostics=(
            {
                "code": DIAG_SPACING_AMBIGUOUS,
                "severity": "warning",
                "message": "候选 c-0 与 c-1 间距低于阈值，排列顺序存疑",
                "candidate_ids": ["c-0", "c-1"],
            },
        ),
        basis={"pitch_px": 80.0},
    )


def test_layout_document_matches_section4_shape() -> None:
    document = sample_layout().to_dict()
    assert document["schema_version"] == "0.0.1"
    assert document["kind"] == "Layout"
    assert document["frame_id"] == "f-unit"
    assert document["reading_direction"] == "ltr_ttb"
    assert [row["row_index"] for row in document["rows"]] == [0, 1]
    assert [slot["slot_index"] for slot in document["rows"][0]["slots"]] == [0, 1]
    # §4 的槽位字段一个不多一个不少；坐标是整数像素（§0）
    for row in document["rows"]:
        for slot in row["slots"]:
            assert set(slot) == SLOT_KEYS
            assert isinstance(slot["x_px"], int) and isinstance(slot["y_px"], int)
            assert 0 <= slot["orientation_quadrant"] <= 3
            assert 0.0 <= slot["confidence"] <= 1.0
    # 「排序结论可追溯到候选 id」：按行→槽位展开的 id 序列
    assert document["basis"]["pitch_px"] == 80.0
    validate_layout(document)


def test_layout_helpers_expose_order_and_diagnostics() -> None:
    layout = sample_layout()
    assert layout.candidate_order == ("c-0", "c-1", "c-2")
    assert [slot.candidate_id for slot in layout.slots] == ["c-0", "c-1", "c-2"]
    assert layout.codes() == (DIAG_SPACING_AMBIGUOUS,)
    assert layout.diagnostics_with(DIAG_SPACING_AMBIGUOUS)[0]["candidate_ids"] == ["c-0", "c-1"]
    assert layout.diagnostics_with(DIAG_DUPLICATE_REMOVED) == ()
    assert layout.rows[0].candidate_ids == ("c-0", "c-1")


def test_layout_document_is_json_serialisable() -> None:
    document = sample_layout().to_dict()
    text = json.dumps(document, ensure_ascii=False)
    assert json.loads(text) == document


def test_layout_codes_are_all_in_layout_range() -> None:
    assert LAYOUT_CODES == (
        DIAG_IDENTITY_UNKNOWN,
        DIAG_DUPLICATE_REMOVED,
        DIAG_ROW_BOUNDARY,
        DIAG_SPACING_AMBIGUOUS,
        DIAG_NOT_UPRIGHT,
    )
    # §9 的 `LB-L0xx` 区间；LB-L001 / LB-L002 是协议已定义的那两个
    assert {DIAG_SPACING_AMBIGUOUS, DIAG_DUPLICATE_REMOVED} == {"LB-L001", "LB-L002"}
    for code in LAYOUT_CODES:
        assert code.startswith("LB-L0")


def test_reading_directions_are_the_protocol_three() -> None:
    assert set(READING_DIRECTIONS) == {"ltr_ttb", "rtl_ttb", "ttb_ltr"}


def _mutated(mutator) -> dict:
    document = sample_layout().to_dict()
    mutator(document)
    return document


@pytest.mark.parametrize(
    ("name", "mutator"),
    [
        ("kind", lambda doc: doc.update({"kind": "CardCandidateList"})),
        ("schema_version", lambda doc: doc.update({"schema_version": "v1"})),
        ("frame_id", lambda doc: doc.update({"frame_id": 7})),
        ("reading_direction", lambda doc: doc.update({"reading_direction": "top-down"})),
        ("rows_type", lambda doc: doc.update({"rows": {}})),
        ("row_index", lambda doc: doc["rows"][1].update({"row_index": 5})),
        ("slot_index", lambda doc: doc["rows"][0]["slots"][1].update({"slot_index": 4})),
        (
            "duplicate_candidate",
            lambda doc: doc["rows"][1]["slots"][0].update({"candidate_id": "c-0"}),
        ),
        ("card_face", lambda doc: doc["rows"][0]["slots"][0].update({"card_face": None})),
        (
            "orientation_quadrant",
            lambda doc: doc["rows"][0]["slots"][0].update({"orientation_quadrant": 4}),
        ),
        ("x_px_float", lambda doc: doc["rows"][0]["slots"][0].update({"x_px": 1.5})),
        ("y_px_bool", lambda doc: doc["rows"][0]["slots"][0].update({"y_px": True})),
        ("confidence", lambda doc: doc["rows"][0]["slots"][0].update({"confidence": 1.4})),
        (
            "diagnostic_severity",
            lambda doc: doc["diagnostics"][0].update({"severity": "fatal"}),
        ),
        ("diagnostic_missing_code", lambda doc: doc["diagnostics"][0].pop("code")),
        ("diagnostic_ids_type", lambda doc: doc["diagnostics"][0].update({"candidate_ids": "c-0"})),
        ("basis", lambda doc: doc.update({"basis": "none"})),
    ],
)
def test_validate_layout_rejects_violations(name: str, mutator) -> None:
    with pytest.raises(ValueError):
        validate_layout(_mutated(mutator))


def test_validate_layout_tolerates_unknown_fields() -> None:
    """§0：消费者必须容忍新增字段（未知字段忽略但保留）。"""
    document = sample_layout().to_dict()
    document["future_field"] = {"anything": [1, 2, 3]}
    document["rows"][0]["slots"][0]["future_slot_field"] = "x"
    validate_layout(document)


def test_layout_without_basis_is_still_valid() -> None:
    document = sample_layout().to_dict()
    document.pop("basis")
    validate_layout(document)
    assert sample_layout().to_dict()["basis"] == {"pitch_px": 80.0}
