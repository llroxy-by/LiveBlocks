"""由标记四边形估计位姿（针孔 + 平面单应分解）。

`docs/PROTOCOL.md` §3.1 的 ``pose`` 是可选信息：**只有给了相机内参**（``Frame.intrinsics``）才能
算，否则输出里不带 ``pose``。LB-003 阶段的内参来自合成场景配置；LB-010 之后由标定文件提供。

做法是教科书式的平面单应分解：``H = K · [r1 r2 t]``，取 ``M = K⁻¹H``，``λ = (|m1| + |m2|)/2``，
``t = m3/λ``，``r1 = m1/λ``、``r2 = m2/λ``，再用 ``r1 × r2`` 正交化。合成图无畸变、无噪声级
误差，实测与真值位姿一致到 1% 量级（见 ``tests/test_vision_pose.py``），但**没有做非线性
精化**，真实相机上只能当粗值——这正是 LB-010 的活。
"""

from __future__ import annotations

import math

from ..synth.geometry import Matrix3, apply_homography
from .decode import homography
from .frame import Intrinsics
from .image import Point


def marker_corners_mm(marker_size_mm: float) -> list[Point]:
    """标记自身坐标系（原点在标记中心、x 右 y 下）下的四个角，顺序左上/右上/右下/左下。"""
    half = marker_size_mm / 2.0
    return [(-half, -half), (half, -half), (half, half), (-half, half)]


def homography_from_marker_frame(corners_px: list[Point], marker_size_mm: float) -> Matrix3:
    """标记毫米坐标 → 图像像素 的单应矩阵。"""
    return homography(marker_corners_mm(marker_size_mm), list(corners_px))


def pose_from_homography(matrix: Matrix3, intrinsics: Intrinsics) -> dict[str, float]:
    """把「标记毫米坐标 → 图像像素」的单应分解成相机坐标位姿。"""
    k_inv: Matrix3 = (
        (1.0 / intrinsics.fx, 0.0, -intrinsics.cx / intrinsics.fx),
        (0.0, 1.0 / intrinsics.fy, -intrinsics.cy / intrinsics.fy),
        (0.0, 0.0, 1.0),
    )
    m = _mat_mul(k_inv, matrix)
    m1 = (m[0][0], m[1][0], m[2][0])
    m2 = (m[0][1], m[1][1], m[2][1])
    m3 = (m[0][2], m[1][2], m[2][2])
    norm1 = math.sqrt(m1[0] ** 2 + m1[1] ** 2 + m1[2] ** 2)
    norm2 = math.sqrt(m2[0] ** 2 + m2[1] ** 2 + m2[2] ** 2)
    if norm1 <= 0.0 or norm2 <= 0.0:
        raise ValueError("单应矩阵退化：无法分解位姿")
    scale = (norm1 + norm2) / 2.0
    r1 = (m1[0] / scale, m1[1] / scale, m1[2] / scale)
    r2 = (m2[0] / scale, m2[1] / scale, m2[2] / scale)
    t = (m3[0] / scale, m3[1] / scale, m3[2] / scale)

    if t[2] <= 0.0:
        # 平面单应有一对符号相反的分解；标记在相机前方时 t_z 必须为正。
        r1 = (-r1[0], -r1[1], -r1[2])
        r2 = (-r2[0], -r2[1], -r2[2])
        t = (-t[0], -t[1], -t[2])

    # λ 取两个轴模长的平均，r1 / r2 各自再归一化一次即可得到稳定的面内朝向（偏航角）。
    norm_r1 = math.sqrt(r1[0] ** 2 + r1[1] ** 2 + r1[2] ** 2)
    if norm_r1 <= 0.0:
        raise ValueError("单应矩阵退化：无法确定标记朝向")
    yaw_rh_deg = math.degrees(math.atan2(r1[1] / norm_r1, r1[0] / norm_r1))
    # 协议 §0 的角度约定是「逆时针为正」，而相机坐标 y 向下，所以绕 +z 的右手法则角
    # 在画面上表现为顺时针；这里统一成「画面上看到的逆时针为正」。
    yaw_display_deg = -yaw_rh_deg
    return {
        "x_mm": round(t[0], 3),
        "y_mm": round(t[1], 3),
        "z_mm": round(t[2], 3),
        "yaw_deg": round(yaw_display_deg, 3),
    }


def _mat_mul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def reprojection_error(
    corners_px: list[Point], pose: dict[str, float], intrinsics: Intrinsics, marker_size_mm: float
) -> float:
    """用位姿与内参把标记四角重新投影回图像，返回与检测角点的最大像素误差。"""
    yaw = math.radians(-pose["yaw_deg"])
    cos, sin = math.cos(yaw), math.sin(yaw)
    rotation: Matrix3 = ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))
    plane: Matrix3 = (
        (rotation[0][0], rotation[0][1], pose["x_mm"]),
        (rotation[1][0], rotation[1][1], pose["y_mm"]),
        (rotation[2][0], rotation[2][1], pose["z_mm"]),
    )
    projection: Matrix3 = (
        (intrinsics.fx, 0.0, intrinsics.cx),
        (0.0, intrinsics.fy, intrinsics.cy),
        (0.0, 0.0, 1.0),
    )
    matrix = _mat_mul(projection, plane)
    worst = 0.0
    for (u, v), (px, py) in zip(marker_corners_mm(marker_size_mm), corners_px):
        x, y = apply_homography(matrix, u, v)
        worst = max(worst, math.hypot(x - px, y - py))
    return worst
