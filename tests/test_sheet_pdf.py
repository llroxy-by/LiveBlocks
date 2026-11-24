"""LB-032 PDF 容器：结构、交叉引用、嵌入的像素与页面尺寸。

PDF 是我们自己写的（零依赖、要字节确定），所以必须自己验结构，并且把嵌入的图像流解回来
逐像素比对——「PDF 里就是那张 PNG 的像素」这件事不能只靠肉眼。

装了 poppler（``pdftoppm`` / ``pdfinfo``）时再把它渲染一遍交叉核对；没有这些工具就跳过，
CI 只装 ``.[dev]`` 时同样跳过（与仓库里 OpenCV / jsonschema 交叉核对的惯例一致）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zlib

import pytest

from liveblocks.sheet.pdf import HEADER, TRAILER_MARKER, encode_pdf_gray, mm_to_points
from liveblocks.sheet.render import render_sheet
from liveblocks.sheet.spec import CardSheetSpec


@pytest.fixture(scope="module")
def image():
    return render_sheet(CardSheetSpec.build())


def test_pdf_header_trailer_and_page_size(image) -> None:
    pdf = image.pdf
    assert pdf.startswith(HEADER)
    assert pdf.endswith(TRAILER_MARKER)
    assert b"/MediaBox [0 0 595.2756 841.8898]" in pdf  # A4：210/297 mm 换算成点
    assert mm_to_points(210.0) == pytest.approx(595.2755905511812)
    assert mm_to_points(297.0) == pytest.approx(841.8897637795277)


def test_xref_offsets_point_at_the_objects(image) -> None:
    pdf = image.pdf
    start = pdf.rindex(b"startxref\n")
    offset = int(pdf[start + len(b"startxref\n") : pdf.index(b"\n", start + len(b"startxref\n"))])
    assert pdf[offset : offset + 4] == b"xref"
    table = pdf[offset:].split(b"trailer")[0]
    lines = [line for line in table.split(b"\n") if re.fullmatch(rb"\d{10} \d{5} [nf] ", line)]
    assert len(lines) == 6  # 0 号是 free 项 + 5 个对象
    assert lines[0].startswith(b"0000000000 65535 f")
    for index, line in enumerate(lines[1:], start=1):
        object_offset = int(line.split()[0])
        assert pdf[object_offset : object_offset + len(f"{index} 0 obj".encode())] == f"{index} 0 obj".encode()


def test_embedded_image_stream_decompresses_to_the_same_pixels(image) -> None:
    pdf = image.pdf
    marker = pdf.index(b"/Subtype /Image")
    length_start = pdf.index(b"/Length ", marker) + len(b"/Length ")
    length = int(pdf[length_start : pdf.index(b" ", length_start)])
    stream_start = pdf.index(b"stream\n", marker) + len(b"stream\n")
    raw = zlib.decompress(pdf[stream_start : stream_start + length])
    assert raw == image.pixels
    assert f"/Width {image.width}".encode() in pdf
    assert f"/Height {image.height}".encode() in pdf
    assert b"/ColorSpace /DeviceGray" in pdf and b"/BitsPerComponent 8" in pdf


def test_pdf_is_byte_identical_for_the_same_pixels() -> None:
    pixels = (bytes([0, 128, 255, 7]) * 9)[: 7 * 5]
    first = encode_pdf_gray(7, 5, pixels, page_width_mm=210.0, page_height_mm=297.0)
    second = encode_pdf_gray(7, 5, bytes(pixels), page_width_mm=210.0, page_height_mm=297.0)
    assert first == second
    other = encode_pdf_gray(7, 5, bytes(reversed(pixels)), page_width_mm=210.0, page_height_mm=297.0)
    assert other != first


@pytest.mark.parametrize(
    "kwargs",
    [
        {"width": 0, "height": 3, "pixels": b"", "page_width_mm": 210.0, "page_height_mm": 297.0},
        {"width": 3, "height": 3, "pixels": b"abc", "page_width_mm": 0.0, "page_height_mm": 297.0},
        {"width": 2, "height": 2, "pixels": b"abc", "page_width_mm": 210.0, "page_height_mm": 297.0},
    ],
)
def test_invalid_inputs_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        encode_pdf_gray(**kwargs)


@pytest.mark.skipif(
    shutil.which("pdftoppm") is None or shutil.which("pdfinfo") is None,
    reason="没有 poppler（pdftoppm / pdfinfo），跳过独立实现的 PDF 交叉核对",
)
def test_poppler_accepts_the_pdf_and_draws_the_same_page(image, tmp_path) -> None:
    path = tmp_path / "sheet.pdf"
    path.write_bytes(image.pdf)
    info = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, check=True).stdout
    assert "Pages:           1" in info
    assert "595.276 x 841.89 pts (A4)" in info
    assert "Syntax Error" not in info
    subprocess.run(
        ["pdftoppm", "-r", "4", "-gray", "-png", str(path), str(tmp_path / "page")],
        check=True,
        capture_output=True,
    )
    rendered = sorted(tmp_path.glob("page*.png"))
    assert len(rendered) == 1
    # 只核对尺寸关系：PDF 页面比例与图像比例一致（A4 竖版）
    width, height, _pixels = _decode_any(rendered[0].read_bytes())
    assert abs(width / height - image.width / image.height) < 0.02


def _decode_any(data: bytes) -> tuple[int, int, bytes]:
    """ponym: poppler 出的可能是 RGB PNG，这里只取尺寸（灰度解码器不接受彩色）。"""
    import struct

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", data[16:24])
    return width, height, b""
