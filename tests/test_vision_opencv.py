"""可选交叉核对：装了 OpenCV 就把它当**独立实现**验证识别结果。

沿用 LB-002 的做法（``tests/test_synth_dictionary.py``）：CI 只装 ``.[dev]``，所以这里在
没有 OpenCV 时整体跳过；本地 ``python -m pip install -e '.[dev,aruco]'`` 后会真正跑一遍。

比对口径：

1. 两边都检出的标记，角点必须吻合在 2 px 内（不同实现各自做亚像素，允许一点差异）；
2. 四个「标记全部完整可见且无遮挡」的场景，标记 id 集合必须**完全一致**；
3. ``clipped`` / ``overlap`` 是已知分歧点，各自只断言一边包含另一边的方向性关系：
   - ``clipped``：本实现能读出被画面边界切到的标记 13，OpenCV 直接漏掉它；
   - ``overlap``：被纸片压住的标记 OpenCV 能读出 id 6，本实现的码字校验判为不可译码。
   这两条分歧写死在测试里，将来行为变了会立刻失败，必须显式更新并说明原因。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.synth.png import decode_gray
from liveblocks.vision import CardRegistry, recognize_path

cv2 = pytest.importorskip("cv2", reason="需要可选的 OpenCV（pip install -e '.[dev,aruco]'）")

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"

LAYOUTS = {
    "normal": "card-60x40",
    "clipped": "card-60x40",
    "overlap": "card-60x40",
    "low-light": "card-60x40",
    "slight-rotation": "card-60x40",
    "multi-row": "card-40x40",
}

CLEAN_SCENES = ("normal", "low-light", "slight-rotation", "multi-row")


def opencv_markers(scene: str):
    import numpy as np

    width, height, pixels = decode_gray((SCENES_DIR / f"{scene}.png").read_bytes())
    image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
        cv2.aruco.DetectorParameters(),
    )
    corners, ids, _rejected = detector.detectMarkers(image)
    found: dict[int, list[tuple[float, float]]] = {}
    if ids is not None:
        for index, marker_id in enumerate(ids.reshape(-1)):
            found[int(marker_id)] = [tuple(float(value) for value in point) for point in corners[index][0]]
    return found


def ours_markers(scene: str):
    registry = CardRegistry.load(capture_layout=LAYOUTS[scene])
    result = recognize_path(SCENES_DIR / f"{scene}.png", registry=registry)
    return {
        marker.marker_id: [tuple(point) for point in marker.corners_px]
        for marker in result.markers.markers
        if marker.marker_id is not None
    }, result


@pytest.mark.parametrize("scene", sorted(LAYOUTS))
def test_corners_agree_with_opencv(scene: str) -> None:
    reference = opencv_markers(scene)
    ours, _result = ours_markers(scene)
    shared = set(reference) & set(ours)
    assert shared, f"{scene}: 与 OpenCV 没有任何共同检出，说明两边都跑偏了"
    for marker_id in shared:
        for ours_point, ref_point in zip(ours[marker_id], reference[marker_id]):
            distance = ((ours_point[0] - ref_point[0]) ** 2 + (ours_point[1] - ref_point[1]) ** 2) ** 0.5
            assert distance <= 2.0, f"{scene} 标记 {marker_id} 角点差 {distance:.2f} px"


@pytest.mark.parametrize("scene", CLEAN_SCENES)
def test_marker_ids_match_opencv_on_clean_scenes(scene: str) -> None:
    reference = opencv_markers(scene)
    ours, _result = ours_markers(scene)
    assert set(ours) == set(reference)


def test_clipped_scene_disagreement_is_documented() -> None:
    reference = opencv_markers("clipped")
    ours, _result = ours_markers("clipped")
    assert set(ours) == {3, 13}
    assert set(reference) == {3}  # OpenCV 漏掉被下边界切到的 13
    assert set(ours) >= set(reference)


def test_overlap_scene_disagreement_is_documented() -> None:
    reference = opencv_markers("overlap")
    ours, result = ours_markers("overlap")
    assert set(ours) == {1}
    assert set(reference) == {1, 6}  # OpenCV 读出了被遮挡的 6，本实现判为不可译码
    assert set(ours) <= set(reference)
    assert any(marker.marker_id is None for marker in result.markers.markers)


def test_ground_truth_matches_opencv_on_clean_scenes() -> None:
    """顺带核对 LB-002 的真值与 OpenCV 一致（真值不是自说自话）。"""
    for scene in CLEAN_SCENES:
        truth = json.loads((SCENES_DIR / f"{scene}.json").read_text(encoding="utf-8"))
        expected = {int(card["marker_id"]) for card in truth["cards"]}
        assert expected == set(opencv_markers(scene)), scene
