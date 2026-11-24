"""镜头畸变模型（径向 + 切向），给合成器用：按给定真值内参把「理想针孔图」变成「带畸变的拍摄图」。

模型是 Brown–Conrady 的 ``plumb_bob``（与 ``docs/PROTOCOL.md`` §2 的 ``distortion`` 字段同名同参）：

    归一化坐标   x = (u - cx) / fx,  y = (v - cy) / fy,  r² = x² + y²
    径向         r_s = 1 + k1·r² + k2·r⁴
    切向         dx = 2·p1·x·y + p2·(r² + 2x²)
                 dy = p1·(r² + 2y²) + 2·p2·x·y
    畸变后       x_d = x·r_s + dx,  y_d = y·r_s + dy
    像素         u_d = fx·x_d + cx,  v_d = fy·y_d + cy

「理想图」= 针孔投影图（无畸变），「拍摄图」= 理想图经过上面这层映射。因此：

- :func:`distort_raster` 由理想图生成拍摄图（合成器走这条）；
- :func:`undistort_raster` 由拍摄图还原理想图（LB-031 的校正输出走这条）。

坐标约定与 ``liveblocks.synth.raster`` 一致：像素 ``(x, y)`` 的中心在 ``(x + 0.5, y + 0.5)``，
坐标是**连续**的像素坐标（``0`` 是左上像素的外边界）。

反解 :meth:`LensModel.undistort` 用不动点迭代（``x ← (x_d - dx) / r_s``）。切向项与径向项
在 ``|k1·r² + k2·r⁴|`` 不接近 1 时压缩，合成器设定的畸变量都远小于该上限；超过
:data:`MAX_RADIAL_FACTOR` 时直接报错而不是给出发散的结果。

一切计算都只有 ``float`` 四则运算与固定迭代次数，因此同样的输入必然得到同样的字节。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .raster import Raster

Point = tuple[float, float]

#: 反解不动点迭代的最大次数与收敛阈值（像素坐标下的 1e-12 已经远超 8bit 图像能表达的精度）。
UNDISTORT_ITERATIONS = 24
UNDISTORT_TOLERANCE = 1e-12

#: 径向缩放因子的绝对值上限：超过它说明这组系数不是「镜头畸变」而是数值故障。
MAX_RADIAL_FACTOR = 4.0

#: 映射查找表的网格间距（像素）。畸变映射是光滑的，双线性插值到 1e-3 px 量级足够，
#: 而纯 Python 逐像素解不动点太慢（640×480 约 3e5 个像素 × 24 次迭代）。
MAP_GRID_PX = 8


@dataclass(frozen=True)
class LensModel:
    """内参 + 畸变系数（一个「镜头」的完整描述）。"""

    fx: float
    fy: float
    cx: float
    cy: float
    k1: float = 0.0
    k2: float = 0.0
    p1: float = 0.0
    p2: float = 0.0
    model: str = "plumb_bob"

    def __post_init__(self) -> None:
        if self.fx <= 0.0 or self.fy <= 0.0:
            raise ValueError(f"焦距必须为正：fx={self.fx}, fy={self.fy}")
        if self.model != "plumb_bob":
            raise ValueError(f"暂只支持 plumb_bob 畸变模型：{self.model!r}")
        if not math.isfinite(self.k1 + self.k2 + self.p1 + self.p2):
            raise ValueError("畸变系数必须是有限数")

    @property
    def has_distortion(self) -> bool:
        return any(value != 0.0 for value in (self.k1, self.k2, self.p1, self.p2))

    # --- 归一化坐标上的正向 / 反向映射 -------------------------------------------------

    def _radial(self, r2: float) -> float:
        factor = 1.0 + self.k1 * r2 + self.k2 * r2 * r2
        if abs(factor) > MAX_RADIAL_FACTOR:
            raise ValueError(
                f"径向缩放因子 {factor:.3f} 超出 ±{MAX_RADIAL_FACTOR}：r²={r2:.4f}，"
                f"k1={self.k1}，k2={self.k2}"
            )
        return factor

    def _tangential(self, x: float, y: float, r2: float) -> Point:
        dx = 2.0 * self.p1 * x * y + self.p2 * (r2 + 2.0 * x * x)
        dy = self.p1 * (r2 + 2.0 * y * y) + 2.0 * self.p2 * x * y
        return dx, dy

    def distort_normalized(self, x: float, y: float) -> Point:
        """归一化坐标（理想）→ 归一化坐标（畸变后）。"""
        r2 = x * x + y * y
        radial = self._radial(r2)
        dx, dy = self._tangential(x, y, r2)
        return x * radial + dx, y * radial + dy

    def undistort_normalized(self, xd: float, yd: float) -> Point:
        """归一化坐标（畸变后）→ 归一化坐标（理想），不动点迭代。"""
        x, y = xd, yd
        for _ in range(UNDISTORT_ITERATIONS):
            r2 = x * x + y * y
            radial = self._radial(r2)
            dx, dy = self._tangential(x, y, r2)
            nx = (xd - dx) / radial
            ny = (yd - dy) / radial
            done = abs(nx - x) <= UNDISTORT_TOLERANCE and abs(ny - y) <= UNDISTORT_TOLERANCE
            x, y = nx, ny
            if done:
                break
        return x, y

    # --- 像素坐标 ---------------------------------------------------------------------

    def distort(self, u: float, v: float) -> Point:
        """理想像素坐标 → 拍摄像素坐标。"""
        xd, yd = self.distort_normalized((u - self.cx) / self.fx, (v - self.cy) / self.fy)
        return self.fx * xd + self.cx, self.fy * yd + self.cy

    def undistort(self, u: float, v: float) -> Point:
        """拍摄像素坐标 → 理想像素坐标。"""
        x, y = self.undistort_normalized((u - self.cx) / self.fx, (v - self.cy) / self.fy)
        return self.fx * x + self.cx, self.fy * y + self.cy

    # --- 序列化 -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """按 PROTOCOL §2 的形状：内参一个对象，畸变一个对象。"""
        return {
            "intrinsics": {
                "fx": round(self.fx, 6),
                "fy": round(self.fy, 6),
                "cx": round(self.cx, 6),
                "cy": round(self.cy, 6),
            },
            "distortion": {
                "model": self.model,
                "k1": round(self.k1, 9),
                "k2": round(self.k2, 9),
                "p1": round(self.p1, 9),
                "p2": round(self.p2, 9),
            },
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "LensModel":
        intr = document["intrinsics"]
        dist = document.get("distortion") or {}
        return cls(
            fx=float(intr["fx"]),
            fy=float(intr["fy"]),
            cx=float(intr["cx"]),
            cy=float(intr["cy"]),
            k1=float(dist.get("k1", 0.0)),
            k2=float(dist.get("k2", 0.0)),
            p1=float(dist.get("p1", 0.0)),
            p2=float(dist.get("p2", 0.0)),
            model=str(dist.get("model", "plumb_bob")),
        )

    def with_distortion(
        self, *, k1: float = 0.0, k2: float = 0.0, p1: float = 0.0, p2: float = 0.0
    ) -> "LensModel":
        return LensModel(
            fx=self.fx, fy=self.fy, cx=self.cx, cy=self.cy,
            k1=k1, k2=k2, p1=p1, p2=p2, model=self.model,
        )

    def ideal(self) -> "LensModel":
        """同内参、零畸变（针孔）模型。"""
        return LensModel(fx=self.fx, fy=self.fy, cx=self.cx, cy=self.cy)

    def describe(self) -> str:
        return (
            f"fx={self.fx:.3f} fy={self.fy:.3f} cx={self.cx:.3f} cy={self.cy:.3f} "
            f"k1={self.k1:+.6f} k2={self.k2:+.6f} p1={self.p1:+.7f} p2={self.p2:+.7f}"
        )


# --- 重采样 ---------------------------------------------------------------------------


def _bilinear(raster: Raster, x: float, y: float) -> float:
    """以像素中心为采样点的双线性插值（越界按边缘夹取）。"""
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


def grid_vertices(size: int, step: int) -> list[int]:
    """查找表顶点位置：``0, step, 2·step, …`` 且最后一个顶点正好是 ``size - 1``。"""
    if size <= 1:
        return [0]
    vertices = list(range(0, size, step))
    if vertices[-1] != size - 1:
        vertices.append(size - 1)
    return vertices


def map_grid(
    width: int, height: int, mapping, *, step: int = MAP_GRID_PX
) -> tuple[list[int], list[int], list[float], list[float]]:
    """把逐像素映射采样到一张粗网格上，返回 ``(col_x, row_y, map_x, map_y)``。

    ``mapping(u, v)`` 给出该像素在**源图**里的采样坐标（以像素中心为口径）。
    """
    if step < 1:
        raise ValueError(f"step 必须 >= 1：{step}")
    col_x = grid_vertices(width, step)
    row_y = grid_vertices(height, step)
    map_x: list[float] = []
    map_y: list[float] = []
    for y in row_y:
        sy = y + 0.5
        for x in col_x:
            mx, my = mapping(x + 0.5, sy)
            map_x.append(mx)
            map_y.append(my)
    return col_x, row_y, map_x, map_y


def _grid_maps(
    width: int, height: int, col_x: list[int], row_y: list[int], map_x: list[float], map_y: list[float]
) -> tuple[list[float], list[float]]:
    """把粗网格双线性插值回每个像素（行优先，长度 ``width * height``）。"""
    cols = len(col_x)
    out_x = [0.0] * (width * height)
    out_y = [0.0] * (width * height)
    # 预先算好每列的 (左顶点下标, 右顶点下标, 权重)，每行复用。
    columns: list[tuple[int, int, float]] = []
    for x in range(width):
        gx = 0
        while gx + 2 < cols and col_x[gx + 1] <= x:
            gx += 1
        x0, x1 = col_x[gx], col_x[min(gx + 1, cols - 1)]
        tx = 0.0 if x1 <= x0 else (x - x0) / (x1 - x0)
        columns.append((gx, min(gx + 1, cols - 1), tx))
    for y in range(height):
        gy = 0
        while gy + 2 < len(row_y) and row_y[gy + 1] <= y:
            gy += 1
        y0, y1 = row_y[gy], row_y[min(gy + 1, len(row_y) - 1)]
        ty = 0.0 if y1 <= y0 else (y - y0) / (y1 - y0)
        gy1 = min(gy + 1, len(row_y) - 1)
        row0 = gy * cols
        row1 = gy1 * cols
        base = y * width
        for x, (gx, gx1, tx) in enumerate(columns):
            wx00 = (1.0 - tx) * (1.0 - ty)
            wx10 = tx * (1.0 - ty)
            wx01 = (1.0 - tx) * ty
            wx11 = tx * ty
            out_x[base + x] = (
                map_x[row0 + gx] * wx00
                + map_x[row0 + gx1] * wx10
                + map_x[row1 + gx] * wx01
                + map_x[row1 + gx1] * wx11
            )
            out_y[base + x] = (
                map_y[row0 + gx] * wx00
                + map_y[row0 + gx1] * wx10
                + map_y[row1 + gx] * wx01
                + map_y[row1 + gx1] * wx11
            )
    return out_x, out_y


def _resample(raster: Raster, mapping) -> Raster:
    col_x, row_y, map_x, map_y = map_grid(raster.width, raster.height, mapping)
    out_x, out_y = _grid_maps(raster.width, raster.height, col_x, row_y, map_x, map_y)
    width, height = raster.width, raster.height
    out = Raster.filled(width, height, 0)
    pixels = out.pixels
    for y in range(height):
        base = y * width
        for x in range(width):
            value = _bilinear(raster, out_x[base + x], out_y[base + x])
            if value <= 0.0:
                pixels[base + x] = 0
            elif value >= 255.0:
                pixels[base + x] = 255
            else:
                pixels[base + x] = int(value + 0.5)
    return out


def distort_raster(ideal: Raster, lens: LensModel) -> Raster:
    """理想针孔图 → 带畸变的拍摄图（对每个像素取它在理想图里的位置采样）。"""
    if not lens.has_distortion:
        return ideal.copy()
    return _resample(ideal, lens.undistort)


def undistort_raster(distorted: Raster, lens: LensModel) -> Raster:
    """带畸变的拍摄图 → 理想针孔图（LB-031 的「校正」输出）。"""
    if not lens.has_distortion:
        return distorted.copy()
    return _resample(distorted, lens.distort)


def distort_points(lens: LensModel, points: list[Point]) -> list[Point]:
    return [lens.distort(x, y) for x, y in points]


def undistort_points(lens: LensModel, points: list[Point]) -> list[Point]:
    return [lens.undistort(x, y) for x, y in points]
