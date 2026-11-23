"""识别层基础几何与二值化：积分图、连通域、凸包化简、亚像素角点细化。"""

from __future__ import annotations

import math

import pytest

from liveblocks.vision import GrayImage
from liveblocks.vision.image import (
    adaptive_binary,
    connected_components,
    convex_hull,
    integral_image,
    is_convex_quad,
    outside_frame,
    point_in_quad,
    polygon_area,
    quad_iou,
    refine_quad,
    side_lengths,
    simplify_to_quad,
)


def make_image(width: int, height: int, value: int = 0) -> GrayImage:
    return GrayImage(width, height, bytes([value]) * (width * height))


def paint_rect(image: GrayImage, x0: int, y0: int, x1: int, y1: int, value: int) -> GrayImage:
    pixels = bytearray(image.pixels)
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[y * image.width + x] = value
    return GrayImage(image.width, image.height, bytes(pixels))


def test_integral_image_sums_rectangles() -> None:
    image = GrayImage(3, 2, bytes([1, 2, 3, 4, 5, 6]))
    table = integral_image(image)
    stride = 4
    # 右下 2x1 区域（第二行的后两列）= 5 + 6
    assert table[2 * stride + 3] - table[2 * stride + 1] - table[1 * stride + 3] + table[1 * stride + 1] == 11
    # 整幅图求和 = 21
    assert table[2 * stride + 3] == 21


def test_adaptive_binary_finds_thin_dark_strokes() -> None:
    """局部均值二值化能挑出「比邻域暗」的细笔画（ArUco 黑边与黑模块就是这种形状）。

    注意：局部均值法**不能**识别「大片均匀暗区」的内部（窗口整体落在暗区里时均值就等于自身），
    这对本任务是可接受的——标记的黑边只有 1 个模块宽，卡片页面底色是亮的。
    """
    image = make_image(40, 40, 200)
    image = paint_rect(image, 18, 5, 22, 35, 20)  # 4 px 宽的竖条
    mask = adaptive_binary(image, block=11, offset=8)
    assert mask[20 * 40 + 20] == 1  # 竖条中心
    assert mask[2 * 40 + 2] == 0  # 角落背景
    assert sum(mask) == 4 * 30


def test_connected_components_separates_blobs_and_filters_small() -> None:
    image = make_image(30, 30, 200)
    image = paint_rect(image, 3, 2, 6, 22, 10)  # 3 x 20 = 60 px 竖条
    image = paint_rect(image, 20, 20, 22, 22, 10)  # 2 x 2 = 4 px 小块
    mask = adaptive_binary(image, block=9, offset=8)
    components = connected_components(mask, 30, 30, min_area=10)
    assert len(components) == 1
    assert len(components[0]) == 60


def test_convex_hull_and_quad_simplify_square() -> None:
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (5.0, 0.0), (5.0, 10.0)]
    hull = convex_hull(points)
    quad = simplify_to_quad(hull)
    assert len(quad) == 4
    assert is_convex_quad(quad)
    assert polygon_area(quad) == pytest.approx(100.0)
    assert all(side == pytest.approx(10.0) for side in side_lengths(quad))


def test_simplify_to_quad_on_rotated_square() -> None:
    angle = math.radians(17.0)
    points = [
        (5.0 + 8.0 * math.cos(angle) * sx - 8.0 * math.sin(angle) * sy,
         5.0 + 8.0 * math.sin(angle) * sx + 8.0 * math.cos(angle) * sy)
        for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
    ]
    quad = simplify_to_quad(convex_hull(points))
    assert len(quad) == 4
    assert is_convex_quad(quad)
    assert polygon_area(quad) == pytest.approx(256.0, rel=1e-6)
    for side in side_lengths(quad):
        assert side == pytest.approx(16.0, rel=1e-6)


def test_point_in_quad_and_outside_frame() -> None:
    quad = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert point_in_quad((5.0, 5.0), quad)
    assert point_in_quad((0.0, 5.0), quad)
    assert not point_in_quad((10.5, 5.0), quad)
    flags = outside_frame([(5.0, 5.0), (-1.0, 5.0), (640.0, 5.0)], 640, 480)
    assert flags == [False, True, True]  # 与 LB-002 真值同口径：x == width 已经算越界


def test_quad_iou_bounds() -> None:
    a = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    b = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    c = [(100.0, 100.0), (110.0, 100.0), (110.0, 110.0), (100.0, 110.0)]
    assert quad_iou(a, b) == pytest.approx(1.0)
    assert quad_iou(a, c) == 0.0


def test_refine_quad_recovers_subpixel_edges() -> None:
    """画一个带抗锯齿边缘的方块，检查细化能把角点找回亚像素位置。"""
    width = height = 64
    # 真值方块：左边界 12.4、右边界 47.6、上边界 15.3、下边界 48.1（全部非整数）
    left, right, top, bottom = 12.4, 47.6, 15.3, 48.1
    pixels = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            # 每个像素按覆盖率把黑白混合，制造真实的亚像素过渡
            overlap_x = max(0.0, min(right, x + 1.0) - max(left, float(x)))
            overlap_y = max(0.0, min(bottom, y + 1.0) - max(top, float(y)))
            coverage = overlap_x * overlap_y
            pixels[y * width + x] = int(round(20.0 + (230.0 - 20.0) * (1.0 - coverage)))
    image = GrayImage(width, height, bytes(pixels))
    rough = [(12.0, 15.0), (48.0, 15.0), (48.0, 48.0), (12.0, 48.0)]
    refined = refine_quad(image, rough, black_level=20.0, white_level=230.0)
    expected = [(left, top), (right, top), (right, bottom), (left, bottom)]
    for found, want in zip(refined, expected):
        assert found == pytest.approx(want, abs=0.25)


def test_refine_quad_keeps_rough_quad_when_evidence_is_missing() -> None:
    """整幅全黑（没有黑白过渡）时不应凭空造角点。"""
    image = make_image(32, 32, 10)
    rough = [(8.0, 8.0), (24.0, 8.0), (24.0, 24.0), (8.0, 24.0)]
    assert refine_quad(image, rough, black_level=10.0, white_level=10.0) == rough
