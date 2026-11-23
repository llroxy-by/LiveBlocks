"""卡面纹理测试：尺寸、圆角掩码、标记位图案、静区干净、标签有墨。"""

from __future__ import annotations

import pytest

from liveblocks.synth import SynthContext
from liveblocks.synth.raster import INSIDE, OUTSIDE
from liveblocks.synth.texture import render_card_texture, MARKER_INK


@pytest.fixture(scope="module")
def rendered(context: SynthContext):
    layout = context.layouts["card-60x40"]
    card = context.card_dictionary.card("C01")
    return layout, card, render_card_texture(card, layout, context.aruco, context.font)


def test_texture_size_and_mask(rendered) -> None:
    layout, _, texture = rendered
    assert (texture.width, texture.height) == (layout.width_px, layout.height_px)
    assert (layout.width_px, layout.height_px) == (600, 400)
    assert len(texture.mask) == texture.width * texture.height
    # 圆角：四角外侧不属于卡片，中心属于卡片
    assert texture.mask[0] == OUTSIDE
    assert texture.mask[texture.width - 1] == OUTSIDE
    assert texture.mask[(texture.height - 1) * texture.width] == OUTSIDE
    center = (texture.height // 2) * texture.width + texture.width // 2
    assert texture.mask[center] == INSIDE
    assert sum(1 for value in texture.mask if value == INSIDE) > 0.9 * len(texture.mask)


def test_marker_modules_match_dictionary(rendered, context: SynthContext) -> None:
    layout, card, texture = rendered
    ppm = layout.pixels_per_mm
    modules = context.aruco.marker_modules + 2 * context.aruco.border_bits
    module_px = int(round(layout.marker_size_mm * ppm / modules))
    side = module_px * modules
    left = int(round(layout.marker_center_mm[0] * ppm - side / 2))
    top = int(round(layout.marker_center_mm[1] * ppm - side / 2))
    bits = context.aruco.matrix(card.marker_id)

    def module_shade(row: int, col: int) -> int:
        x = left + col * module_px + module_px // 2
        y = top + row * module_px + module_px // 2
        return texture.raster.pixel(x, y)

    border = context.aruco.border_bits
    for row in range(modules):
        for col in range(modules):
            shade = module_shade(row, col)
            if row < border or col < border or row >= modules - border or col >= modules - border:
                assert shade == MARKER_INK, f"第 {row} 行第 {col} 列应是黑边"
            else:
                expected = (
                    layout.background_shade
                    if bits[row - border][col - border] == "1"
                    else MARKER_INK
                )
                assert shade == expected, f"第 {row} 行第 {col} 列位图案不符"


def test_marker_quiet_zone_is_clean(rendered) -> None:
    layout, _, texture = rendered
    ppm = layout.pixels_per_mm
    half = layout.marker_size_mm * ppm / 2.0
    quiet = layout.marker_quiet_zone_mm * ppm
    cx = layout.marker_center_mm[0] * ppm
    cy = layout.marker_center_mm[1] * ppm
    # 静区内（标记外）应当全是卡面底色，不能有标签或装饰侵入
    for y in range(int(cy - half - quiet) + 1, int(cy - half)):
        for x in range(int(cx - half - quiet) + 1, int(cx + half + quiet)):
            assert texture.raster.pixel(x, y) == layout.background_shade


def test_label_has_ink(rendered) -> None:
    layout, card, texture = rendered
    ppm = layout.pixels_per_mm
    y0 = int(layout.label_anchor_mm[1] * ppm)
    band = [
        texture.raster.pixel(x, y)
        for y in range(y0, min(texture.height, y0 + int(layout.label_max_height_mm * ppm)))
        for x in range(int(layout.label_anchor_mm[0] * ppm), texture.width - int(layout.label_anchor_mm[0] * ppm))
    ]
    dark = sum(1 for value in band if value < layout.background_shade - 80)
    assert dark > 50, f"标签区域几乎没有墨迹：{dark}"
    assert card.card_face  # 标签文本来自卡片字典里的 card_face


def test_different_cards_produce_different_textures(context: SynthContext) -> None:
    layout = context.layouts["card-60x40"]
    first = render_card_texture(context.card_dictionary.card("C01"), layout, context.aruco, context.font)
    second = render_card_texture(context.card_dictionary.card("N02"), layout, context.aruco, context.font)
    assert first.raster.as_bytes() != second.raster.as_bytes()


def test_render_is_deterministic(context: SynthContext) -> None:
    layout = context.layouts["card-60x40"]
    card = context.card_dictionary.card("C07")
    first = render_card_texture(card, layout, context.aruco, context.font)
    second = render_card_texture(card, layout, context.aruco, context.font)
    assert first.raster.as_bytes() == second.raster.as_bytes()
    assert first.mask == second.mask


def test_every_dictionary_card_renders(context: SynthContext) -> None:
    for layout_name in sorted(context.layouts):
        layout = context.layouts[layout_name]
        for card in context.card_dictionary.cards:
            texture = render_card_texture(card, layout, context.aruco, context.font)
            assert texture.width == layout.width_px
            assert texture.height == layout.height_px


def test_layout_rejects_too_small_marker(context: SynthContext) -> None:
    layout = context.layouts["card-60x40"]
    too_small = type(layout)(
        **{
            **{field: getattr(layout, field) for field in layout.__dataclass_fields__},
            "pixels_per_mm": 1,
            "marker_size_mm": 1.0,
        }
    )
    with pytest.raises(ValueError, match="每模块只有"):
        too_small.validate(context.aruco)
