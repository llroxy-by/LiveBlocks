"""PNG 编码/解码测试：可复现、可回读、坏数据要报错而不是静默。"""

from __future__ import annotations

import zlib

import pytest

from liveblocks.synth.png import PNG_SIGNATURE, decode_gray, encode_gray


def _sample(width: int = 17, height: int = 9) -> bytes:
    return bytes((x * 7 + y * 13) % 256 for y in range(height) for x in range(width))


def test_encode_decode_roundtrip() -> None:
    pixels = _sample()
    data = encode_gray(17, 9, pixels)
    assert data.startswith(PNG_SIGNATURE)
    width, height, restored = decode_gray(data)
    assert (width, height) == (17, 9)
    assert restored == pixels


def test_encoding_is_deterministic() -> None:
    pixels = _sample(64, 32)
    assert encode_gray(64, 32, pixels) == encode_gray(64, 32, pixels)
    assert encode_gray(64, 32, pixels, compresslevel=9) == encode_gray(64, 32, pixels, compresslevel=9)


def test_encoding_rejects_wrong_pixel_count() -> None:
    with pytest.raises(ValueError, match="像素数不符"):
        encode_gray(4, 4, b"\x00" * 15)
    with pytest.raises(ValueError, match="图像尺寸必须为正"):
        encode_gray(0, 4, b"")


def test_chunk_structure_is_minimal() -> None:
    data = encode_gray(4, 4, bytes(range(16)))
    chunks = _chunk_tags(data)
    # 只有 IHDR + IDAT + IEND 三个数据块
    assert chunks == ["IHDR", "IDAT", "IEND"]


def _chunk_tags(data: bytes) -> list[str]:
    import struct

    offset = len(PNG_SIGNATURE)
    tags = []
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tags.append(data[offset + 4 : offset + 8].decode("ascii"))
        offset += 12 + length
    return tags


def test_decode_rejects_bad_signature() -> None:
    with pytest.raises(ValueError, match="签名不符"):
        decode_gray(b"not a png at all")


def test_decode_rejects_corrupted_crc() -> None:
    data = bytearray(encode_gray(4, 4, bytes(16)))
    data[-15] ^= 0xFF  # 打坏 IDAT 的 CRC
    with pytest.raises(ValueError, match="CRC"):
        decode_gray(bytes(data))


def test_decode_rejects_truncated_file() -> None:
    data = encode_gray(4, 4, bytes(16))
    with pytest.raises(ValueError):
        decode_gray(data[: len(data) // 2])


def test_decode_rejects_non_grayscale_header() -> None:
    import struct

    raw = bytes([0, 0, 0, 0, 0, 0])  # 2 行 x (1 filter + 2 像素)
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)  # color_type = 2（真彩）
    data = PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
    with pytest.raises(ValueError, match="只支持 8bit 灰度"):
        decode_gray(data)


def test_decode_handles_other_filter_types() -> None:
    """虽然本包只写 filter=None，解码器要能读其它过滤器的 PNG（兼容历史/外部文件）。"""
    width, height = 5, 3
    pixels = bytes([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150])
    raw = bytearray()
    prev = bytes(width)
    for y in range(height):
        line = pixels[y * width : (y + 1) * width]
        if y == 0:
            raw.append(0)
            raw += line
        else:
            raw.append(2)  # Up 过滤器
            raw += bytes((line[x] - prev[x]) & 0xFF for x in range(width))
        prev = line
    data = _build_png(width, height, bytes(raw))
    assert decode_gray(data) == (width, height, pixels)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    import struct

    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _build_png(width: int, height: int, raw: bytes) -> bytes:
    import struct

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")
