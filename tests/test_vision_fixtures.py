"""认认识别路径在 LB-002 fixtures 上的表现：这就是 Issue #9 的可观察验收。

验收条款（逐条对应）：

1. 对 LB-002 的 fixtures 跑识别，输出符合 ``docs/PROTOCOL.md`` §3 的 JSON —— 每帧输出都过
   ``validate_marker_candidate_list`` / ``validate_card_candidate_list``；
2. 「正常」样例无漏检 —— ``normal`` 场景 1 张卡全部识别；
3. 「裁切」样例标 ``clipped`` —— ``clipped`` 场景 2 张卡都是 ``clipped``；
4. 未登记标记标 ``unknown_card`` —— 见 ``tests/test_vision_cards.py``（本文件用全量登记的卡片字典）；
5. 有一份可读的准确率/漏检统计输出 —— ``liveblocks recognize report``，见 ``tests/test_vision_cli.py``。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from liveblocks.vision import validate_card_candidate_list, validate_marker_candidate_list
from liveblocks.vision.stats import evaluate_scene

#: 期望的识别结果（真值 marker_id → 识别状态）。之所以写死一份，是为了让「识别行为变了」必须
#: 显式改测试，而不是被一句「看起来差不多」放过去。
EXPECTED = {
    "normal": {0: "ok"},
    "clipped": {3: "clipped", 13: "clipped"},
    "low-light": {8: "ok", 14: "ok"},
    "slight-rotation": {2: "ok", 11: "ok", 5: "ok"},
    "multi-row": {0: "ok", 11: "ok", 4: "ok", 7: "ok", 12: "ok", 9: "ok"},
}

#: ``overlap`` 是已知的难例：一张卡的标记被纸片压住（检出但读不出码字），另一张卡的标记被
#: 后画的卡片盖住（完全漏检），剩下那张能识别但卡片级的遮挡在识别层看不到（见 PROGRESS 限制）。
OVERLAP_IDENTIFIED = {1}
OVERLAP_UNIDENTIFIED = 1
#: 完全漏检（连形状都没检出）的标记；标记 6 是「检出但读不出码字」，单独计为未识别。
OVERLAP_MISSED = {"12"}


@pytest.mark.parametrize("scene", sorted(EXPECTED))
def test_scene_identifies_every_card(recognized, scene: str) -> None:
    result, truth = recognized[scene]
    cards = result.cards.cards
    status_by_marker = {
        marker_id: card.status for card in cards for marker_id in card.marker_ids
    }
    expected = {str(marker_id): status for marker_id, status in EXPECTED[scene].items()}
    assert status_by_marker == expected
    # 没有身份不明的候选、没有多出来的候选
    assert len(cards) == len(truth["cards"])


@pytest.mark.parametrize("scene", sorted(EXPECTED))
def test_corner_and_center_errors_are_subpixel(recognized, scene: str) -> None:
    result, truth = recognized[scene]
    marker_by_id = {marker.marker_id: marker for marker in result.markers.markers}
    card_by_marker = {
        marker_id: card for card in result.cards.cards for marker_id in card.marker_ids
    }
    for truth_card in truth["cards"]:
        marker_id = int(truth_card["marker_id"])
        marker = marker_by_id[marker_id]
        worst = max(
            math.dist(found, tuple(want))
            for found, want in zip(marker.corners_px, truth_card["marker_corners_px"])
        )
        assert worst <= 1.0, f"{scene} 标记 {marker_id} 角点误差 {worst:.2f} px"
        center_error = math.dist(
            card_by_marker[str(marker_id)].center_px, tuple(truth_card["center_px"])
        )
        assert center_error <= 1.0, f"{scene} 标记 {marker_id} 卡片中心误差 {center_error:.2f} px"


@pytest.mark.parametrize(
    ("scene", "tolerance"),
    [
        ("normal", 1.0),
        ("clipped", 1.5),
        ("low-light", 1.5),
        ("slight-rotation", 1.5),
        # multi-row 的标记只有 19 px（约 3 px/模块），面内角精度受像素栅格限制，放宽到 4°
        ("multi-row", 4.0),
    ],
)
def test_angle_follows_protocol_ccw_convention(recognized, scene: str, tolerance: float) -> None:
    """协议 §0 说角度逆时针为正；LB-002 真值里的 ``in_plane_angle_deg`` 是「图像 y 向下顺时针为正」。"""
    result, truth = recognized[scene]
    marker_by_id = {marker.marker_id: marker for marker in result.markers.markers}
    for truth_card in truth["cards"]:
        marker = marker_by_id[int(truth_card["marker_id"])]
        expected = -float(truth_card["in_plane_angle_deg"])
        assert marker.angle_deg == pytest.approx(expected, abs=tolerance)


@pytest.mark.parametrize("scene", sorted(EXPECTED) + ["overlap"])
def test_outputs_conform_to_protocol(recognized, scene: str) -> None:
    result, _truth = recognized[scene]
    markers = result.markers.to_dict()
    cards = result.cards.to_dict()
    validate_marker_candidate_list(markers)
    validate_card_candidate_list(cards)
    assert markers["frame_id"] == cards["frame_id"] == f"f-{scene}"
    for card in cards["cards"]:
        assert card["status"] in {"ok", "unknown_card", "occluded", "clipped", "ambiguous"}
        assert 0.0 <= card["confidence"] <= 1.0
        assert all(isinstance(value, int) for value in card["center_px"])
    diagnostics = markers["diagnostics"]
    for marker in markers["markers"]:
        assert all(isinstance(value, int) for value in marker["center_px"])
        assert 0.0 <= marker["confidence"] <= 1.0
        if marker["marker_id"] == "unknown":  # §0：未知值降级为 unknown，并且必须留诊断
            assert marker["confidence"] <= 0.5
            assert any(entry["code"] == "LB-V003" for entry in diagnostics)


def unsatisfied_markers(truth: dict, identified: set[int]) -> set[str]:
    """真值里有、但没有被识别的标记（字符串形式，与协议里的 marker_ids 一致）。"""
    expected = {str(card["marker_id"]) for card in truth["cards"]}
    return expected - {str(value) for value in identified}


def test_overlap_is_reported_honestly(recognized) -> None:
    result, truth = recognized["overlap"]
    identified = {
        int(marker_id) for card in result.cards.cards for marker_id in card.marker_ids
    }
    unidentified = [
        card for card in result.cards.cards if card.status in {"occluded", "ambiguous"}
    ]
    assert identified == OVERLAP_IDENTIFIED
    assert len(unidentified) == OVERLAP_UNIDENTIFIED
    assert unsatisfied_markers(truth, identified) == {"6", "12"}
    # 统计口径要能区分「检出但读不出」与「完全漏检」
    from liveblocks.vision.stats import evaluate_scene

    scenes_dir = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
    evaluation, _ = evaluate_scene(scenes_dir / "overlap.png", truth)
    assert evaluation.missed == ("12",)
    assert evaluation.unidentified == 1
    assert any("C07" in note and "不可识别" in note for note in evaluation.notes)
    # 识别层不排序、不丢弃：候选数与「识别 + 未识别」一致
    assert len(result.cards.cards) == len(identified) + len(unidentified)


def test_no_duplicate_marker_ids_across_fixtures(recognized) -> None:
    for scene, (result, _truth) in recognized.items():
        ids = [marker.marker_id for marker in result.markers.markers if marker.marker_id is not None]
        assert len(ids) == len(set(ids)), scene


def test_statistics_are_reported_for_every_scene(recognized, scenes_dir) -> None:
    evaluation, result = evaluate_scene(
        scenes_dir / "normal.png", recognized["normal"][1]
    )
    assert evaluation.expected == 1
    assert evaluation.identified == 1
    assert evaluation.missed == ()
    assert evaluation.extra == ()
    assert evaluation.recall == pytest.approx(1.0)
    assert result.frame.frame_id == "f-normal"


def test_report_document_contains_acceptance_lines() -> None:
    from pathlib import Path

    from liveblocks.vision import evaluate_directory, format_report

    scenes_dir = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
    report = evaluate_directory(scenes_dir)
    text = format_report(report)
    assert "「正常」样例无漏检：通过" in text
    assert "「裁切」样例标 clipped：通过" in text
    assert "召回率" in text and "精确率" in text
    assert report.expected == 17
    assert report.extra == 0
