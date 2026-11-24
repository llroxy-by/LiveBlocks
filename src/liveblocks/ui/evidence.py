"""截图证据的最小校验：读 PNG、算「有多少像素被画过」。

为什么要这个模块：Issue #20 要求「附截图作为证据」，而截图必须是**真渲染出来的**
（不是 mock-up）。渲染由 WebKitGTK 完成（``scripts/screenshot_view.py``），本模块只做
**可机器核对**的那部分：图像尺寸、颜色类型、以及「非空白」的量化指标（墨迹覆盖率、
不同灰度/颜色数）。它不能替代人眼判断「好不好看」，但能证明「这张图不是纯白、不是空文件」，
也能在 CI 里防止证据被误删或退化成空白。

支持 8bit 的灰度 / 真彩 / 带 Alpha（color type 0/2/4/6，filter 0..4），不支持调色板、
隔行与 16bit——WebKit 与仓库自带的编码器都不产出这些。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


@dataclass(frozen=True)
class ScreenshotStats:
    """一张截图的统计量。"""

    width: int
    height: int
    color_type: int
    pixels: int
    #: 灰度/平均亮度不等于背景（取众数亮度）的像素占比，0..1。
    ink_ratio: float
    #: 出现过的不同灰度（0..255）个数。
    levels: int
    #: 平均亮度（0..255）。
    mean: float
    #: 最亮与最暗的灰度差。
    contrast: int

    @property
    def non_blank(self) -> bool:
        """「不是空白图」的判据：至少 1% 的像素与背景不同，且有 8 级以上层次。"""
        return self.ink_ratio >= 0.01 and self.levels >= 8

    def describe(self) -> str:
        return (
            f"{self.width}x{self.height} color_type={self.color_type} "
            f"ink={self.ink_ratio * 100:.1f}% levels={self.levels} "
            f"mean={self.mean:.1f} contrast={self.contrast}"
        )


def _read_chunks(data: bytes) -> tuple[int, int, int, bytes]:
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("不是 PNG：签名不符")
    offset = len(PNG_SIGNATURE)
    width = height = color_type = 0
    idat = bytearray()
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        tag = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if tag == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", payload[:10])
            if depth != 8:
                raise ValueError(f"只支持 8bit PNG，实际 {depth}bit")
            if color_type not in _CHANNELS:
                raise ValueError(f"不支持的 color_type：{color_type}")
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if not width or not height:
        raise ValueError("PNG 缺少 IHDR")
    return width, height, color_type, bytes(idat)


def _unfilter(raw: bytes, width: int, height: int, channels: int) -> list[int]:
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    position = 0
    for row in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(channels, stride):
                line[index] = (line[index] + line[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                line[index] = (line[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                line[index] = (line[index] + ((left + previous[index]) >> 1)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = line[index - channels] if index >= channels else 0
                up = previous[index]
                up_left = previous[index - channels] if index >= channels else 0
                p = left + up - up_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - up_left)
                if pa <= pb and pa <= pc:
                    predictor = left
                elif pb <= pc:
                    predictor = up
                else:
                    predictor = up_left
                line[index] = (line[index] + predictor) & 0xFF
        else:
            raise ValueError(f"不支持的 PNG filter：{filter_type}")
        out[row * stride : (row + 1) * stride] = line
        previous = line
    return list(out)


def screenshot_stats(data: bytes) -> ScreenshotStats:
    """统计一张 PNG 的「墨迹覆盖率」等指标（用于证明截图不是空白）。"""
    width, height, color_type, idat = _read_chunks(data)
    channels = _CHANNELS[color_type]
    raw = zlib.decompress(idat)
    samples = _unfilter(raw, width, height, channels)
    luminances = bytearray(width * height)
    for index in range(width * height):
        base = index * channels
        if color_type in (0, 4):
            value = samples[base]
        else:
            r, g, b = samples[base], samples[base + 1], samples[base + 2]
            # 整数版 0.299/0.587/0.114，避免浮点比较带来平台差异。
            value = (299 * r + 587 * g + 114 * b) // 1000
        luminances[index] = value
    histogram = [0] * 256
    for value in luminances:
        histogram[value] += 1
    background = max(range(256), key=lambda value: (histogram[value], -value))
    ink = sum(count for value, count in enumerate(histogram) if value != background)
    total = width * height
    total_luminance = sum(value * count for value, count in enumerate(histogram))
    darkest = min(value for value in range(256) if histogram[value])
    brightest = max(value for value in range(256) if histogram[value])
    return ScreenshotStats(
        width=width,
        height=height,
        color_type=color_type,
        pixels=total,
        ink_ratio=ink / total if total else 0.0,
        levels=sum(1 for count in histogram if count),
        mean=total_luminance / total if total else 0.0,
        contrast=brightest - darkest,
    )


__all__ = ["ScreenshotStats", "screenshot_stats"]
