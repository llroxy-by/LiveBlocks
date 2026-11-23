"""灰度光栅：画布、纹理反投影采样、多边形填充、模糊与光照噪声。

全部实现只用标准库 ``math`` / ``random``，并且都按固定的像素遍历顺序计算，因此
「同样的输入 + 同样的种子」必然得到同样的字节。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .geometry import Matrix3, Point2

#: 纹理覆盖率掩码里「属于卡片」的取值。
INSIDE = 255
OUTSIDE = 0


@dataclass
class Raster:
    """8bit 灰度画布，行优先。"""

    width: int
    height: int
    pixels: bytearray = field(default_factory=bytearray)

    def __post_init__(self) -> None:
        if not self.pixels:
            self.pixels = bytearray(self.width * self.height)
        elif len(self.pixels) != self.width * self.height:
            raise ValueError(
                f"像素数不符：{self.width}x{self.height} 需要 {self.width * self.height}，"
                f"实际 {len(self.pixels)}"
            )

    @classmethod
    def filled(cls, width: int, height: int, shade: int) -> "Raster":
        if not 0 <= shade <= 255:
            raise ValueError(f"灰度必须在 0..255：{shade}")
        return cls(width, height, bytearray([shade]) * (width * height))

    def copy(self) -> "Raster":
        return Raster(self.width, self.height, bytearray(self.pixels))

    def pixel(self, x: int, y: int) -> int:
        return self.pixels[y * self.width + x]

    def as_bytes(self) -> bytes:
        return bytes(self.pixels)


@dataclass
class CardTexture:
    """一张卡片的纹理：像素 + 覆盖率掩码（决定卡片轮廓外的像素不被画上去）。"""

    raster: Raster
    mask: bytearray
    width_mm: float
    height_mm: float
    pixels_per_mm: float

    def inside(self, x: int, y: int) -> bool:
        return self.mask[y * self.width + x] >= 128

    @property
    def width(self) -> int:
        return self.raster.width

    @property
    def height(self) -> int:
        return self.raster.height


def _bilinear(raster: Raster, x: float, y: float) -> float:
    """以像素中心为采样点的双线性插值。"""
    w, h = raster.width, raster.height
    fx = x - 0.5
    fy = y - 0.5
    x0 = math.floor(fx)
    y0 = math.floor(fy)
    tx = fx - x0
    ty = fy - y0
    px = raster.pixels

    def clamp_pix(cx: int, cy: int) -> int:
        if cx < 0:
            cx = 0
        elif cx >= w:
            cx = w - 1
        if cy < 0:
            cy = 0
        elif cy >= h:
            cy = h - 1
        return px[cy * w + cx]

    p00 = clamp_pix(x0, y0)
    p10 = clamp_pix(x0 + 1, y0)
    p01 = clamp_pix(x0, y0 + 1)
    p11 = clamp_pix(x0 + 1, y0 + 1)
    top = p00 + (p10 - p00) * tx
    bottom = p01 + (p11 - p01) * tx
    return top + (bottom - top) * ty


def mask_at(mask: bytearray, width: int, height: int, x: int, y: int) -> bool:
    cx = min(max(x, 0), width - 1)
    cy = min(max(y, 0), height - 1)
    return mask[cy * width + cx] >= 128


def project_bbox(points: list[Point2], width: int, height: int, *, margin: int = 2) -> tuple[int, int, int, int] | None:
    """把一组图像坐标点转成裁剪到画布内的整数包围盒 ``(x0, y0, x1, y1)``（右开）。"""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0 = max(0, int(math.floor(min(xs))) - margin)
    y0 = max(0, int(math.floor(min(ys))) - margin)
    x1 = min(width, int(math.ceil(max(xs))) + margin)
    y1 = min(height, int(math.ceil(max(ys))) + margin)
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def warp_texture(
    canvas: Raster,
    texture: CardTexture,
    inverse_homography: Matrix3,
    *,
    bbox: tuple[int, int, int, int],
) -> int:
    """用逆单应把纹理反投影到画布上，返回实际写入的像素数。

    只有落在纹理范围内（``0 <= u < w``、``0 <= v < h``）且掩码为「属于卡片」的像素
    才会写入，因此裁切、圆角与卡外区域自然表现为「没画」。
    """
    x0, y0, x1, y1 = bbox
    tex = texture.raster
    tw, th = tex.width, tex.height
    mask = texture.mask
    out = canvas.pixels
    cw = canvas.width
    m = inverse_homography
    m00, m01, m02 = m[0]
    m10, m11, m12 = m[1]
    m20, m21, m22 = m[2]
    written = 0

    for y in range(y0, y1):
        sy = y + 0.5
        row = y * cw
        for x in range(x0, x1):
            sx = x + 0.5
            w = m20 * sx + m21 * sy + m22
            if -1e-12 < w < 1e-12:
                continue
            u = (m00 * sx + m01 * sy + m02) / w
            if u < 0.0 or u >= tw:
                continue
            v = (m10 * sx + m11 * sy + m12) / w
            if v < 0.0 or v >= th:
                continue
            tu = int(u)
            tv = int(v)
            if mask[tv * tw + tu] < 128:
                continue
            out[row + x] = int(round(_bilinear(tex, u, v)))
            written += 1
    return written


def fill_convex_polygon(
    canvas: Raster, quad: list[Point2], shade: int, *, bbox: tuple[int, int, int, int] | None = None
) -> int:
    """用固定灰度填充一个凸多边形（遮挡物用），返回写入像素数。"""
    if bbox is None:
        bbox = project_bbox(quad, canvas.width, canvas.height, margin=1)
        if bbox is None:
            return 0
    x0, y0, x1, y1 = bbox
    n = len(quad)
    out = canvas.pixels
    cw = canvas.width
    written = 0
    for y in range(y0, y1):
        sy = y + 0.5
        row = y * cw
        for x in range(x0, x1):
            sx = x + 0.5
            inside = True
            sign = 0
            for i in range(n):
                ax, ay = quad[i]
                bx, by = quad[(i + 1) % n]
                cross = (bx - ax) * (sy - ay) - (by - ay) * (sx - ax)
                if cross > 1e-9:
                    if sign < 0:
                        inside = False
                        break
                    sign = 1
                elif cross < -1e-9:
                    if sign > 0:
                        inside = False
                        break
                    sign = -1
            if inside:
                out[row + x] = shade
                written += 1
    return written


def box_blur(canvas: Raster, radius: int) -> None:
    """就地做 ``radius`` 半径的可分离盒式模糊（含自身，2r+1 平均）。"""
    if radius <= 0:
        return
    w, h = canvas.width, canvas.height
    px = canvas.pixels

    # 水平方向
    tmp = bytearray(len(px))
    window = 2 * radius + 1
    for y in range(h):
        row = y * w
        acc = 0
        for i in range(-radius, radius + 1):
            acc += px[row + min(max(i, 0), w - 1)]
        for x in range(w):
            tmp[row + x] = acc // window
            add = px[row + min(x + radius + 1, w - 1)]
            sub = px[row + min(max(x - radius, 0), w - 1)]
            acc += add - sub

    # 垂直方向
    for x in range(w):
        acc = 0
        for i in range(-radius, radius + 1):
            acc += tmp[min(max(i, 0), h - 1) * w + x]
        for y in range(h):
            px[y * w + x] = acc // window
            add = tmp[min(y + radius + 1, h - 1) * w + x]
            sub = tmp[min(max(y - radius, 0), h - 1) * w + x]
            acc += add - sub


def apply_lighting(
    canvas: Raster,
    *,
    gain: float,
    vignette: float,
    noise_sigma: float,
    rng: random.Random,
    offset: int = 0,
) -> None:
    """就地施加「增益 → 暗角 → 高斯噪声」，并按行优先顺序消耗随机数。"""
    if gain < 0:
        raise ValueError(f"gain 必须非负：{gain}")
    if not 0.0 <= vignette <= 1.0:
        raise ValueError(f"vignette 必须在 0..1：{vignette}")
    if noise_sigma < 0:
        raise ValueError(f"noise_sigma 必须非负：{noise_sigma}")

    w, h = canvas.width, canvas.height
    px = canvas.pixels
    cx = (w - 1) / 2.0
    cy = (h - 1) / 2.0
    max_d2 = cx * cx + cy * cy
    gauss = rng.gauss
    for y in range(h):
        dy = y - cy
        row = y * w
        dy2 = dy * dy
        for x in range(w):
            dx = x - cx
            factor = 1.0 - vignette * ((dx * dx + dy2) / max_d2)
            if factor < 0.0:
                factor = 0.0
            v = px[row + x] * factor * gain
            if noise_sigma > 0.0:
                v += gauss(0.0, noise_sigma)
            v += offset
            if v <= 0.0:
                px[row + x] = 0
            elif v >= 255.0:
                px[row + x] = 255
            else:
                px[row + x] = int(v + 0.5)
