"""内置点阵字形表（从包内 ``data/bitmap_font.json`` 读取），用于在卡面打印标签。

字形数据是**入库的固定点阵**，不在运行时依赖任何字体库，因此渲染结果与运行环境无关。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data import read_json


@dataclass(frozen=True)
class Glyph:
    x_offset: int
    y_offset: int
    width: int
    height: int
    rows: tuple[str, ...]


@dataclass(frozen=True)
class BitmapFont:
    name: str
    line_height_px: int
    advances: dict[str, float]
    glyphs: dict[str, Glyph]

    @classmethod
    def load(cls, resource: str = "bitmap_font.json") -> "BitmapFont":
        raw = read_json(resource)
        glyphs = {
            char: Glyph(
                x_offset=int(spec["x_offset"]),
                y_offset=int(spec["y_offset"]),
                width=int(spec["width"]),
                height=int(spec["height"]),
                rows=tuple(spec["rows"]),
            )
            for char, spec in raw["glyphs"].items()
        }
        return cls(
            name=str(raw["name"]),
            line_height_px=int(raw["line_height_px"]),
            advances={char: float(value) for char, value in raw["advances"].items()},
            glyphs=glyphs,
        )

    def supports(self, text: str) -> bool:
        return all(char in self.glyphs for char in text)

    def advance(self, char: str) -> float:
        return self.advances.get(char, float(self.line_height_px))

    def text_width(self, text: str, *, scale: int = 1) -> int:
        return int(round(sum(self.advance(char) for char in text) * scale))


def draw_text(
    pixels: bytearray,
    mask: bytearray | None,
    width: int,
    height: int,
    font: BitmapFont,
    text: str,
    origin: tuple[int, int],
    *,
    scale: int = 1,
    shade: int = 20,
) -> None:
    """把 ``text`` 以整数倍 ``scale`` 画到 ``pixels`` 上（超界自动裁掉）。

    ``origin`` 是文本行左上角（对应 ``line_height_px`` 的顶边），与 ``bitmap_font.json``
    里记录的字形偏移配套。
    """
    if scale < 1:
        raise ValueError(f"scale 必须 >= 1：{scale}")
    pen_x = float(origin[0])
    pen_y = origin[1]
    for char in text:
        glyph = font.glyphs.get(char)
        if glyph is None:
            raise KeyError(f"字形表里没有 {char!r}（charset 见 data/bitmap_font.json）")
        if glyph.height and glyph.width:
            gx = int(round(pen_x)) + glyph.x_offset * scale
            gy = pen_y + glyph.y_offset * scale
            for gy_row, row_bits in enumerate(glyph.rows):
                py0 = gy + gy_row * scale
                if py0 + scale <= 0 or py0 >= height:
                    continue
                for gx_col, bit in enumerate(row_bits):
                    if bit != "1":
                        continue
                    px0 = gx + gx_col * scale
                    if px0 + scale <= 0 or px0 >= width:
                        continue
                    for dy in range(scale):
                        y = py0 + dy
                        if y < 0 or y >= height:
                            continue
                        base = y * width
                        for dx in range(scale):
                            x = px0 + dx
                            if x < 0 or x >= width:
                                continue
                            pixels[base + x] = shade
                            if mask is not None:
                                mask[base + x] = 255
        pen_x += font.advance(char) * scale
