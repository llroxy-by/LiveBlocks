"""LB-031 标定求解（``liveblocks.calib.solve``）的测试。

关键回归是 **「理想数据能不能把真值精确解回来」**：用真值内参/畸变把一组位姿投影成观测，
再让求解器从头解一遍。只要求解器有病（例如交替解两个块停在坏点上、Schur 补符号错），
这条就会立刻失败——合成场景下这是唯一能区分「数值凑合」与「算法正确」的判据。
"""

from __future__ import annotations

import math

import pytest

from liveblocks.calib.solve import (
    CalibrationError,
    Observation,
    apply_homography,
    calibrate,
    homography_dlt,
    joint_refine,
    project_point,
    rodrigues,
    rotation_to_rvec,
    _initial_poses,  # noqa: PLC2701 - 测试要看初值质量
    zhang_intrinsics,
)
from liveblocks.synth.calibtarget import CalibTarget
from liveblocks.synth.geometry import Camera, Pose, plane_homography
from liveblocks.synth.lens import LensModel

CHECKER = CalibTarget(
    name="checkerboard-6x5-20mm",
    kind="checkerboard",
    squares_x=6,
    squares_y=5,
    square_mm=20.0,
    margin_mm=15.0,
)
TRUTH = LensModel(fx=230.0, fy=231.0, cx=119.5, cy=89.5, k1=-0.2, k2=0.05, p1=0.004, p2=-0.003)
POSES = [
    Pose(x_mm=0, y_mm=0, z_mm=210, yaw_deg=3, pitch_deg=16, roll_deg=-5),
    Pose(x_mm=-24, y_mm=12, z_mm=195, yaw_deg=-12, pitch_deg=-14, roll_deg=8),
    Pose(x_mm=22, y_mm=-14, z_mm=225, yaw_deg=14, pitch_deg=11, roll_deg=4),
    Pose(x_mm=-12, y_mm=-16, z_mm=190, yaw_deg=10, pitch_deg=18, roll_deg=-9),
    Pose(x_mm=14, y_mm=16, z_mm=215, yaw_deg=-10, pitch_deg=-16, roll_deg=7),
]
WIDTH, HEIGHT = 240, 180


def exact_observations(poses=POSES, *, lens: LensModel = TRUTH, target: CalibTarget = CHECKER):
    """用真值模型把每个位姿投影成「完美观测」（没有任何检角误差）。"""
    camera = Camera(fx=lens.fx, fy=lens.fy, cx=lens.cx, cy=lens.cy)
    object_points = target.object_points()
    observations: list[Observation] = []
    for pose in poses:
        matrix = plane_homography(camera, pose)
        flat = [row for row in (matrix[0], matrix[1], matrix[2])]
        # 由单应分解出位姿（与求解器内部一致），再用真值模型投影
        from liveblocks.calib.solve import extrinsics_from_homography

        flat_list = [value for row in flat for value in row]
        rvec, tvec = extrinsics_from_homography(flat_list, lens)
        points = tuple(
            (corner_id, *project_point(lens, rvec, tvec, x_mm, y_mm))
            for corner_id, (x_mm, y_mm) in sorted(object_points.items())
        )
        observations.append(Observation(width=WIDTH, height=HEIGHT, points=points))
    return observations


def test_homography_dlt_recovers_a_known_matrix() -> None:
    truth = [[1.2, 0.1, 30.0], [-0.05, 1.1, 20.0], [0.0004, -0.0002, 1.0]]
    flat = [value for row in truth for value in row]
    source = [(0.0, 0.0), (200.0, 0.0), (200.0, 150.0), (0.0, 150.0), (60.0, 40.0)]
    target = [apply_homography(flat, x, y) for x, y in source]
    recovered = homography_dlt(source, target)
    assert recovered == pytest.approx([value / recovered[8] for value in flat], abs=1e-9)


def test_rodrigues_round_trip() -> None:
    for rvec in ([0.0, 0.0, 0.0], [0.12, -0.4, 0.05], [1.1, 0.2, -0.3]):
        restored = rotation_to_rvec(rodrigues(list(rvec)))
        assert restored == pytest.approx(rvec, abs=1e-9)


def test_zhang_recovers_intrinsics_from_undistorted_grid() -> None:
    """无畸变的理想观测上，Zhang 闭式解应当把内参找回来（1% 以内）。"""
    pinhole = LensModel(fx=TRUTH.fx, fy=TRUTH.fy, cx=TRUTH.cx, cy=TRUTH.cy)
    observations = exact_observations(lens=pinhole)
    object_points = CHECKER.object_points()
    homographies = [
        homography_dlt(
            [object_points[corner_id] for corner_id, _u, _v in observation.points],
            [(u, v) for _cid, u, v in observation.points],
        )
        for observation in observations
    ]
    fx, fy, cx, cy = zhang_intrinsics(homographies)
    assert fx == pytest.approx(TRUTH.fx, rel=0.01)
    assert fy == pytest.approx(TRUTH.fy, rel=0.01)
    assert cx == pytest.approx(TRUTH.cx, abs=1.0)
    assert cy == pytest.approx(TRUTH.cy, abs=1.0)


def test_solver_recovers_truth_from_perfect_observations() -> None:
    result = calibrate(exact_observations(), CHECKER.object_points())
    assert result.reprojection_rms_px < 1e-6
    assert result.reprojection_max_px < 1e-5
    assert result.dropped_points == 0
    assert result.lens.fx == pytest.approx(TRUTH.fx, rel=1e-5)
    assert result.lens.fy == pytest.approx(TRUTH.fy, rel=1e-5)
    assert result.lens.cx == pytest.approx(TRUTH.cx, abs=1e-4)
    assert result.lens.k1 == pytest.approx(TRUTH.k1, abs=1e-5)
    assert result.lens.p1 == pytest.approx(TRUTH.p1, abs=1e-5)


def test_joint_refine_does_not_wander_away_from_a_good_start() -> None:
    """从真值出发、位姿也正确时，联合 LM 不应把参数带离真值。"""
    observations = exact_observations()
    poses = _initial_poses(observations, CHECKER.object_points(), TRUTH)
    lens, refined_poses, _rounds, cost = joint_refine(
        TRUTH, poses, observations, CHECKER.object_points(), iterations=5
    )
    residual_rms = math.sqrt(cost / (2 * sum(len(item.points) for item in observations)))
    assert residual_rms < 0.05
    assert lens.fx == pytest.approx(TRUTH.fx, rel=0.02)


def test_single_bad_point_is_dropped() -> None:
    observations = list(exact_observations())
    first = observations[0]
    points = list(first.points)
    corner_id, u, v = points[3]
    points[3] = (corner_id, u + 25.0, v - 18.0)  # 明显错的观测（例如编号错一格）
    observations[0] = Observation(width=first.width, height=first.height, points=tuple(points))
    result = calibrate(observations, CHECKER.object_points())
    assert result.dropped_points >= 1
    assert result.reprojection_rms_px < 0.05


def test_needs_at_least_three_views() -> None:
    observations = exact_observations(poses=POSES[:2])
    with pytest.raises(CalibrationError, match="至少需要 3 个视图"):
        calibrate(observations, CHECKER.object_points())


def test_rejects_unknown_corner_ids() -> None:
    observations = exact_observations(poses=POSES[:3])
    first = observations[0]
    broken = Observation(
        width=first.width,
        height=first.height,
        points=first.points + ((9999, 10.0, 10.0),),
    )
    with pytest.raises(CalibrationError, match="不在目标定义里"):
        calibrate([broken] + observations[1:], CHECKER.object_points())


def test_rejects_view_with_too_few_points() -> None:
    observations = exact_observations(poses=POSES[:3])
    thin = Observation(width=WIDTH, height=HEIGHT, points=observations[0].points[:4])
    with pytest.raises(CalibrationError, match="角点"):
        calibrate([thin] + observations[1:], CHECKER.object_points())


def test_result_reports_per_view_rms() -> None:
    result = calibrate(exact_observations(), CHECKER.object_points())
    per_view = result.per_view_rms_px()
    assert len(per_view) == len(POSES)
    assert all(value < 1e-5 for value in per_view)
    assert result.point_count == len(POSES) * len(CHECKER.inner_corners())
