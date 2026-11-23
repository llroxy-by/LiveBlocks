"""卡面纹理渲染：圆角卡片底、ArUco 标记、卡片标签。

纹理坐标系以卡片左上角为原点、单位像素（``pixels_per_mm`` 决定分辨率），后续由
``geometry.texture_homography`` 换算成毫米坐标并投影到图像上。
"""

from __future__ import annotations

from dataclasses import dataclass

from .dictionaries import ArucoDictionary, CardEntry
from .font import BitmapFont, draw_text
from .raster import CardTexture, Raster

#: 标记墨色（几乎纯黑，留一点余量让「打印过」的观感更自然）。
MARKER_INK = 22


@dataclass(frozen=True)
class CardLayout:
    """卡面版式（来自 ``data/synth_layouts.json`` 的 ``card_layouts``）。"""

    name: str
    image_width_mm: float
    image_height_mm: float
    pixels_per_mm: int
    corner_radius_mm: float
    border_mm: float
    border_shade: int
    background_shade: int
    marker_size_mm: float
    marker_center_mm: tuple[float, float]
    marker_quiet_zone_mm: float
    label_anchor_mm: tuple[float, float]
    label_max_height_mm: float
    label_shade: int
    label_max_width_mm: float

    @property
    def width_px(self) -> int:
        return int(round(self.image_width_mm * self.pixels_per_mm))

    @property
    def height_px(self) -> int:
        return int(round(self.image_height_mm * self.pixels_per_mm))

    def validate(self, aruco: ArucoDictionary) -> None:
        if self.pixels_per_mm <= 0:
            raise ValueError(f"{self.name}: pixels_per_mm 必须为正")
        marker_px = self.marker_size_mm * self.pixels_per_mm
        modules = aruco.marker_modules + 2 * aruco.border_bits
        if marker_px / modules < 4:
            raise ValueError(
                f"{self.name}: 每模块只有 {marker_px / modules:.2f} 像素，太小（需要 >= 4）"
            )
        if self.marker_size_mm + 2 * self.marker_quiet_zone_mm > min(
            self.image_width_mm, self.image_height_mm
        ):
            raise ValueError(f"{self.name}: 标记 + 静区放不进卡面")
        if self.border_mm <= 0:
            raise ValueError(f"{self.name}: border_mm 必须为正")


def _inside_rounded(x: float, y: float, width: int, height: int, radius: float) -> bool:
    if x < 0.0 or y < 0.0 or x > width or y > height:
        return False
    if radius <= 0.0:
        return True
    cx = min(max(x, radius), width - radius)
    cy = min(max(y, radius), height - radius)
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= radius * radius


def _fill_rounded(
    raster: Raster,
    mask: bytearray,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    shade: int,
    mark_inside: bool,
) -> None:
    w, h = raster.width, raster.height
    px = raster.pixels
    y_start = max(0, int(x0 // 1) - 1)
    y_end = min(h, int(y1) + 2)
    x_start = max(0, int(x0 // 1) - 1)
    x_end = min(w, int(x1) + 2)
    for y in range(y_start, y_end):
        cy = y + 0.5
        if not (y0 <= cy <= y1):
            continue
        row = y * w
        for x in range(x_start, x_end):
            cx = x + 0.5
            if not (x0 <= cx <= x1):
                continue
            if not _inside_rounded(cx - x0, cy - y0, int(round(x1 - x0)), int(round(y1 - y0)), radius):
                continue
            px[row + x] = shade
            if mark_inside:
                mask[row + x] = 255


def render_card_texture(
    card: CardEntry,
    layout: CardLayout,
    aruco: ArucoDictionary,
    font: BitmapFont,
) -> CardTexture:
    """按版式把一张卡渲染成纹理。"""
    width_px, height_px = layout.width_px, layout.height_px
    raster = Raster.filled(width_px, height_px, 0)
    mask = bytearray(width_px * height_px)

    border_px = layout.border_mm * layout.pixels_per_mm
    radius_outer = layout.corner_radius_mm * layout.pixels_per_mm

    # 1) 卡片外轮廓（描边色），同时把掩码标记为「属于卡片」
    _fill_rounded(
        raster,
        mask,
        x0=0.0,
        y0=0.0,
        x1=float(width_px),
        y1=float(height_px),
        radius=radius_outer,
        shade=layout.border_shade,
        mark_inside=True,
    )

    # 2) 卡面底色（向内缩 border_px）
    _fill_rounded(
        raster,
        mask,
        x0=border_px,
        y0=border_px,
        x1=width_px - border_px,
        y1=height_px - border_px,
        radius=max(0.0, radius_outer - border_px),
        shade=layout.background_shade,
        mark_inside=True,
    )

    _draw_marker(raster, mask, card=card, aruco=aruco, layout=layout)
    _draw_label(raster, mask, card=card, layout=layout, font=font)

    return CardTexture(
        raster=raster,
        mask=mask,
        width_mm=layout.image_width_mm,
        height_mm=layout.image_height_mm,
        pixels_per_mm=float(layout.pixels_per_mm),
    )


def _draw_marker(
    raster: Raster, mask: bytearray, *, card: CardEntry, aruco: ArucoDictionary, layout: CardLayout
) -> None:
    ppm = layout.pixels_per_mm
    modules = aruco.marker_modules + 2 * aruco.border_bits
    module_px = int(round(layout.marker_size_mm * ppm / modules))
    side_px = module_px * modules
    center_x = layout.marker_center_mm[0] * ppm
    center_y = layout.marker_center_mm[1] * ppm
    left = int(round(center_x - side_px / 2.0))
    top = int(round(center_y - side_px / 2.0))

    bits = aruco.matrix(card.marker_id)
    w = raster.width
    px = raster.pixels

    def put(x: int, y: int, shade: int) -> None:
        if 0 <= x < raster.width and 0 <= y < raster.height:
            px[y * w + x] = shade
            mask[y * w + x] = 255

    # 静区：显式刷成底色，避免相邻图形（如标签）侵入标记静区
    quiet_px = int(round(layout.marker_quiet_zone_mm * ppm))
    for y in range(top - quiet_px, top + side_px + quiet_px):
        for x in range(left - quiet_px, left + side_px + quiet_px):
            put(x, y, layout.background_shade)

    for row in range(modules):
        for col in range(modules):
            is_border = (
                row < aruco.border_bits
                or col < aruco.border_bits
                or row >= modules - aruco.border_bits
                or col >= modules - aruco.border_bits
            )
            if is_border:
                white = False
            else:
                bit = bits[row - aruco.border_bits][col - aruco.border_bits]
                white = bit == "1"
            shade = layout.background_shade if white else MARKER_INK
            for dy in range(module_px):
                y = top + row * module_px + dy
                for dx in range(module_px):
                    put(left + col * module_px + dx, y, shade)


def _draw_label(raster: Raster, mask: bytearray, *, card: CardEntry, layout: CardLayout, font: BitmapFont) -> None:
    text = f"{card.card_id} {card.card_face}"
    if not font.supports(text):
        text = card.card_id
        if not font.supports(text):
            return
    ppm = layout.pixels_per_mm
    max_height_px = layout.label_max_height_mm * ppm
    max_width_px = layout.label_max_width_mm * ppm
    scale = max(1, int(max_height_px // font.line_height_px))
    while scale > 1 and font.text_width(text, scale=scale) > max_width_px:
        scale -= 1
    origin = (
        int(round(layout.label_anchor_mm[0] * ppm)),
        int(round(layout.label_anchor_mm[1] * ppm)),
    )
    draw_text(
        raster.pixels,
        mask,
        raster.width,
        raster.height,
        font,
        text,
        origin,
        scale=scale,
        shade=layout.label_shade,
    )
