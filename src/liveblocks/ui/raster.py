"""叠加层的确定性光栅（只用标准库 + 仓库自带的点阵字体）。

「帧叠加」面板要在一张 8bit 灰度帧上画出标记框、卡片中心与槽位号。为了保持
「零运行时依赖 + 字节可复现」，这里不引入任何绘图库：画线用整数 Bresenham，
文字用 ``liveblocks.synth.font`` 的入库点阵字形，PNG 编解码用
``liveblocks.synth.png``（固定压缩级别、固定 filter）。

所有遍历顺序都是固定的整数循环，因此同样的输入必然得到同样的字节——截图与
「两次回放一致」都依赖这一点。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..synth.font import BitmapFont, draw_text
from ..synth.png import decode_gray, encode_gray

#: 叠加用的灰度（背景是 60 左右的深灰桌面，所以线要亮）。
INK = 245
INK_SOFT = 200
LABEL_BG = 16
LABEL_FG = 250
PLACEHOLDER_BG = 96

FONT_SCALE = 1

#: 叠加图的 PNG 压缩级别。级别固定（与 ``synth.png`` 一样接受「字节可复现」的约束），
#: 取 3 而不是 9：同一张 640x480 图从 ~60 ms 降到 ~13 ms，体积只大 2%，而界面每次渲染
#: 都要重新编码一次（回放的确定性不依赖压缩级别）。
OVERLAY_COMPRESSLEVEL = 3


@dataclass
class Canvas:
    """8bit 灰度画布。"""

    width: int
    height: int
    pixels: bytearray

    @classmethod
    def blank(cls, width: int, height: int, shade: int = 255) -> "Canvas":
        return cls(width=width, height=height, pixels=bytearray([shade]) * (width * height))

    @classmethod
    def from_png(cls, data: bytes) -> "Canvas":
        width, height, pixels = decode_gray(data)
        return cls(width=width, height=height, pixels=bytearray(pixels))

    def to_png(self, *, compresslevel: int = OVERLAY_COMPRESSLEVEL) -> bytes:
        return encode_gray(self.width, self.height, bytes(self.pixels), compresslevel=compresslevel)

    def copy(self) -> "Canvas":
        return Canvas(width=self.width, height=self.height, pixels=bytearray(self.pixels))

    def set(self, x: int, y: int, shade: int) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = shade

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.pixels[y * self.width + x]
        return 0

    def fill_rect(self, x0: int, y0: int, x1: int, y1: int, shade: int) -> None:
        left, right = (x0, x1) if x0 <= x1 else (x1, x0)
        top, bottom = (y0, y1) if y0 <= y1 else (y1, y0)
        left = max(0, left)
        top = max(0, top)
        right = min(self.width - 1, right)
        bottom = min(self.height - 1, bottom)
        for y in range(top, bottom + 1):
            base = y * self.width
            for x in range(left, right + 1):
                self.pixels[base + x] = shade

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, shade: int = INK, thickness: int = 1) -> None:
        """整数 Bresenham 直线（``thickness`` 用像素偏移加粗，偏移顺序固定）。"""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        offsets = [(ox, oy) for oy in range(thickness) for ox in range(thickness)]
        while True:
            for ox, oy in offsets:
                self.set(x + ox, y + oy, shade)
            if x == x1 and y == y1:
                break
            err2 = 2 * err
            if err2 >= dy:
                err += dy
                x += sx
            if err2 <= dx:
                err += dx
                y += sy

    def draw_polyline(
        self,
        points: list[tuple[int, int]],
        *,
        closed: bool = True,
        shade: int = INK,
        thickness: int = 1,
    ) -> None:
        if len(points) < 2:
            return
        for index in range(len(points) - 1):
            self.draw_line(*points[index], *points[index + 1], shade=shade, thickness=thickness)
        if closed:
            self.draw_line(*points[-1], *points[0], shade=shade, thickness=thickness)

    def draw_cross(self, cx: int, cy: int, size: int = 5, shade: int = INK) -> None:
        self.draw_line(cx - size, cy, cx + size, cy, shade=shade)
        self.draw_line(cx, cy - size, cx, cy + size, shade=shade)
        self.draw_line(cx - size + 2, cy - size + 2, cx + size - 2, cy + size - 2, shade=shade)
        self.draw_line(cx - size + 2, cy + size - 2, cx + size - 2, cy - size + 2, shade=shade)

    def draw_label(
        self,
        text: str,
        origin: tuple[int, int],
        *,
        font: BitmapFont,
        shade: int = LABEL_FG,
        background: int | None = LABEL_BG,
    ) -> None:
        """画一行等宽点阵标签，可选地先填一块底色（保证在任意背景上可读）。

        入库字形集是 ASCII（``data/bitmap_font.json``），字形表里没有的字符（中文、``?`` 等）
        统一替换成 ``_`` 占位——不抛异常，也不改字形表（改它会改变 LB-002 的 fixtures 字节）。
        """
        safe = "".join(char if char in font.glyphs else "_" for char in text)
        if not safe:
            return
        width = font.text_width(safe, scale=FONT_SCALE)
        height = font.line_height_px * FONT_SCALE
        x, y = origin
        if background is not None:
            self.fill_rect(x - 2, y - 1, x + width + 1, y + height, background)
        draw_text(
            self.pixels,
            None,
            self.width,
            self.height,
            font,
            safe,
            (x, y),
            scale=FONT_SCALE,
            shade=shade,
        )


def load_overlay_font() -> BitmapFont:
    """叠加图的标签字体（入库点阵，ASCII）。"""
    return BitmapFont.load()


def placeholder(width: int, height: int, lines: list[str], *, font: BitmapFont) -> Canvas:
    """帧图像缺失时的占位画布：灰色斜纹 + 说明文字（可见提示，不是空白图）。"""
    canvas = Canvas.blank(width, height, PLACEHOLDER_BG)
    for y in range(height):
        for x in range(width):
            if (x // 8 + y // 8) % 2 == 0:
                canvas.pixels[y * width + x] = PLACEHOLDER_BG + 12
    canvas.fill_rect(8, 8, width - 9, 8 + 14 * len(lines) + 8, LABEL_BG)
    for index, line in enumerate(lines):
        canvas.draw_label(line, (14, 14 + index * 14), font=font, background=None)
    return canvas


__all__ = [
    "Canvas",
    "OVERLAY_COMPRESSLEVEL",
    "INK",
    "INK_SOFT",
    "LABEL_BG",
    "LABEL_FG",
    "load_overlay_font",
    "placeholder",
]
