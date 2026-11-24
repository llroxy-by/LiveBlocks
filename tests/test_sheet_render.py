"""LB-032 标记页渲染：页面家具（页眉页脚 / 裁切线 / 朝向标记 / 静区）与确定性。

像素级的断言都写成「某个区域里有没有墨、墨在哪一毫米处」，而不是比对整张图——
整张图交给 golden（``tests/test_sheet_cli.py``）与确定性核对。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from liveblocks.sheet.render import (
    GUIDE_SHADE,
    PAGE_SHADE,
    render_page,
    render_sheet,
    rotate_quarter_turns,
)
from liveblocks.sheet.spec import CardSheetSpec
from liveblocks.synth.png import decode_gray


@pytest.fixture(scope="module")
def spec() -> CardSheetSpec:
    return CardSheetSpec.build()


@pytest.fixture(scope="module")
def image(spec: CardSheetSpec):
    return render_sheet(spec)


@pytest.fixture(scope="module")
def page(spec: CardSheetSpec):
    """整页光栅（渲染一次约 1 秒，多个用例共享）。"""
    return render_page(spec)


def _ink(raster, x0_mm: float, y0_mm: float, x1_mm: float, y1_mm: float, ppm: float, limit: int) -> int:
    """某块毫米矩形里灰度 < ``limit`` 的像素数。"""
    total = 0
    for y in range(int(y0_mm * ppm), int(y1_mm * ppm)):
        row = y * raster.width
        for x in range(int(x0_mm * ppm), int(x1_mm * ppm)):
            if raster.pixels[row + x] < limit:
                total += 1
    return total


def test_page_is_paper_white_apart_from_the_drawn_content(spec: CardSheetSpec, image) -> None:
    assert (image.width, image.height) == (spec.width_px, spec.height_px)
    width, height, pixels = decode_gray(image.png)
    assert (width, height) == (spec.width_px, spec.height_px)
    assert max(pixels) == PAGE_SHADE  # 有纯纸白的像素
    assert min(pixels) <= 20  # 卡面标签（版式 label_shade）用了最深的墨色
    # 最右下角（页脚右侧、最后一列卡之外）是纸白
    assert pixels[-1] == PAGE_SHADE


def test_header_and_footer_are_really_drawn(spec: CardSheetSpec, page) -> None:
    raster = page
    ppm = spec.pixels_per_mm
    assert _ink(raster, 0.0, 1.0, spec.paper_width_mm, 6.5, ppm, 120) > 500
    assert _ink(raster, 0.0, 288.0, spec.paper_width_mm, 296.0, ppm, 120) > 500


def test_cut_lines_sit_one_gap_outside_each_card(spec: CardSheetSpec, page) -> None:
    raster = page
    ppm = spec.pixels_per_mm
    x0, y0, _x1, _y1 = spec.slot_rect_mm(0)
    line_x = x0 - spec.cut_gap_mm
    # 卡片左侧 1 mm 处有一条灰线；卡片正边缘上是卡面自己的描边（更深）
    grey = [
        x
        for x in range(int((line_x - 1.0) * ppm), int((line_x + 1.0) * ppm))
        if raster.pixel(x, int((y0 + 20.0) * ppm)) == GUIDE_SHADE
    ]
    assert grey, "裁切线没画出来"
    assert min(abs(x / ppm - line_x) for x in grey) <= 0.5


def test_every_card_has_its_own_orientation_triangle(spec: CardSheetSpec, page) -> None:
    raster = page
    ppm = spec.pixels_per_mm
    for index in range(len(spec.cards)):
        x0, y0, x1, y1 = spec.orientation_mark_rect_mm(index)
        inside = _ink(raster, x0, y0, x1, y1, ppm, 60)
        # 3×3 mm 的三角形在 4 px/mm 下是 12×12 的格子，实心三角约占一半
        assert inside > 40
    # 三角形上尖下宽：贴底那一行的墨比顶点那一行多
    x0, y0, x1, y1 = spec.orientation_mark_rect_mm(0)
    apex_row = _ink(raster, x0, y0, x1, y0 + 0.25 * (y1 - y0), ppm, 60)
    base_row = _ink(raster, x0, y1 - 0.25 * (y1 - y0), x1, y1, ppm, 60)
    assert base_row > apex_row


def test_marker_quiet_zone_is_blank_inside_the_card(spec: CardSheetSpec, page) -> None:
    raster = page
    ppm = spec.pixels_per_mm
    for index in range(len(spec.cards)):
        mx0, my0, mx1, my1 = spec.marker_rect_mm(index)
        pad = spec.quiet_zone_face_mm
        # 静区里（标记外面一圈）不应出现深墨
        assert _ink(raster, mx0 - pad, my0 - pad, mx1 + pad, my0, ppm, 120) == 0
        assert _ink(raster, mx0 - pad, my1, mx1 + pad, my1 + pad, ppm, 120) == 0
        assert _ink(raster, mx0 - pad, my0, mx0, my1, ppm, 120) == 0
        assert _ink(raster, mx1, my0, mx1 + pad, my1, ppm, 120) == 0


def test_cards_are_rendered_with_the_layout_pixels_per_mm(spec: CardSheetSpec, page) -> None:
    texture_layout = replace(spec.layout, pixels_per_mm=int(spec.pixels_per_mm))
    assert texture_layout.width_px == int(round(spec.layout.image_width_mm * spec.pixels_per_mm))
    raster = page
    _x0, _y0, x1, y1 = spec.slot_rect_mm(0)
    ppm = spec.pixels_per_mm
    # 卡面里侧（避开描边、圆角、标记、标签）应当是卡面底色而不是纸白
    assert raster.pixel(int((x1 - 5.0) * ppm), int((y1 - 4.0) * ppm)) == spec.layout.background_shade
    # 卡面外侧（卡槽外 1 mm 之外）是纸白
    assert raster.pixel(int((x1 + 2.5) * ppm), int((y1 - 4.0) * ppm)) == PAGE_SHADE


def test_render_is_byte_identical_for_the_same_parameters(spec: CardSheetSpec) -> None:
    first = render_sheet(spec)
    second = render_sheet(CardSheetSpec.build())
    assert first.pixels == second.pixels
    assert first.png == second.png
    assert first.pdf == second.pdf
    assert first.manifest_json == second.manifest_json
    assert first.png_sha256 == first.manifest["raster"]["png_sha256"]
    assert first.pdf_sha256 == first.manifest["raster"]["pdf_sha256"]
    assert first.pixel_sha256 == first.manifest["raster"]["pixel_sha256"]


def test_different_parameters_change_the_bytes(spec: CardSheetSpec) -> None:
    base = render_sheet(spec)
    other_batch = render_sheet(CardSheetSpec.build(batch_id="lb-b0002"))
    other_ppm = render_sheet(CardSheetSpec.build(pixels_per_mm=5))
    assert base.png != other_batch.png
    assert base.png != other_ppm.png
    assert other_batch.manifest["batch_id"] == "lb-b0002"


def test_manifest_records_geometry_and_has_no_timestamp(spec: CardSheetSpec, image) -> None:
    document = image.manifest
    assert document["kind"] == "CardSheet"
    assert document["schema_version"] == "0.0.1"
    assert document["page"]["width_px"] == image.width
    assert document["raster"]["png_bytes"] == len(image.png)
    assert len(document["cards"]) == len(spec.cards)
    assert document["unmeasured"], "待实测项必须进清单"
    assert document["human_verification"]
    text = image.manifest_json.lower()
    for forbidden in ("created_at", "timestamp", "generated_at"):
        assert forbidden not in text


@pytest.mark.parametrize("turns", [0, 1, 2, 3, 4])
def test_rotate_quarter_turns_keeps_every_pixel(turns: int) -> None:
    width, height = 5, 3
    pixels = bytes((x + y * width) % 256 for x in range(width) for y in range(height))
    out_w, out_h, out = rotate_quarter_turns(width, height, pixels, turns)
    expected_shape = (height, width) if turns % 2 else (width, height)
    assert (out_w, out_h) == expected_shape
    assert len(out) == width * height
    assert sorted(out) == sorted(pixels)


def test_rotate_quarter_turns_moves_the_corner_clockwise() -> None:
    # 2×2 图：左上角是 1、右上角是 2、左下 3、右下 4
    pixels = bytes([1, 2, 3, 4])
    _w, _h, out = rotate_quarter_turns(2, 2, pixels, 1)
    assert tuple(out) == (3, 1, 4, 2)


def test_rotated_page_still_has_the_same_ink_histogram(spec: CardSheetSpec, image) -> None:
    width, height, pixels = rotate_quarter_turns(image.width, image.height, image.pixels, 1)
    assert (width, height) == (image.height, image.width)
    assert sorted(pixels) == sorted(image.pixels)
