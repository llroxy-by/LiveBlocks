"""几何与单应变换测试：投影语义、角点顺序、可逆性。"""

from __future__ import annotations

import math

import pytest

from liveblocks.synth import geometry
from liveblocks.synth.geometry import Camera, Pose

CAMERA = Camera(fx=700.0, fy=700.0, cx=320.0, cy=240.0)


def test_card_center_projects_to_principal_point() -> None:
    pose = Pose(z_mm=300.0)
    assert geometry.apply_homography(geometry.plane_homography(CAMERA, pose), 0.0, 0.0) == pytest.approx(
        (320.0, 240.0)
    )


def test_scale_is_focal_over_depth() -> None:
    pose = Pose(z_mm=350.0)
    homography = geometry.plane_homography(CAMERA, pose)
    x, y = geometry.apply_homography(homography, 10.0, 0.0)
    assert x == pytest.approx(320.0 + 10.0 * 700.0 / 350.0)
    assert y == pytest.approx(240.0)


def test_translation_shifts_by_focal_over_depth() -> None:
    pose = Pose(x_mm=20.0, y_mm=-10.0, z_mm=200.0)
    x, y = geometry.apply_homography(geometry.plane_homography(CAMERA, pose), 0.0, 0.0)
    assert x == pytest.approx(320.0 + 20.0 * 700.0 / 200.0)
    assert y == pytest.approx(240.0 - 10.0 * 700.0 / 200.0)


def test_yaw_rotates_corners() -> None:
    straight = geometry.card_corners_px(CAMERA, Pose(z_mm=300.0), width_mm=60.0, height_mm=40.0)
    rotated = geometry.card_corners_px(CAMERA, Pose(z_mm=300.0, yaw_deg=90.0), width_mm=60.0, height_mm=40.0)
    # 90° 后面内角从 0 变成 90（图像 y 向下，顺时针为正）
    assert geometry.in_plane_angle_deg(straight) == pytest.approx(0.0, abs=1e-9)
    assert geometry.in_plane_angle_deg(rotated) == pytest.approx(90.0, abs=1e-9)
    # 面积保持不变
    assert _quad_area(rotated) == pytest.approx(_quad_area(straight), rel=1e-9)


def test_pitch_shrinks_area_but_keeps_center() -> None:
    flat = geometry.card_corners_px(CAMERA, Pose(z_mm=300.0), width_mm=60.0, height_mm=40.0)
    tilted = geometry.card_corners_px(
        CAMERA, Pose(z_mm=300.0, pitch_deg=30.0), width_mm=60.0, height_mm=40.0
    )
    assert _quad_area(tilted) < _quad_area(flat)
    # 正对时四角质心就是主点；倾斜后透视会让质心偏移，但左右对称性仍在
    flat_center = (
        sum(point[0] for point in flat) / 4.0,
        sum(point[1] for point in flat) / 4.0,
    )
    assert flat_center == pytest.approx((320.0, 240.0), abs=1e-6)
    tilted_center_x = sum(point[0] for point in tilted) / 4.0
    assert tilted_center_x == pytest.approx(320.0, abs=1e-6)


def test_corner_order_is_tl_tr_br_bl() -> None:
    corners = geometry.card_corners_px(CAMERA, Pose(z_mm=300.0), width_mm=60.0, height_mm=40.0)
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = corners
    assert x0 < x1 and x3 < x2  # 左边先、右边后
    assert y0 < y3 and y1 < y2  # 上边先、下边后
    assert (x0, y0) == pytest.approx((320.0 - 70.0, 240.0 - 140.0 / 3.0))


def test_marker_corners_follow_marker_frame() -> None:
    pose = Pose(z_mm=300.0)
    corners = geometry.marker_corners_px(
        CAMERA,
        pose,
        card_width_mm=60.0,
        card_height_mm=40.0,
        marker_center_mm=(30.0, 15.5),
        marker_size_mm=24.0,
    )
    assert len(corners) == 4
    scale = 700.0 / 300.0
    # 标记中心相对卡片中心是 (0, -4.5) mm
    center = (sum(p[0] for p in corners) / 4.0, sum(p[1] for p in corners) / 4.0)
    assert center == pytest.approx((320.0, 240.0 - 4.5 * scale))
    # 边长 = 24mm * scale
    assert corners[1][0] - corners[0][0] == pytest.approx(24.0 * scale)


def test_homography_inverse_roundtrip() -> None:
    pose = Pose(x_mm=12.0, y_mm=-7.0, z_mm=420.0, yaw_deg=7.0, pitch_deg=5.0, roll_deg=-3.0)
    homography = geometry.plane_homography(CAMERA, pose)
    inverse = geometry.mat_inv(homography)
    for point in ((0.0, 0.0), (30.0, 20.0), (-25.5, 8.25)):
        x, y = geometry.apply_homography(homography, *point)
        u, v = geometry.apply_homography(inverse, x, y)
        assert (u, v) == pytest.approx(point, abs=1e-9)


def test_mat_inv_rejects_singular_matrix() -> None:
    with pytest.raises(ValueError, match="不可逆"):
        geometry.mat_inv(((1.0, 2.0, 3.0), (2.0, 4.0, 6.0), (0.0, 0.0, 1.0)))


def test_point_in_convex_quad() -> None:
    quad = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert geometry.point_in_convex_quad((5.0, 5.0), quad)
    assert not geometry.point_in_convex_quad((11.0, 5.0), quad)
    assert not geometry.point_in_convex_quad((-1.0, 5.0), quad)


def test_point_in_card_uses_card_frame() -> None:
    pose = Pose(z_mm=300.0, yaw_deg=30.0)
    homography = geometry.plane_homography(CAMERA, pose)
    inverse = geometry.mat_inv(homography)
    inside = geometry.apply_homography(homography, 25.0, 15.0)
    outside = geometry.apply_homography(homography, 40.0, 0.0)
    assert geometry.point_in_card(inside, inverse, width_mm=60.0, height_mm=40.0)
    assert not geometry.point_in_card(outside, inverse, width_mm=60.0, height_mm=40.0)


def test_pose_dict_is_rounded() -> None:
    pose = Pose(x_mm=1.23456, y_mm=-0.0000001, z_mm=300.0004, yaw_deg=1.0 / 3.0)
    data = pose.to_dict()
    assert data["x_mm"] == 1.235
    assert data["y_mm"] == 0.0
    assert data["z_mm"] == 300.0


def test_negative_depth_is_rejected() -> None:
    with pytest.raises(ValueError, match="z_mm 必须为正"):
        geometry.plane_homography(CAMERA, Pose(z_mm=-1.0))


def _quad_area(corners: list[tuple[float, float]]) -> float:
    area = 0.0
    for index, (x0, y0) in enumerate(corners):
        x1, y1 = corners[(index + 1) % len(corners)]
        area += x0 * y1 - x1 * y0
    return abs(area) / 2.0


def test_rotation_matrix_is_orthonormal() -> None:
    rotation = Pose(yaw_deg=15.0, pitch_deg=8.0, roll_deg=-5.0).rotation()
    for i in range(3):
        for j in range(3):
            dot = sum(rotation[k][i] * rotation[k][j] for k in range(3))
            assert dot == pytest.approx(1.0 if i == j else 0.0, abs=1e-12)
    determinant = (
        rotation[0][0] * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )
    assert determinant == pytest.approx(1.0, abs=1e-12)


def test_yaw_90_maps_card_x_axis_to_image_y_axis() -> None:
    pose = Pose(z_mm=300.0, yaw_deg=90.0)
    homography = geometry.plane_homography(CAMERA, pose)
    origin = geometry.apply_homography(homography, 0.0, 0.0)
    along_x = geometry.apply_homography(homography, 10.0, 0.0)
    assert origin == pytest.approx((320.0, 240.0))
    assert along_x[0] == pytest.approx(320.0, abs=1e-9)
    assert along_x[1] > origin[1] + 20.0
    assert math.degrees(math.atan2(along_x[1] - origin[1], along_x[0] - origin[0])) == pytest.approx(90.0)
