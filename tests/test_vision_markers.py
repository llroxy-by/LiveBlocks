"""标记检测层：连通域候选、去重、诊断码、亚像素细化带来的精度提升。"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from liveblocks.vision import CardRegistry, detect_markers, load_frame
from liveblocks.vision.markers import (
    POSE_MIN_SIDE_PX,
    MarkerCandidate,
    MarkerCandidateList,
    _candidate_from_component,
    _deduplicate,
)
from liveblocks.vision.image import adaptive_binary, connected_components

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"


def test_detect_markers_normal_has_one_candidate(recognized) -> None:
    result, truth = recognized["normal"]
    markers = result.markers
    assert markers.dictionary == "DICT_4X4_50"
    assert [marker.marker_id for marker in markers.markers] == [0]
    assert markers.frame_id == "f-normal"
    marker = markers.markers[0]
    assert marker.decode.match.bit_errors == 0
    assert marker.confidence == pytest.approx(1.0, abs=0.05)
    assert marker.touches_border is False


def test_detect_markers_is_not_sorted(recognized) -> None:
    """协议 §3.2：识别层不得排序。候选顺序必须是连通域的扫描顺序（左上→右下）。"""
    result, _truth = recognized["multi-row"]
    centers = [marker.center_px for marker in result.markers.markers]
    # multi-row 是 2 行 x 3 列：第一行的三个候选必须都排在第一行三个候选之前
    rows = [0 if center[1] < 240 else 1 for center in centers]
    assert rows == sorted(rows)


def test_border_cut_marker_is_still_detected_and_flagged(recognized) -> None:
    result, _truth = recognized["clipped"]
    ids = {marker.marker_id for marker in result.markers.markers}
    assert ids == {3, 13}
    touching = [marker for marker in result.markers.markers if marker.touches_border]
    assert [marker.marker_id for marker in touching] == [13]
    codes = [entry["code"] for entry in result.markers.diagnostics]
    assert "LB-V002" in codes


def test_low_light_confidence_is_lower_than_clean(recognized) -> None:
    clean = recognized["normal"][0].markers.markers[0].confidence
    dark = min(marker.confidence for marker in recognized["low-light"][0].markers.markers)
    assert 0.0 < dark < clean


def test_subpixel_refinement_beats_raw_component_quad(recognized) -> None:
    """细化后的角点必须比「凸包化简出的整数角点」更接近真值。"""
    from liveblocks.vision.image import convex_hull, simplify_to_quad

    result, truth = recognized["slight-rotation"]
    frame = load_frame(SCENES_DIR / "slight-rotation.png")
    mask = adaptive_binary(frame.image)
    components = connected_components(mask, frame.width, frame.height, min_area=60)
    raw_errors = []
    refined_errors = []
    for component in components:
        points = [(float(index % frame.width), float(index // frame.width)) for index in component]
        rough = simplify_to_quad(convex_hull(points))
        if len(rough) != 4:
            continue
        candidate = _candidate_from_component(
            frame, component, CardRegistry.load().aruco, index=0, max_bit_errors=1
        )
        if candidate is None or candidate.marker_id is None:
            continue
        truth_card = next(
            card for card in truth["cards"] if int(card["marker_id"]) == candidate.marker_id
        )
        # 粗角点：把凸包四边形按同样的顺序对齐后误差
        rotations = candidate.decode.match.rotations
        ordered_raw = [rough[(i + rotations) % 4] for i in range(4)]
        raw_errors.append(
            max(
                math.dist(found, tuple(want))
                for found, want in zip(ordered_raw, truth_card["marker_corners_px"])
            )
        )
        refined_errors.append(
            max(
                math.dist(found, tuple(want))
                for found, want in zip(candidate.corners_px, truth_card["marker_corners_px"])
            )
        )
    assert raw_errors and refined_errors
    assert sum(refined_errors) / len(refined_errors) < sum(raw_errors) / len(raw_errors) / 2.0


def test_no_pose_without_intrinsics(recognized) -> None:
    for scene, (result, _truth) in recognized.items():
        assert all(marker.pose is None for marker in result.markers.markers), scene


def test_small_markers_get_no_pose() -> None:
    """小于 ``POSE_MIN_SIDE_PX`` 的标记不求位姿（单应分解在小标记上会成倍放大误差）。"""
    from liveblocks.vision import Intrinsics

    frame = load_frame(
        SCENES_DIR / "multi-row.png", intrinsics=Intrinsics(700.0, 700.0, 320.0, 240.0)
    )
    registry = CardRegistry.load(capture_layout="card-40x40")
    result = detect_markers(
        frame, aruco=registry.aruco, marker_size_mm=registry.marker_size_mm
    )
    assert result.markers
    for marker in result.markers:
        assert marker.pose is None
        assert min(
            math.dist(marker.corners_px[i], marker.corners_px[(i + 1) % 4]) for i in range(4)
        ) < POSE_MIN_SIDE_PX + 1.0


def test_deduplicate_keeps_higher_confidence() -> None:
    """同一标记被重复检出时保留置信度更高的那个，并留 LB-V004 诊断。"""

    class FakeDecode:
        def __init__(self, marker_id: int) -> None:
            from liveblocks.vision.decode import CodeMatch

            self.match = CodeMatch(
                marker_id=marker_id,
                rotations=0,
                bit_errors=0,
                runner_up_id=None,
                runner_up_errors=4,
                tied_with=(marker_id,),
            )
            self.bits = "0" * 16
            self.black_level = 20.0
            self.white_level = 240.0
            self.border_dark = True
            self.border_max = 25.0
            self.quadrant_contrast = 1.0

    def make(index: int, confidence: float, offset: float) -> MarkerCandidate:
        quad = ((10.0 + offset, 10.0), (40.0 + offset, 10.0), (40.0 + offset, 40.0), (10.0 + offset, 40.0))
        return MarkerCandidate(
            index=index,
            marker_id=7,
            quad=quad,
            corners_px=quad,
            center_px=(25.0 + offset, 25.0),
            angle_deg=0.0,
            confidence=confidence,
            decode=FakeDecode(7),
            touches_border=False,
        )

    kept, diagnostics = _deduplicate([make(0, 0.4, 0.0), make(1, 0.9, 2.0)])
    assert len(kept) == 1
    assert kept[0].index == 1
    assert [entry["code"] for entry in diagnostics] == ["LB-V004"]


def test_candidate_rejects_non_marker_shapes(recognized) -> None:
    """卡片的深色边框环、标签笔画都不能被当成标记。"""
    frame = load_frame(SCENES_DIR / "normal.png")
    mask = adaptive_binary(frame.image)
    markers = 0
    for component in connected_components(mask, frame.width, frame.height, min_area=60):
        candidate = _candidate_from_component(
            frame, component, CardRegistry.load().aruco, index=0, max_bit_errors=1
        )
        if candidate is not None:
            markers += 1
            assert candidate.marker_id == 0
    assert markers == 1


def test_marker_candidate_list_requires_dictionary_name() -> None:
    empty = MarkerCandidateList(frame_id="f-x", dictionary="DICT_4X4_50", markers=())
    document = empty.to_dict()
    assert document["kind"] == "MarkerCandidateList"
    assert document["markers"] == []
    assert document["diagnostics"] == []


