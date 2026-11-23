"""确定性 PNG 编解码（只用标准库 ``zlib`` + ``struct``）。

LB-002 的验收要求「给定固定随机种子，生成结果字节可复现」，所以要避免把
PNG 编码交给任何第三方库：编码器只做灰度 8bit、filter 恒为 ``None``、zlib
压缩级别固定，同一进程/同一 zlib 版本下同样的像素必然得到同样的字节。
``decode_gray`` 只用于测试与自检（把 fixtures 读回来比像素），不支持调色板、
真彩、隔行与 16bit。
"""

from __future__ import annotations

import struct
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
BIT_DEPTH = 8
COLOR_TYPE_GRAY = 0
FILTER_NONE = 0
COMPRESSION_LEVEL = 9


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_gray(
    width: int,
    height: int,
    pixels: bytes | bytearray,
    *,
    compresslevel: int = COMPRESSION_LEVEL,
) -> bytes:
    """把灰度像素（行优先，每像素 1 字节）编码成 PNG 字节串。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"图像尺寸必须为正：{width}x{height}")
    expected = width * height
    if len(pixels) != expected:
        raise ValueError(f"像素数不符：期望 {expected}，实际 {len(pixels)}")

    stride = width + 1
    raw = bytearray(stride * height)
    view = memoryview(bytes(pixels))
    for y in range(height):
        src = y * width
        dst = y * stride
        raw[dst] = FILTER_NONE
        raw[dst + 1 : dst + stride] = view[src : src + width]

    ihdr = struct.pack(">IIBBBBB", width, height, BIT_DEPTH, COLOR_TYPE_GRAY, 0, 0, 0)
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", zlib.compress(bytes(raw), compresslevel)),
            _chunk(b"IEND", b""),
        )
    )


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_gray(data: bytes) -> tuple[int, int, bytes]:
    """解码 ``encode_gray`` 产出的 PNG，返回 ``(width, height, pixels)``。"""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("不是 PNG：签名不符")

    offset = len(PNG_SIGNATURE)
    width = height = 0
    idat = bytearray()
    saw_ihdr = saw_iend = False

    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        if len(payload) != length:
            raise ValueError(f"PNG 数据块被截断：{tag!r}")
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        if crc != zlib.crc32(tag + payload) & 0xFFFFFFFF:
            raise ValueError(f"PNG 数据块 CRC 校验失败：{tag!r}")
        offset += 12 + length

        if tag == b"IHDR":
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (bit_depth, color_type, comp, filt, interlace) != (BIT_DEPTH, COLOR_TYPE_GRAY, 0, 0, 0):
                raise ValueError(
                    "只支持 8bit 灰度、不隔行的 PNG："
                    f"bit_depth={bit_depth} color_type={color_type} interlace={interlace}"
                )
            saw_ihdr = True
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            saw_iend = True
            break

    if not saw_ihdr or not saw_iend:
        raise ValueError("PNG 缺少 IHDR 或 IEND")

    raw = zlib.decompress(bytes(idat))
    stride = width + 1
    if len(raw) != stride * height:
        raise ValueError(f"IDAT 解压长度不符：期望 {stride * height}，实际 {len(raw)}")

    out = bytearray(width * height)
    prev = bytearray(width)
    for y in range(height):
        base = y * stride
        filter_type = raw[base]
        line = bytearray(raw[base + 1 : base + stride])
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for x in range(1, width):
                line[x] = (line[x] + line[x - 1]) & 0xFF
        elif filter_type == 2:
            for x in range(width):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif filter_type == 3:
            for x in range(width):
                left = line[x - 1] if x else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif filter_type == 4:
            for x in range(width):
                left = line[x - 1] if x else 0
                up_left = prev[x - 1] if x else 0
                line[x] = (line[x] + _paeth(left, prev[x], up_left)) & 0xFF
        else:
            raise ValueError(f"未知 PNG 过滤器类型：{filter_type}")
        out[y * width : (y + 1) * width] = line
        prev = line

    return width, height, bytes(out)
