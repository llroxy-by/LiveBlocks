"""相机模型与单应变换（纯标准库，全部用 ``float`` 显式运算以保证确定性）。

坐标系约定（与 ``docs/PROTOCOL.md`` §0 一致的部分）：

- 图像坐标：原点在左上角，x 向右、y 向下，单位为像素；像素 ``(x, y)`` 的中心在
  ``(x + 0.5, y + 0.5)``。
- 卡面（毫米）：原点在卡片左上角、x 向右 y 向下，由 ``CardTexture`` 换算到以卡片
  中心为原点的毫米坐标后再做投影。
- 相机坐标：原点在光心，x 向右、y 向下、z 沿光轴指向场景；``z_mm`` 是卡片中心到
  相机的深度。

单应矩阵 ``H`` 把「卡片中心坐标系下的毫米坐标 ``(u, v, 1)``」映射到图像齐次坐标，
即 ``x = (H·[u, v, 1]) / z``。``H = K · [r1 r2 t]``，其中 ``r1, r2`` 是卡片姿态旋转
矩阵的前两列，``t`` 是卡片中心的相机坐标。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Point2 = tuple[float, float]


@dataclass(frozen=True)
class Camera:
    """针孔相机内参（无畸变；畸变留给 LB-010 的标定任务）。"""

    fx: float
    fy: float
    cx: float
    cy: float

    def projection_matrix(self) -> Matrix3:
        return (
            (self.fx, 0.0, self.cx),
            (0.0, self.fy, self.cy),
            (0.0, 0.0, 1.0),
        )


def mat_mul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def mat_inv(m: Matrix3) -> Matrix3:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise ValueError(f"单应矩阵不可逆（det={det!r}）")
    inv_det = 1.0 / det
    return (
        (
            (e * i - f * h) * inv_det,
            (c * h - b * i) * inv_det,
            (b * f - c * e) * inv_det,
        ),
        (
            (f * g - d * i) * inv_det,
            (a * i - c * g) * inv_det,
            (c * d - a * f) * inv_det,
        ),
        (
            (d * h - e * g) * inv_det,
            (b * g - a * h) * inv_det,
            (a * e - b * d) * inv_det,
        ),
    )


def apply_homography(m: Matrix3, u: float, v: float) -> Point2:
    x = m[0][0] * u + m[0][1] * v + m[0][2]
    y = m[1][0] * u + m[1][1] * v + m[1][2]
    z = m[2][0] * u + m[2][1] * v + m[2][2]
    if abs(z) < 1e-12:
        raise ValueError("齐次坐标 w≈0：该点无法投影（相机平面退化）")
    return (x / z, y / z)


def _rot_x(deg: float) -> Matrix3:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))


def _rot_y(deg: float) -> Matrix3:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))


def _rot_z(deg: float) -> Matrix3:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


@dataclass(frozen=True)
class Pose:
    """卡片在相机坐标系里的位姿。

    - ``x_mm`` / ``y_mm``：卡片中心相对光轴的横向偏移（右/下为正）。
    - ``z_mm``：卡片中心到相机的深度。
    - ``yaw_deg``：绕卡片法向（正对相机时即图像平面法向）的旋转，也就是卡片在桌面上的
      「转了多少度」。
    - ``pitch_deg``：绕卡片 x 轴的倾斜；``roll_deg``：绕卡片 y 轴的倾斜。二者叠加即
      「任意角度」里的透视倾斜。
    """

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 300.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    def rotation(self) -> Matrix3:
        # 先倾斜（pitch/roll）再在自身平面内转（yaw）。
        return mat_mul(_rot_z(self.yaw_deg), mat_mul(_rot_x(self.pitch_deg), _rot_y(self.roll_deg)))

    def to_dict(self) -> dict[str, float]:
        return {
            "x_mm": round(self.x_mm, 3),
            "y_mm": round(self.y_mm, 3),
            "z_mm": round(self.z_mm, 3),
            "yaw_deg": round(self.yaw_deg, 3),
            "pitch_deg": round(self.pitch_deg, 3),
            "roll_deg": round(self.roll_deg, 3),
        }


def plane_homography(camera: Camera, pose: Pose) -> Matrix3:
    """卡片中心毫米坐标 → 图像像素 的单应矩阵。"""
    if pose.z_mm <= 0:
        raise ValueError(f"z_mm 必须为正：{pose.z_mm}")
    r = pose.rotation()
    plane_to_camera: Matrix3 = (
        (r[0][0], r[0][1], pose.x_mm),
        (r[1][0], r[1][1], pose.y_mm),
        (r[2][0], r[2][1], pose.z_mm),
    )
    return mat_mul(camera.projection_matrix(), plane_to_camera)


def texture_homography(
    camera: Camera,
    pose: Pose,
    *,
    width_px: int,
    height_px: int,
    pixels_per_mm: float,
) -> Matrix3:
    """卡片纹理像素坐标 → 图像像素 的单应矩阵。"""
    plane = plane_homography(camera, pose)
    tex_to_plane: Matrix3 = (
        (1.0 / pixels_per_mm, 0.0, -0.5 * width_px / pixels_per_mm),
        (0.0, 1.0 / pixels_per_mm, -0.5 * height_px / pixels_per_mm),
        (0.0, 0.0, 1.0),
    )
    return mat_mul(plane, tex_to_plane)


def card_corners_px(
    camera: Camera, pose: Pose, *, width_mm: float, height_mm: float
) -> list[Point2]:
    """卡片四角在图像里的坐标，顺序固定为左上、右上、右下、左下（卡片自身坐标系）。"""
    h = plane_homography(camera, pose)
    hw, hh = width_mm / 2.0, height_mm / 2.0
    return [
        apply_homography(h, -hw, -hh),
        apply_homography(h, hw, -hh),
        apply_homography(h, hw, hh),
        apply_homography(h, -hw, hh),
    ]


def marker_corners_px(
    camera: Camera,
    pose: Pose,
    *,
    card_width_mm: float,
    card_height_mm: float,
    marker_center_mm: tuple[float, float],
    marker_size_mm: float,
) -> list[Point2]:
    """标记四角在图像里的坐标，顺序固定为左上、右上、右下、左下（**标记**自身坐标系）。

    与 ``docs/PROTOCOL.md`` §3.1 的 ``corners_px`` 语义一致：角点顺序跟标记坐标系走，
    不按「图像上方」解释。
    """
    h = plane_homography(camera, pose)
    cu = marker_center_mm[0] - card_width_mm / 2.0
    cv = marker_center_mm[1] - card_height_mm / 2.0
    half = marker_size_mm / 2.0
    return [
        apply_homography(h, cu - half, cv - half),
        apply_homography(h, cu + half, cv - half),
        apply_homography(h, cu + half, cv + half),
        apply_homography(h, cu - half, cv + half),
    ]


def in_plane_angle_deg(corners: list[Point2]) -> float:
    """由角点算卡片在图像里的面内旋转角（度，y 向下坐标系里顺时针为正）。"""
    (x0, y0), (x1, y1) = corners[0], corners[1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0))


def point_in_convex_quad(point: Point2, quad: list[Point2]) -> bool:
    """点是否落在凸四边形内（含边界）。"""
    x, y = point
    sign = 0
    n = len(quad)
    for i in range(n):
        x0, y0 = quad[i]
        x1, y1 = quad[(i + 1) % n]
        cross = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
        if cross > 1e-9:
            if sign < 0:
                return False
            sign = 1
        elif cross < -1e-9:
            if sign > 0:
                return False
            sign = -1
    return True


def point_in_card(point: Point2, inverse_homography: Matrix3, *, width_mm: float, height_mm: float) -> bool:
    """点是否落在某张卡片范围内（用该卡的逆单应把点映射回卡片中心毫米坐标）。"""
    try:
        u, v = apply_homography(inverse_homography, *point)
    except ValueError:
        return False
    return abs(u) <= width_mm / 2.0 and abs(v) <= height_mm / 2.0
