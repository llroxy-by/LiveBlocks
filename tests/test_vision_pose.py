"""位姿估计（可选输出）：只在给了内参、且标记足够大时才出现，精度与真值对照。"""

from __future__ import annotations

from pathlib import Path

import pytest

from liveblocks.vision import Intrinsics, detect_markers, load_frame, recognize
from liveblocks.vision.pose import (
    homography_from_marker_frame,
    marker_corners_mm,
    pose_from_homography,
    reprojection_error,
)

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
INTRINSICS = Intrinsics(fx=700.0, fy=700.0, cx=320.0, cy=240.0)


def recognize_with_intrinsics(scene: str, layout: str):
    frame = load_frame(SCENES_DIR / f"{scene}.png", intrinsics=INTRINSICS)
    return recognize(frame, registry=_registry(layout)), frame


def _registry(layout: str):
    from liveblocks.vision import CardRegistry

    return CardRegistry.load(capture_layout=layout)


@pytest.mark.parametrize(
    ("scene", "layout"),
    [
        ("normal", "card-60x40"),
        ("clipped", "card-60x40"),
        ("low-light", "card-60x40"),
        ("overlap", "card-60x40"),
        ("slight-rotation", "card-60x40"),
    ],
)
def test_pose_depth_matches_truth_within_five_percent(scene: str, layout: str) -> None:
    import json

    truth = json.loads((SCENES_DIR / f"{scene}.json").read_text(encoding="utf-8"))
    result, _frame = recognize_with_intrinsics(scene, layout)
    truth_z = {int(card["marker_id"]): float(card["pose"]["z_mm"]) for card in truth["cards"]}
    checked = 0
    for marker in result.markers.markers:
        if marker.marker_id is None or marker.pose is None:
            continue
        expected = truth_z[marker.marker_id]
        assert marker.pose["z_mm"] == pytest.approx(expected, rel=0.05), (
            f"{scene} 标记 {marker.marker_id}：z={marker.pose['z_mm']} 真值 {expected}"
        )
        checked += 1
    assert checked >= 1


def test_pose_yaw_follows_display_ccw_convention() -> None:
    import json

    truth = json.loads((SCENES_DIR / "slight-rotation.json").read_text(encoding="utf-8"))
    result, _frame = recognize_with_intrinsics("slight-rotation", "card-60x40")
    expected = {int(card["marker_id"]): -float(card["in_plane_angle_deg"]) for card in truth["cards"]}
    for marker in result.markers.markers:
        if marker.marker_id is None or marker.pose is None:
            continue
        assert marker.pose["yaw_deg"] == pytest.approx(expected[marker.marker_id], abs=2.0)


def test_pose_reprojects_marker_corners() -> None:
    """用估计出的位姿把标记四角投回图像，误差应小于 1 px。"""
    result, frame = recognize_with_intrinsics("normal", "card-60x40")
    marker = result.markers.markers[0]
    assert marker.pose is not None
    error = reprojection_error(
        list(marker.corners_px), marker.pose, INTRINSICS, marker_size_mm=24.0
    )
    assert error <= 1.0


def test_pose_absent_without_intrinsics() -> None:
    frame = load_frame(SCENES_DIR / "normal.png")
    result = recognize(frame, registry=_registry("card-60x40"))
    assert result.markers.markers[0].pose is None


def test_pose_homography_decomposition_roundtrip() -> None:
    """人为构造一个已知位姿的四边形，分解出的位姿必须还原它。"""
    import math

    corners = [(300.0, 180.0), (380.0, 186.0), (374.0, 266.0), (294.0, 260.0)]
    matrix = homography_from_marker_frame(corners, 24.0)
    pose = pose_from_homography(matrix, INTRINSICS)
    assert pose["z_mm"] > 0.0
    assert math.isfinite(pose["yaw_deg"])
    error = reprojection_error(corners, pose, INTRINSICS, 24.0)
    assert error <= 1.0
    for u, v in marker_corners_mm(24.0):
        assert isinstance(u, float) and isinstance(v, float)


def test_detect_markers_without_intrinsics_has_no_pose() -> None:
    frame = load_frame(SCENES_DIR / "normal.png")
    markers = detect_markers(
        frame, aruco=_registry("card-60x40").aruco, marker_size_mm=24.0
    )
    assert all(marker.pose is None for marker in markers.markers)
