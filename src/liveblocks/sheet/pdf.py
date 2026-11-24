"""把标记页的灰度光栅写成**定长、确定性的 PDF**（只用标准库 ``zlib``）。

为什么自己写 PDF：仓库的运行时依赖为零（``docs/DECISIONS.md`` D-012），而「同参数两次输出
字节一致」不允许把页面交给第三方 PDF 库（它们的版本差异会改变输出）。这里的 PDF 只有一页、
只放一个 ``/DeviceGray`` 8 bit 图像对象：

    页面尺寸 = 纸张毫米数 × 72 / 25.4（点），图像按整页缩放（``q … cm /Im0 Do Q``）

因此 PDF 与 PNG **是同一批像素**：PNG 与 PDF 的差别只有容器，页面里画什么（卡片、裁切线、
编号、朝向标记）全在光栅里。字节确定性来自三点：

1. 没有 ``/Info`` 字典、没有时间戳、没有随机 ``/ID``；
2. 对象顺序与流内容固定，压缩用 ``zlib.compress(level=9)``；
3. 交叉引用表按实际字节偏移写出（同一份输入必然得到同一份偏移）。
"""

from __future__ import annotations

import zlib

MM_PER_INCH = 25.4
POINTS_PER_INCH = 72.0

#: PDF 结果里的固定注释（不含版本号以外的可变信息）。
HEADER = b"%PDF-1.4\n"
TRAILER_MARKER = b"%%EOF\n"


def mm_to_points(value_mm: float) -> float:
    return value_mm * POINTS_PER_INCH / MM_PER_INCH


def _number(value: float) -> str:
    """固定 4 位小数的数字文本（不出现指数记法、不出现 ``-0.0000``）。"""
    text = f"{value:.4f}"
    return "0.0000" if text == "-0.0000" else text


def encode_pdf_gray(
    width: int,
    height: int,
    pixels: bytes | bytearray,
    *,
    page_width_mm: float,
    page_height_mm: float,
    compresslevel: int = 9,
) -> bytes:
    """把 8 bit 灰度光栅编码成单页 PDF（页面按毫米尺寸等比铺满整页）。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"图像尺寸必须为正：{width}x{height}")
    if len(pixels) != width * height:
        raise ValueError(f"像素数不符：期望 {width * height}，实际 {len(pixels)}")
    if page_width_mm <= 0 or page_height_mm <= 0:
        raise ValueError(f"页面尺寸必须为正：{page_width_mm}x{page_height_mm}")

    image_stream = zlib.compress(bytes(pixels), compresslevel)
    page_w_pt = mm_to_points(page_width_mm)
    page_h_pt = mm_to_points(page_height_mm)
    content = (
        f"q\n{_number(page_w_pt)} 0 0 {_number(page_h_pt)} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
    )

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_number(page_w_pt)} "
            f"{_number(page_h_pt)}] /Resources << /XObject << /Im0 4 0 R >> >> "
            f"/Contents 5 0 R >>"
        ).encode("ascii"),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            f"/Length {len(image_stream)} >>\nstream\n"
        ).encode("ascii")
        + image_stream
        + b"\nendstream",
        (f"<< /Length {len(content)} >>\nstream\n").encode("ascii") + content + b"endstream",
    ]

    out = bytearray(HEADER)
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("ascii")
        out += body
        out += b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n".encode(
            "ascii"
        )
    )
    out += TRAILER_MARKER
    return bytes(out)


__all__ = ["HEADER", "TRAILER_MARKER", "encode_pdf_gray", "mm_to_points"]
