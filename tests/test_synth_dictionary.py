"""卡片字典与 ArUco 位表的完整性测试。

这一层保证「生成出来的卡片在字典里查得到」，且码字与 OpenCV 的 DICT_4X4_50 一致
（一致性的最终受益者是 LB-003 的识别，但本任务不实现也不评估识别）。
"""

from __future__ import annotations

import pytest

from liveblocks.synth.dictionaries import (
    ArucoDictionary,
    CardDictionary,
    CardEntry,
    load_aruco_dictionaries,
    load_card_dictionary,
)


def test_aruco_dictionary_shape_and_uniqueness() -> None:
    dictionaries = load_aruco_dictionaries()
    aruco = dictionaries["DICT_4X4_50"]
    assert aruco.marker_modules == 4
    assert aruco.border_bits == 1
    assert len(aruco.codes) == 50
    assert aruco.max_marker_id == 49
    assert all(len(code) == 16 and set(code) <= {"0", "1"} for code in aruco.codes)
    aruco.validate()


def test_aruco_dictionary_rotation_unique() -> None:
    aruco = load_aruco_dictionaries()["DICT_4X4_50"]
    rotations: set[str] = set()
    for marker_id in range(len(aruco.codes)):
        matrix = [list(row) for row in aruco.matrix(marker_id)]
        for _ in range(4):
            rotations.add("".join("".join(row) for row in matrix))
            matrix = [list(row) for row in zip(*matrix[::-1])]
    assert len(rotations) == 4 * len(aruco.codes)


def test_aruco_dictionary_rejects_duplicate_rotations() -> None:
    codes = ("1010" "0101" "1010" "0101",) * 2  # 同一个码字登记两次
    aruco = ArucoDictionary(name="DUP", marker_modules=4, border_bits=1, codes=codes)
    with pytest.raises(ValueError, match="旋转后撞码"):
        aruco.validate()


def test_card_dictionary_is_consistent() -> None:
    dictionaries = load_aruco_dictionaries()
    cards = load_card_dictionary(dictionaries)
    assert cards.aruco_dictionary == "DICT_4X4_50"
    assert len(cards.cards) >= 5
    aruco = dictionaries[cards.aruco_dictionary]
    cards.validate(aruco)

    ids = [card.card_id for card in cards.cards]
    marker_ids = [card.marker_id for card in cards.cards]
    faces = [card.card_face for card in cards.cards]
    assert len(set(ids)) == len(ids)
    assert len(set(marker_ids)) == len(marker_ids)
    assert len(set(faces)) == len(faces)
    assert all(0 <= marker_id <= aruco.max_marker_id for marker_id in marker_ids)


def test_card_dictionary_lookup() -> None:
    cards = load_card_dictionary()
    card = cards.card("C01")
    assert card.marker_id == 0
    assert card.card_face == "move-forward"
    with pytest.raises(KeyError):
        cards.card("不存在")


def test_card_dictionary_rejects_duplicate_marker_id() -> None:
    aruco = ArucoDictionary(name="DICT_4X4_50", marker_modules=4, border_bits=1, codes=load_aruco_dictionaries()["DICT_4X4_50"].codes)
    cards = CardDictionary(
        version="test",
        aruco_dictionary="DICT_4X4_50",
        cards=(
            CardEntry("A", 0, "action", "a", ""),
            CardEntry("B", 0, "action", "b", ""),
        ),
    )
    with pytest.raises(ValueError, match="重复登记"):
        cards.validate(aruco)


def test_card_dictionary_rejects_out_of_range_marker() -> None:
    aruco = load_aruco_dictionaries()["DICT_4X4_50"]
    cards = CardDictionary(
        version="test",
        aruco_dictionary="DICT_4X4_50",
        cards=(CardEntry("A", 99, "action", "a", ""),),
    )
    with pytest.raises(ValueError, match="范围"):
        cards.validate(aruco)


def test_vendored_bit_patterns_match_opencv() -> None:
    """如果有 OpenCV，就把它当作独立实现来核对入库的位表（没有则跳过）。"""
    cv2 = pytest.importorskip("cv2", reason="需要 opencv-python-headless 才做这项交叉核对")
    np = pytest.importorskip("numpy")

    aruco = load_aruco_dictionaries()["DICT_4X4_50"]
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    modules = dictionary.markerSize
    assert modules == aruco.marker_modules
    for marker_id in range(len(aruco.codes)):
        side = (modules + 2) * 4
        image = cv2.aruco.generateImageMarker(dictionary, marker_id, side, borderBits=1)
        expected = []
        for row in range(modules):
            pattern = ""
            for col in range(modules):
                px = int(image[(row + 1) * 4 + 2, (col + 1) * 4 + 2])
                pattern += "1" if px > 127 else "0"
            expected.append(pattern)
        assert "".join(expected) == aruco.code(marker_id), f"marker {marker_id} 位表不一致"
        assert tuple(expected) == tuple("".join(row) for row in aruco.matrix(marker_id))
    # 同一个字典对象也能画出与入库位表一致的标记图（再核一次尺寸/边界）
    image = cv2.aruco.generateImageMarker(dictionary, 0, (modules + 2) * 4, borderBits=1)
    assert image.shape == ((modules + 2) * 4, (modules + 2) * 4)
    assert np is not None
