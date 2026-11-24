"""标记页的光栅渲染：A4 白纸 + 卡面 + 裁切线 + 卡片编号 + 朝向标记。

渲染是一遍完成的（没有抗锯齿、没有随机量）：每个像素只被写入一次或保持纸白，所有几何量
都从 :class:`~liveblocks.sheet.spec.CardSheetSpec` 的毫米坐标乘 ``pixels_per_mm`` 得到，
因此「同样的参数 → 同样的像素 → 同样的 PNG / PDF 字节」。

画面上有什么：

- **卡面**：直接复用 LB-002 的卡面渲染器（``synth.texture.render_card_texture``），所以标记页
  上的卡和合成场景里的卡是同一份画法——这一点是刻意的：识别层在合成场景里验证过的画法，
  原样出现在打印页上。卡面上带**卡片编号**（``card_id`` + ``card_face``，见版式的标签位）。
- **静区**：标记四周由版式声明的 ``marker_quiet_zone_mm`` 刷白；卡与卡之间再留 ``gutter_mm``，
  按静区规则不小于 1 个模块（``spec.validate`` 会挡住更小的间距）。
- **裁切线**：每张卡外围 ``cut_gap_mm`` 处一圈细灰线（线宽 ``cut_line_mm``），沿两线中间剪。
- **朝向标记**：每张卡面左下角一个实心三角形，尖朝卡片上边；页面上边另有一个「TOP」三角，
  规定「页面上边 = 朝向 0°」（识别侧的读数就是这个角度，见 ``sheet.verify``）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from ..synth.font import BitmapFont, draw_text
from ..synth.png import encode_gray
from ..synth.raster import Raster, fill_convex_polygon
from ..synth.texture import render_card_texture
from .manifest import build_manifest
from .pdf import encode_pdf_gray
from .spec import SHEET_VERSION, CardSheetSpec

#: 纸白（不落墨）。
PAGE_SHADE = 255
#: 裁切线（浅灰：看得见、但不会被当成卡面内容）。
GUIDE_SHADE = 120
#: 文字与朝向三角形的墨色。
INK_SHADE = 20

#: 页眉 / 页脚在页面顶部与底部的锚点（毫米，原点在纸张左上角）。
HEADER_ANCHOR_MM = 2.2
FOOTER_FROM_BOTTOM_MM = 6.0


@dataclass(frozen=True)
class SheetImage:
    """一页标记页的渲染结果：像素、PNG、PDF 与清单（全部确定）。"""

    spec: CardSheetSpec
    width: int
    height: int
    pixels: bytes
    png: bytes
    pdf: bytes
    manifest: dict
    manifest_json: str

    @property
    def pixel_sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    @property
    def png_sha256(self) -> str:
        return hashlib.sha256(self.png).hexdigest()

    @property
    def pdf_sha256(self) -> str:
        return hashlib.sha256(self.pdf).hexdigest()


# --- 基本图元 -------------------------------------------------------------------------


def _fill_rect_px(raster: Raster, x0: float, y0: float, x1: float, y1: float, shade: int) -> None:
    """按像素中心判定填充一个轴对齐矩形（与 ``calibtarget._fill_rect_px`` 同一取舍）。"""
    w, h = raster.width, raster.height
    left, right = int(round(x0)), int(round(x1))
    top, bottom = int(round(y0)), int(round(y1))
    pixels = raster.pixels
    for y in range(max(0, top), min(h, bottom)):
        row = y * w
        for x in range(max(0, left), min(w, right)):
            pixels[row + x] = shade


def _stroke_rect_px(
    raster: Raster, x0: float, y0: float, x1: float, y1: float, shade: int, line_px: int
) -> None:
    """画一个空心矩形（四条边），线宽 ``line_px``（至少 1 像素）。"""
    width = max(1, int(line_px))
    _fill_rect_px(raster, x0, y0, x1, y0 + width, shade)
    _fill_rect_px(raster, x0, y1 - width, x1, y1, shade)
    _fill_rect_px(raster, x0, y0, x0 + width, y1, shade)
    _fill_rect_px(raster, x1 - width, y0, x1, y1, shade)


def _triangle_up_px(
    raster: Raster, x0: float, y0: float, x1: float, y1: float, shade: int
) -> None:
    """尖朝上的实心三角形：底边贴 ``y1``、顶点在 ``((x0+x1)/2, y0)``。"""
    apex = ((x0 + x1) / 2.0, y0)
    quad = [apex, (x1, y1), (x0, y1)]
    fill_convex_polygon(raster, quad, shade)


def _text(
    raster: Raster,
    font: BitmapFont,
    text: str,
    anchor_px: tuple[int, int],
    *,
    scale: int = 1,
) -> None:
    draw_text(raster.pixels, None, raster.width, raster.height, font, text, anchor_px, scale=scale, shade=INK_SHADE)


def _fit_scale(font: BitmapFont, text: str, max_width_px: float, max_height_px: float) -> int:
    """在给定尺寸内取最大的整数倍字号（放不下时退回 1；字形缺失时返回 0）。"""
    if not font.supports(text):
        return 0
    scale = max(1, int(max_height_px // font.line_height_px))
    while scale > 1 and font.text_width(text, scale=scale) > max_width_px:
        scale -= 1
    return scale


# --- 页面 -----------------------------------------------------------------------------


def _header_text(spec: CardSheetSpec) -> str:
    return (
        f"LiveBlocks card sheet v{SHEET_VERSION}  layout {spec.layout_name}  "
        f"batch {spec.batch_id}  page 1/{max(1, spec.page_count)}"
    )


def _footer_text(spec: CardSheetSpec) -> str:
    # 只用内置点阵字形支持的字符（字母 / 数字 / 空格 / 标点 . - / :），否则整行画不出来。
    return (
        f"page top is orientation 0 deg  cut along the grey lines "
        f"{spec.cut_gap_mm:g} mm outside the card edge  "
        f"card gap {spec.gutter_mm:g} mm  quiet zone {spec.quiet_zone_rule_mm:g} mm"
    )


def render_page(spec: CardSheetSpec, *, font: BitmapFont | None = None) -> Raster:
    """把整页渲染成灰度光栅（像素数 = 纸张毫米数 × ``pixels_per_mm``）。"""
    spec.validate()
    bitmap = font or BitmapFont.load()
    ppm = spec.pixels_per_mm
    raster = Raster.filled(spec.width_px, spec.height_px, PAGE_SHADE)

    # 1) 页眉 / 页脚（都在页边距里，不压卡片）
    header = _header_text(spec)
    if not bitmap.supports(header):  # pragma: no cover - 数据文件改动时才可能触发
        raise ValueError(f"页眉含有内置字形不支持的字符：{header!r}")
    header_scale = _fit_scale(bitmap, header, spec.paper_width_mm * ppm - 2 * spec.margin_mm * ppm, 3.0 * ppm)
    if header_scale:
        _text(raster, bitmap, header, (int(spec.margin_mm * ppm), int(HEADER_ANCHOR_MM * ppm)), scale=header_scale)
    footer = _footer_text(spec)
    if not bitmap.supports(footer):  # pragma: no cover - 数据文件改动时才可能触发
        raise ValueError(f"页脚含有内置字形不支持的字符：{footer!r}")
    footer_scale = _fit_scale(
        bitmap, footer, spec.paper_width_mm * ppm - 2 * spec.margin_mm * ppm, 3.0 * ppm
    )
    if footer_scale:
        _text(
            raster,
            bitmap,
            footer,
            (
                int(spec.margin_mm * ppm),
                int((spec.paper_height_mm - FOOTER_FROM_BOTTOM_MM) * ppm),
            ),
            scale=footer_scale,
        )

    # 2) 页面级的「上」标记：页眉右下的三角 + TOP 字样
    top_mark_x0 = spec.paper_width_mm / 2.0 - 2.5
    _triangle_up_px(
        raster,
        top_mark_x0 * ppm,
        (spec.margin_mm - 4.2) * ppm,
        (top_mark_x0 + 5.0) * ppm,
        (spec.margin_mm - 1.2) * ppm,
        INK_SHADE,
    )
    if bitmap.supports("TOP"):
        _text(
            raster,
            bitmap,
            "TOP",
            (int((top_mark_x0 + 6.0) * ppm), int((spec.margin_mm - 4.2) * ppm)),
            scale=1,
        )

    # 3) 裁切线（先画：卡片与三角形后画会盖住自己的那份）
    line_px = max(1, int(round(spec.cut_line_mm * ppm)))
    gap = spec.cut_gap_mm
    for slot in spec.slots():
        x0, y0, x1, y1 = spec.slot_rect_mm(slot.index)
        _stroke_rect_px(
            raster,
            (x0 - gap) * ppm,
            (y0 - gap) * ppm,
            (x1 + gap) * ppm,
            (y1 + gap) * ppm,
            GUIDE_SHADE,
            line_px,
        )

    # 4) 卡面（含标记、静区、卡片编号）
    card_layout = replace(spec.layout, pixels_per_mm=int(round(ppm)))
    for slot in spec.slots():
        texture = render_card_texture(slot.card, card_layout, spec.aruco, bitmap)
        x0, y0, _x1, _y1 = spec.slot_rect_mm(slot.index)
        ox = int(round(x0 * ppm))
        oy = int(round(y0 * ppm))
        width, height = texture.width, texture.height
        src = texture.raster.pixels
        dst = raster.pixels
        page_width = raster.width
        for y in range(height):
            py = oy + y
            if not 0 <= py < raster.height:
                continue
            row = py * page_width
            src_row = y * width
            for x in range(width):
                if not texture.inside(x, y):
                    continue
                px = ox + x
                if 0 <= px < page_width:
                    dst[row + px] = src[src_row + x]

    # 5) 每张卡的朝向标记（卡面左下角，尖朝卡片上边）
    for slot in spec.slots():
        x0, y0, x1, y1 = spec.orientation_mark_rect_mm(slot.index)
        _triangle_up_px(raster, x0 * ppm, y0 * ppm, x1 * ppm, y1 * ppm, INK_SHADE)

    return raster


def render_sheet(spec: CardSheetSpec, *, font: BitmapFont | None = None) -> SheetImage:
    """渲染一页标记页，并给出 PNG / PDF / 清单（三者都由同一批像素决定）。"""
    raster = render_page(spec, font=font)
    pixels = raster.as_bytes()
    png = encode_gray(raster.width, raster.height, pixels)
    pdf = encode_pdf_gray(
        raster.width,
        raster.height,
        pixels,
        page_width_mm=spec.paper_width_mm,
        page_height_mm=spec.paper_height_mm,
    )
    manifest = build_manifest(spec, width=raster.width, height=raster.height, pixels=pixels, png=png, pdf=pdf)
    manifest_json = _manifest_json(manifest)
    return SheetImage(
        spec=spec,
        width=raster.width,
        height=raster.height,
        pixels=pixels,
        png=png,
        pdf=pdf,
        manifest=manifest,
        manifest_json=manifest_json,
    )


def _manifest_json(manifest: dict) -> str:
    import json

    return json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def rotate_quarter_turns(
    width: int, height: int, pixels: bytes | bytearray, turns: int
) -> tuple[int, int, bytes]:
    """把图像内容**顺时针**旋转 ``turns`` 个 90°（用于验证「朝向读数来自图像」）。"""
    if turns % 4 == 0:
        return width, height, bytes(pixels)
    if len(pixels) != width * height:
        raise ValueError(f"像素数不符：期望 {width * height}，实际 {len(pixels)}")
    data = bytes(pixels)
    out_w, out_h = (height, width) if turns % 2 else (width, height)
    out = bytearray(out_w * out_h)
    for y in range(height):
        row = y * width
        for x in range(width):
            shade = data[row + x]
            if turns % 4 == 1:  # 顺时针 90°：(x, y) → (H-1-y, x)
                nx, ny = height - 1 - y, x
            elif turns % 4 == 2:  # 180°
                nx, ny = width - 1 - x, height - 1 - y
            else:  # 顺时针 270°（= 逆时针 90°）
                nx, ny = y, width - 1 - x
            out[ny * out_w + nx] = shade
    return out_w, out_h, bytes(out)


__all__ = [
    "GUIDE_SHADE",
    "INK_SHADE",
    "PAGE_SHADE",
    "SheetImage",
    "render_page",
    "render_sheet",
    "rotate_quarter_turns",
]
