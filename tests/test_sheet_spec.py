"""LB-032 版式规格：几何、静区规则、校验码与数字溯源表。

这一层不画像素，所以可以放心把「版面算得对不对」按毫米逐项断言。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from liveblocks.sheet.codes import CODES
from liveblocks.sheet.spec import (
    DEFAULT_BATCH_ID,
    DEFAULT_PIXELS_PER_MM,
    HUMAN_VERIFICATION,
    QUIET_ZONE_MODULES,
    CardSheetSpec,
    SheetContext,
    SheetError,
    all_numbers,
    generation_numbers,
    spec_numbers,
    unmeasured_items,
)


@pytest.fixture(scope="module")
def context() -> SheetContext:
    return SheetContext.load()


@pytest.fixture(scope="module")
def spec(context: SheetContext) -> CardSheetSpec:
    return CardSheetSpec.build(context=context)


def test_default_spec_is_one_a4_page_with_every_card(spec: CardSheetSpec) -> None:
    assert spec.paper == "A4"
    assert (spec.paper_width_mm, spec.paper_height_mm) == (210.0, 297.0)
    assert (spec.columns, spec.rows, spec.capacity, spec.page_count) == (3, 6, 18, 1)
    assert len(spec.cards) == 16
    assert (spec.width_px, spec.height_px) == (840, 1188)


def test_slot_grid_is_row_major_and_inside_the_page(spec: CardSheetSpec) -> None:
    for slot in spec.slots():
        x0, y0, x1, y1 = spec.slot_rect_mm(slot.index)
        assert 0.0 <= x0 < x1 <= spec.paper_width_mm
        assert 0.0 <= y0 < y1 <= spec.paper_height_mm
        assert (x1 - x0, y1 - y0) == (spec.layout.image_width_mm, spec.layout.image_height_mm)
        assert (slot.row, slot.col) == divmod(slot.index, spec.columns)
    first = spec.slot_rect_mm(0)
    second = spec.slot_rect_mm(1)
    assert second[0] - first[0] == pytest.approx(spec.layout.image_width_mm + spec.gutter_mm)


def test_quiet_zone_rule_is_the_larger_of_layout_and_one_module(spec: CardSheetSpec) -> None:
    assert spec.module_count == 6
    assert spec.module_mm == pytest.approx(4.0)
    assert spec.quiet_zone_face_mm == pytest.approx(2.5)
    assert spec.quiet_zone_rule_mm == pytest.approx(QUIET_ZONE_MODULES * spec.module_mm)
    # 卡面声明的静区比规则小：这是文档 §3 里如实写下的差异
    assert spec.quiet_zone_face_mm < spec.quiet_zone_rule_mm
    assert spec.gutter_mm >= spec.quiet_zone_rule_mm


def test_marker_and_quiet_zone_stay_inside_the_card_face(spec: CardSheetSpec) -> None:
    for index in range(len(spec.cards)):
        mx0, my0, mx1, my1 = spec.marker_rect_mm(index)
        qx0, qy0, qx1, qy1 = spec.quiet_zone_rect_mm(index)
        cx0, cy0, cx1, cy1 = spec.slot_rect_mm(index)
        assert cx0 <= qx0 < mx0 < mx1 < qx1 <= cx1
        assert cy0 <= qy0 < my0 < my1 < qy1 <= cy1
        assert (mx1 - mx0) == pytest.approx(spec.layout.marker_size_mm)


def test_orientation_mark_sits_in_the_lower_left_and_avoids_the_quiet_zone(spec: CardSheetSpec) -> None:
    for index in range(len(spec.cards)):
        ox0, oy0, ox1, oy1 = spec.orientation_mark_rect_mm(index)
        _cx0, _cy0, cx1, cy1 = spec.slot_rect_mm(index)
        qx0, qy0, qx1, qy1 = spec.quiet_zone_rect_mm(index)
        assert ox0 < cx1 and oy1 <= cy1  # 在卡面内、贴下边
        assert (ox1 - ox0, oy1 - oy0) == (spec.orientation_mark_mm, spec.orientation_mark_mm)
        # 与静区不相交（三角形不会污染标记四周的净白）
        assert ox1 <= qx0 or ox0 >= qx1 or oy1 <= qy0 or oy0 >= qy1


def test_card_ids_come_from_the_dictionary_in_order(spec: CardSheetSpec, context: SheetContext) -> None:
    assert [slot.card.card_id for slot in spec.slots()] == [
        card.card_id for card in context.dictionary.cards
    ]
    assert [slot.card.marker_id for slot in spec.slots()] == [
        card.marker_id for card in context.dictionary.cards
    ]


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"margin_mm": -1.0}, "LB-S001"),
        ({"gutter_mm": -1.0}, "LB-S001"),
        ({"pixels_per_mm": 0.5}, "LB-S001"),
        ({"batch_id": "   "}, "LB-S001"),
        ({"paper": "A5"}, "LB-S001"),
    ],
)
def test_invalid_layout_parameters_are_rejected(kwargs: dict, code: str) -> None:
    with pytest.raises(SheetError) as error:
        CardSheetSpec.build(**kwargs)
    assert error.value.code == code
    assert error.value.code in CODES


def test_gutter_smaller_than_the_quiet_zone_rule_is_rejected() -> None:
    with pytest.raises(SheetError) as error:
        CardSheetSpec.build(gutter_mm=3.0)
    assert error.value.code == "LB-S001"
    assert "静区" in error.value.message


def test_marker_plus_quiet_zone_must_fit_the_card_face(spec: CardSheetSpec) -> None:
    oversized = replace(spec, layout=replace(spec.layout, marker_quiet_zone_mm=30.0))
    with pytest.raises(SheetError) as error:
        oversized.validate()
    assert error.value.code == "LB-S002"


def test_page_that_cannot_fit_a_single_card_is_rejected() -> None:
    with pytest.raises(SheetError) as error:
        CardSheetSpec.build(margin_mm=100.0)
    assert error.value.code == "LB-S003"


def test_unknown_layout_or_card_id_is_rejected() -> None:
    with pytest.raises(SheetError):
        CardSheetSpec.build(layout_name="card-99x99")
    with pytest.raises(SheetError):
        CardSheetSpec.build(card_ids=["C01", "X99"])


def test_subset_of_cards_can_be_printed() -> None:
    subset = CardSheetSpec.build(card_ids=["C01", "C02", "O01"])
    assert [slot.card.card_id for slot in subset.slots()] == ["C01", "C02", "O01"]
    assert subset.page_count == 1


def test_traced_numbers_cover_every_layout_and_point_at_data_files(spec: CardSheetSpec, context: SheetContext) -> None:
    numbers = spec_numbers(spec, layouts=context.layouts)
    keys = {entry.key for entry in numbers}
    for name in context.layouts:
        assert f"card_size.{name}" in keys
        assert f"marker_size.{name}" in keys
        assert f"quiet_zone_face.{name}" in keys
        assert f"quiet_zone_rule.{name}" in keys
    for entry in numbers:
        assert entry.measured is True
        assert entry.kind in ("dictionary", "layout", "quiet_zone", "derived", "paper")
        assert entry.source
    assert {entry.value for entry in numbers if entry.key == "card_count"} == {"16"}


def test_traced_numbers_match_the_data_files(spec: CardSheetSpec, context: SheetContext) -> None:
    numbers = {entry.key: entry for entry in spec_numbers(spec, layouts=context.layouts)}
    aruco = context.aruco
    layout = context.layouts["card-60x40"]
    assert numbers["module_count"].value == f"{aruco.marker_modules + 2 * aruco.border_bits}×{aruco.marker_modules + 2 * aruco.border_bits}"
    assert numbers["marker_size.card-60x40"].value == f"{layout.marker_size_mm:g}"
    assert numbers["card_size.card-60x40"].value == f"{layout.image_width_mm:g}×{layout.image_height_mm:g}"
    assert numbers["quiet_zone_face.card-60x40"].value == f"{layout.marker_quiet_zone_mm:g}"
    assert numbers["columns"].value == str(spec.columns)
    assert numbers["rows"].value == str(spec.rows)
    assert numbers["capacity"].value == str(spec.capacity)
    assert numbers["page_count"].value == str(spec.page_count)


def test_generation_parameters_are_marked_unmeasured(spec: CardSheetSpec) -> None:
    generation = generation_numbers(spec)
    assert generation, "排版参数必须单独列出来，并且没有实测依据"
    assert all(entry.measured is False for entry in generation)
    assert all(entry.kind == "generation" for entry in generation)
    assert all("设计默认值" in entry.source for entry in generation)


def test_unmeasured_items_have_no_numeric_claim() -> None:
    items = unmeasured_items()
    assert items
    assert all(entry.measured is False for entry in items)
    assert all(entry.value == "—" for entry in items)
    labels = {entry.label for entry in items}
    assert any("基材" in label for label in labels)
    assert any("打印工艺" in label for label in labels)
    assert any("可识别距离" in label for label in labels)


def test_human_verification_entries_all_have_concrete_steps() -> None:
    assert len(HUMAN_VERIFICATION) >= 5
    for item, steps in HUMAN_VERIFICATION:
        assert item.strip()
        assert steps.strip()
        assert "①" in steps, f"「{item}」的人工步骤必须分步写"


def test_manifest_document_shape(spec: CardSheetSpec) -> None:
    document = spec.to_dict()
    assert document["layout"] == "card-60x40"
    assert document["batch_id"] == DEFAULT_BATCH_ID
    assert document["page"]["pixels_per_mm"] == DEFAULT_PIXELS_PER_MM
    assert document["quiet_zone"]["rule_mm"] == pytest.approx(4.0)
    assert len(document["cards"]) == len(spec.cards)
    first = document["cards"][0]
    assert first["serial"] == f"{DEFAULT_BATCH_ID}-p01-01"
    assert first["orientation_quadrant"] == 0
    assert len(first["rect_mm"]) == 4 and len(first["marker_rect_mm"]) == 4


def test_all_numbers_is_the_union_of_the_three_tables(spec: CardSheetSpec) -> None:
    with_layouts = spec_numbers(spec, layouts=SheetContext.load().layouts)
    expected = len(with_layouts) + len(generation_numbers(spec)) + len(unmeasured_items())
    assert len(all_numbers(spec)) == expected
