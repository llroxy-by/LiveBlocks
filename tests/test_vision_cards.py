"""卡片映射层：字典登记、状态判定、朝向、``unknown_card`` 与状态优先级。"""

from __future__ import annotations

from pathlib import Path

import pytest

from liveblocks.synth.dictionaries import CardDictionary, load_aruco_dictionaries
from liveblocks.vision import (
    CardRegistry,
    build_card_candidates,
    card_geometry,
    load_frame,
    recognize_path,
    validate_card_candidate_list,
)
from liveblocks.vision.cards import UNKNOWN, load_card_dictionary_file
from liveblocks.vision.markers import MarkerCandidate, MarkerCandidateList

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"
DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "liveblocks" / "data"


def restricted_dictionary(excluded: set[int]) -> CardDictionary:
    base = load_card_dictionary_file(DATA_DIR / "card_dictionary.json")
    return CardDictionary(
        version=base.version,
        aruco_dictionary=base.aruco_dictionary,
        cards=tuple(card for card in base.cards if card.marker_id not in excluded),
    )


def test_registry_maps_all_registered_markers() -> None:
    registry = CardRegistry.load()
    assert len(registry.card_dictionary.cards) == 16
    assert registry.entry_for_marker(0).card_face == "move-forward"
    assert registry.entry_for_marker(15).card_type == "operator"
    assert registry.entry_for_marker(49) is None  # 字典内、但没登记
    assert registry.aruco.name == "DICT_4X4_50"
    assert registry.quadrant_for("C01") == 0


def test_card_geometry_for_quadrants() -> None:
    registry = CardRegistry.load()
    layout = registry.layout_for("C01")
    corners, center = card_geometry(layout, 0)
    # 卡片 60x40、标记 24 居中偏上（卡面标记中心在 (30, 15.5) mm）
    assert center == pytest.approx((0.0, 4.5))
    assert corners[0] == pytest.approx((-30.0, -15.5))
    assert corners[1] == pytest.approx((30.0, -15.5))
    assert corners[2] == pytest.approx((30.0, 24.5))
    assert corners[3] == pytest.approx((-30.0, 24.5))
    # 转 90°：中心不动，四角绕中心旋转
    rotated, rotated_center = card_geometry(layout, 1)
    assert rotated_center == pytest.approx(center)
    # 偏移 (-30, -20) 顺时针转 90° 变成 (20, -30)
    assert rotated[0] == pytest.approx((center[0] + 20.0, center[1] - 30.0))


def test_registry_rejects_unknown_layout() -> None:
    with pytest.raises(ValueError):
        CardRegistry.load(capture_layout="card-does-not-exist")


def test_registry_with_capture_layout_keeps_dictionary() -> None:
    registry = CardRegistry.load(capture_layout="card-60x40")
    other = registry.with_capture_layout("card-40x40")
    assert other.card_dictionary is registry.card_dictionary
    assert other.marker_size_mm == pytest.approx(16.0)
    assert registry.marker_size_mm == pytest.approx(24.0)


def test_clipped_scene_marks_cards_clipped(recognized) -> None:
    result, _truth = recognized["clipped"]
    statuses = sorted(card.status for card in result.cards.cards)
    assert statuses == ["clipped", "clipped"]
    codes = [entry["code"] for entry in result.cards.diagnostics]
    assert codes.count("LB-V006") == 2  # 两张卡都由标记外推判出越界
    for card in result.cards.cards:
        assert card.card_corners_px is not None


def test_fully_visible_cards_are_ok(recognized) -> None:
    for scene in ("normal", "low-light", "slight-rotation", "multi-row"):
        result, _truth = recognized[scene]
        assert all(card.status == "ok" for card in result.cards.cards), scene
        assert result.cards.diagnostics == ()


def test_unregistered_marker_is_unknown_card() -> None:
    """把标记 0 从卡片字典里摘掉：识别结论必须是 ``unknown_card``（Issue #9 验收第 3 条）。"""
    registry = CardRegistry.load(
        card_dictionary=restricted_dictionary({0}), capture_layout="card-60x40"
    )
    result = recognize_path(SCENES_DIR / "normal.png", registry=registry)
    assert len(result.cards.cards) == 1
    card = result.cards.cards[0]
    assert card.marker_ids == ("0",)
    assert card.status == "unknown_card"
    assert card.card_type == UNKNOWN
    assert card.card_face == UNKNOWN
    assert card.card_id == UNKNOWN
    assert card.orientation_quadrant is None
    document = result.cards.to_dict()
    validate_card_candidate_list(document)
    codes = [entry["code"] for entry in document["diagnostics"]]
    assert "LB-V001" in codes


def test_unknown_card_has_priority_over_clipped() -> None:
    """裁切图 + 未登记字典：单字段只能给一个状态，``unknown_card`` 优先，越界另留诊断。"""
    registry = CardRegistry.load(
        card_dictionary=restricted_dictionary({3, 13}), capture_layout="card-60x40"
    )
    result = recognize_path(SCENES_DIR / "clipped.png", registry=registry)
    statuses = sorted(card.status for card in result.cards.cards)
    assert statuses == ["unknown_card", "unknown_card"]
    codes = [entry["code"] for entry in result.cards.diagnostics]
    assert codes.count("LB-V001") == 2
    assert codes.count("LB-V006") == 2  # 越界事实照样留痕


def _fake_marker(marker_id: int | None, tied: tuple[int, ...] = ()) -> MarkerCandidate:
    from liveblocks.vision.decode import CodeMatch, DecodeResult

    match = CodeMatch(
        marker_id=marker_id,
        rotations=0,
        bit_errors=0 if marker_id is not None else 2,
        runner_up_id=None,
        runner_up_errors=4,
        tied_with=tied or ((marker_id,) if marker_id is not None else ()),
    )
    decode = DecodeResult(
        match=match,
        bits="0" * 16,
        black_level=20.0,
        white_level=240.0,
        border_dark=True,
        border_max=25.0,
        quadrant_contrast=1.0,
    )
    quad = ((100.0, 100.0), (130.0, 100.0), (130.0, 130.0), (100.0, 130.0))
    return MarkerCandidate(
        index=0,
        marker_id=marker_id,
        quad=quad,
        corners_px=quad,
        center_px=(115.0, 115.0),
        angle_deg=0.0,
        confidence=0.42,
        decode=decode,
        touches_border=False,
    )


def test_ambiguous_candidate_has_no_orientation() -> None:
    frame = load_frame(SCENES_DIR / "normal.png")
    markers = MarkerCandidateList(
        frame_id=frame.frame_id,
        dictionary="DICT_4X4_50",
        markers=(_fake_marker(None, tied=(3, 13)),),
    )
    cards = build_card_candidates(frame, markers, CardRegistry.load())
    assert len(cards.cards) == 1
    card = cards.cards[0]
    assert card.status == "ambiguous"
    assert card.marker_ids == ()
    assert card.card_type == UNKNOWN
    assert card.orientation_quadrant is None
    assert card.confidence <= 0.5
    document = cards.to_dict()
    validate_card_candidate_list(document)
    assert "orientation_quadrant" not in document["cards"][0]
    assert any(entry["code"] == "LB-V005" for entry in document["diagnostics"])


def test_occluded_candidate_without_identity() -> None:
    frame = load_frame(SCENES_DIR / "normal.png")
    markers = MarkerCandidateList(
        frame_id=frame.frame_id, dictionary="DICT_4X4_50", markers=(_fake_marker(None),)
    )
    cards = build_card_candidates(frame, markers, CardRegistry.load())
    card = cards.cards[0]
    assert card.status == "occluded"
    assert card.marker_ids == ()
    assert card.card_corners_px is None


def test_candidate_ids_are_sequential_and_unique(recognized) -> None:
    for scene, (result, _truth) in recognized.items():
        ids = [card.candidate_id for card in result.cards.cards]
        assert ids == [f"c-{index}" for index in range(len(ids))], scene


def test_load_card_dictionary_file_rejects_bad_marker_range(tmp_path) -> None:
    import json

    payload = {
        "schema_version": "0.0.1",
        "kind": "CardDictionary",
        "dictionary_version": "0.0.1",
        "aruco_dictionary": "DICT_4X4_50",
        "cards": [
            {"card_id": "X01", "marker_id": 99, "card_type": "action", "card_face": "nope"}
        ],
    }
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_card_dictionary_file(path)


def test_aruco_dictionary_is_the_expected_one() -> None:
    dictionaries = load_aruco_dictionaries()
    assert "DICT_4X4_50" in dictionaries
    assert dictionaries["DICT_4X4_50"].marker_modules == 4
