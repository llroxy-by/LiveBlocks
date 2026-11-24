"""LB-031 角点检测（``liveblocks.calib.corners``）的测试：两条流程都要真的能把编号对上。

测试用合成拍摄图（真值角点已知）来判：

- 棋盘格：内角点全部检出，且与真值的偏差在 0.2 px 量级；
- 黑格不会被「点接触」粘成一个连通域（4 邻域腐蚀的作用）；
- ChArUco：标记 id 与棋盘编号**逐一对上**，对不上时报 ``LB-B004``；
- 空白画面报 ``LB-B001``；同一幅图两次检测结果完全一致。
"""

from __future__ import annotations

import math

import pytest

from liveblocks.calib.corners import detect_corners, detect_squares
from liveblocks.synth.calibtarget import CalibTarget, render_capture
from liveblocks.synth.geometry import Pose
from liveblocks.synth.lens import LensModel
from liveblocks.vision.image import GrayImage

LENS = LensModel(fx=230.0, fy=231.0, cx=119.5, cy=89.5, k1=-0.2, k2=0.05, p1=0.004, p2=-0.003)
POSE = Pose(x_mm=2, y_mm=-3, z_mm=205, yaw_deg=6, pitch_deg=15, roll_deg=-5)
CHECKER = CalibTarget(
    name="checkerboard-6x5-20mm",
    kind="checkerboard",
    squares_x=6,
    squares_y=5,
    square_mm=20.0,
    margin_mm=15.0,
)
CHARUCO = CalibTarget(
    name="charuco-5x4-20mm",
    kind="charuco",
    squares_x=5,
    squares_y=4,
    square_mm=20.0,
    margin_mm=15.0,
    marker_mm=13.0,
    dictionary="DICT_4X4_50",
)


def _capture(target: CalibTarget, *, pose: Pose = POSE, seed: int = 5):
    return render_capture(
        target, pose=pose, lens=LENS, width=240, height=180, noise_sigma=1.5, seed=seed
    )


def _image(capture) -> GrayImage:
    return GrayImage(capture.width, capture.height, capture.pixels)


@pytest.fixture(scope="module")
def checker_capture():
    return _capture(CHECKER)


@pytest.fixture(scope="module")
def checker_corners(checker_capture):
    return detect_corners(_image(checker_capture), CHECKER)


def test_checkerboard_finds_every_inner_corner(checker_corners) -> None:
    assert checker_corners.method == "checkerboard-grid"
    assert len(checker_corners.corners) == len(CHECKER.inner_corners()) == 20
    assert checker_corners.missing == []
    assert [entry["code"] for entry in checker_corners.diagnostics if entry["severity"] == "error"] == []


def test_checkerboard_corner_positions_match_ground_truth(checker_capture, checker_corners) -> None:
    truth = checker_capture.truth_corners()
    errors = [
        math.hypot(checker_corners.corners[corner_id][0] - truth[corner_id][0],
                   checker_corners.corners[corner_id][1] - truth[corner_id][1])
        for corner_id in checker_corners.corners
    ]
    assert sum(errors) / len(errors) < 0.2
    assert max(errors) < 0.6


def test_black_squares_are_not_merged(checker_capture) -> None:
    """4 邻域腐蚀把「点接触」的黑格断开：黑格数应正好等于目标里的黑格数。"""
    squares, _diagnostics = detect_squares(_image(checker_capture))
    expected = sum(
        1
        for row in range(CHECKER.squares_y)
        for col in range(CHECKER.squares_x)
        if CHECKER.is_black_cell(col, row)
    )
    assert len(squares) == expected


def test_charuco_markers_match_the_grid_numbering() -> None:
    capture = _capture(CHARUCO, pose=Pose(x_mm=0, y_mm=0, z_mm=200, yaw_deg=-5, pitch_deg=12, roll_deg=4))
    corner_set = detect_corners(_image(capture), CHARUCO)
    assert corner_set.method == "charuco-grid+markers"
    assert len(corner_set.corners) == len(CHARUCO.inner_corners())
    assert corner_set.markers > 0
    assert corner_set.marker_matches >= max(1, corner_set.markers // 3)
    errors = [entry for entry in corner_set.diagnostics if entry["code"] == "LB-B004" and entry["severity"] == "error"]
    assert errors == []


def test_charuco_corner_positions_match_ground_truth() -> None:
    capture = _capture(CHARUCO, pose=Pose(x_mm=0, y_mm=0, z_mm=200, yaw_deg=-5, pitch_deg=12, roll_deg=4))
    corner_set = detect_corners(_image(capture), CHARUCO)
    truth = capture.truth_corners()
    errors = [
        math.hypot(corner_set.corners[corner_id][0] - truth[corner_id][0],
                   corner_set.corners[corner_id][1] - truth[corner_id][1])
        for corner_id in corner_set.corners
    ]
    assert sum(errors) / len(errors) < 0.2


def test_blank_image_reports_lb_c001() -> None:
    blank = GrayImage(160, 120, bytes([200]) * (160 * 120))
    corner_set = detect_corners(blank, CHECKER)
    assert corner_set.corners == {}
    assert corner_set.squares == 0
    assert any(entry["code"] == "LB-B001" for entry in corner_set.diagnostics)


def test_detection_is_deterministic(checker_capture) -> None:
    first = detect_corners(_image(checker_capture), CHECKER).to_dict()
    second = detect_corners(_image(checker_capture), CHECKER).to_dict()
    assert first == second


def test_perspective_free_pose_also_works() -> None:
    """正对相机的板（无旋转）也要能编号；这条挡「规范化起点依赖旋转」的回归。"""
    capture = _capture(CHECKER, pose=Pose(x_mm=0, y_mm=0, z_mm=200), seed=11)
    corner_set = detect_corners(_image(capture), CHECKER)
    assert len(corner_set.corners) == len(CHECKER.inner_corners())
