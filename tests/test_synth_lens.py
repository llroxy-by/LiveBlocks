"""LB-031 镜头畸变模型（``liveblocks.synth.lens``）的单元测试。

模型是「理想针孔图 ↔ 带畸变的拍摄图」之间的映射，测试盯三件事：

1. 正反解自洽（``undistort(distort(p)) ≈ p``）；
2. 重采样能真正把畸变加上去 / 拿下来（用一条已知的水平黑带做「直线还是弯的」检验）；
3. 一切确定性：同样的输入两次跑出同样的字节。
"""

from __future__ import annotations

import math

import pytest

from liveblocks.synth.lens import (
    MAP_GRID_PX,
    LensModel,
    _grid_maps,
    distort_raster,
    map_grid,
    undistort_points,
    undistort_raster,
)
from liveblocks.synth.raster import Raster

LENS = LensModel(fx=230.0, fy=231.0, cx=119.5, cy=89.5, k1=-0.2, k2=0.05, p1=0.004, p2=-0.003)


def test_zero_distortion_is_identity() -> None:
    lens = LensModel(fx=100.0, fy=100.0, cx=50.0, cy=40.0)
    assert not lens.has_distortion
    for point in ((0.5, 0.5), (99.5, 79.5), (37.25, 12.75)):
        assert lens.distort(*point) == pytest.approx(point, abs=1e-12)
        assert lens.undistort(*point) == pytest.approx(point, abs=1e-12)


def test_distort_and_undistort_round_trip() -> None:
    points = [(0.5, 0.5), (239.5, 179.5), (12.0, 170.0), (230.0, 8.0), (119.5, 89.5)]
    back = undistort_points(LENS, [LENS.distort(*point) for point in points])
    for original, restored in zip(points, back):
        assert restored == pytest.approx(original, abs=1e-6)


def test_negative_k1_pulls_periphery_inward() -> None:
    """桶形畸变（k1 < 0）把边缘的点向主点方向拉近：半径变小。"""
    cx, cy = LENS.cx, LENS.cy
    edge = (239.5, 0.5)
    distorted = LENS.distort(*edge)
    before = math.hypot(edge[0] - cx, edge[1] - cy)
    after = math.hypot(distorted[0] - cx, distorted[1] - cy)
    assert after < before
    center = (cx, cy)
    assert LENS.distort(*center) == pytest.approx(center, abs=1e-9)


def test_map_grid_interpolation_error_is_subpixel() -> None:
    """查找表（间距 MAP_GRID_PX）双线性插值回逐像素的误差必须远小于 0.1 px。"""
    width, height = 240, 180
    col_x, row_y, map_x, map_y = map_grid(width, height, LENS.undistort, step=MAP_GRID_PX)
    grid_x, grid_y = _grid_maps(width, height, col_x, row_y, map_x, map_y)
    worst = 0.0
    for y in range(0, height, 11):
        for x in range(0, width, 11):
            exact = LENS.undistort(x + 0.5, y + 0.5)
            worst = max(worst, math.hypot(grid_x[y * width + x] - exact[0], grid_y[y * width + x] - exact[1]))
    assert worst < 0.05


def _horizontal_bar(width: int, height: int, row: int, thickness: int) -> Raster:
    raster = Raster.filled(width, height, 240)
    for y in range(row, min(height, row + thickness)):
        for x in range(width):
            raster.pixels[y * width + x] = 20
    return raster


def _bar_rows(raster: Raster, *, level: float = 130.0) -> list[float]:
    """每一列上「黑带中心行」的估计（用于看它是直的还是弯的）。"""
    rows: list[float] = []
    width, height = raster.width, raster.height
    for x in range(width):
        best: float | None = None
        for y in range(height):
            if raster.pixel(x, y) <= level:
                best = float(y)
                break
        rows.append(best if best is not None else math.nan)
    return rows


def test_distort_raster_bends_a_straight_bar() -> None:
    """加上桶形畸变后，靠近边缘的一条水平黑带会弯（这正是要标定出来的东西）。"""
    ideal = _horizontal_bar(240, 180, row=20, thickness=6)
    distorted = distort_raster(ideal, LENS)
    rows = _bar_rows(distorted)
    middle = rows[120]
    left = rows[4]
    right = rows[236]
    assert not any(math.isnan(value) for value in (middle, left, right))
    assert abs(left - middle) > 1.0 or abs(right - middle) > 1.0


def test_undistort_raster_removes_the_bend() -> None:
    """先加畸变再校正，黑带回到接近直线的位置（中间区域误差 < 1 px）。"""
    ideal = _horizontal_bar(240, 180, row=20, thickness=6)
    distorted = distort_raster(ideal, LENS)
    corrected = undistort_raster(distorted, LENS)
    rows = _bar_rows(corrected)
    middle = rows[120]
    near_edges = [rows[x] for x in (40, 60, 180, 200)]
    assert all(abs(value - middle) < 1.0 for value in near_edges)


def test_resampling_is_deterministic() -> None:
    ideal = _horizontal_bar(240, 180, row=60, thickness=5)
    first = distort_raster(ideal, LENS).as_bytes()
    second = distort_raster(ideal, LENS).as_bytes()
    assert first == second
    assert undistort_raster(ideal, LENS).as_bytes() == undistort_raster(ideal, LENS).as_bytes()


def test_out_of_range_coefficients_raise() -> None:
    wild = LensModel(fx=100.0, fy=100.0, cx=50.0, cy=50.0, k1=-100.0)
    with pytest.raises(ValueError, match="径向缩放因子"):
        wild.distort(99.0, 1.0)


def test_invalid_model_raises() -> None:
    with pytest.raises(ValueError, match="plumb_bob"):
        LensModel(fx=100.0, fy=100.0, cx=50.0, cy=50.0, model="fisheye")
    with pytest.raises(ValueError, match="焦距"):
        LensModel(fx=0.0, fy=100.0, cx=50.0, cy=50.0)


def test_dict_round_trip_keeps_five_fields() -> None:
    document = LENS.to_dict()
    assert document["distortion"]["model"] == "plumb_bob"
    assert set(document["distortion"]) == {"model", "k1", "k2", "p1", "p2"}
    restored = LensModel.from_dict(document)
    assert restored.distort(10.0, 10.0) == pytest.approx(LENS.distort(10.0, 10.0))
