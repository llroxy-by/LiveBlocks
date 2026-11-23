"""协议 §3 的最小校验器：真实输出必须通过，明显违约的文档必须被拒绝。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from liveblocks.vision import (
    validate_card_candidate_list,
    validate_marker_candidate_list,
)
from liveblocks.vision.protocol import diagnostic

SCENES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "scenes"


def normal_documents(recognized) -> tuple[dict, dict]:
    result, _truth = recognized["normal"]
    return result.markers.to_dict(), result.cards.to_dict()


def test_generated_documents_validate(recognized) -> None:
    markers, cards = normal_documents(recognized)
    validate_marker_candidate_list(markers)
    validate_card_candidate_list(cards)


def test_every_fixture_document_validates(recognized) -> None:
    for scene, (result, _truth) in recognized.items():
        validate_marker_candidate_list(result.markers.to_dict())
        validate_card_candidate_list(result.cards.to_dict())


def test_diagnostic_shape(recognized) -> None:
    entry = diagnostic("LB-V001", "warning", "示例", candidate_ids=["c-0"])
    assert entry == {
        "code": "LB-V001",
        "severity": "warning",
        "message": "示例",
        "candidate_ids": ["c-0"],
    }
    with pytest.raises(ValueError):
        diagnostic("LB-V001", "fatal", "坏 severity")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda doc: doc.update(kind="Something"), "kind"),
        (lambda doc: doc.pop("frame_id"), "frame_id"),
        (lambda doc: doc.update(schema_version="v1"), "schema_version"),
        (lambda doc: doc["cards"][0].update(status="fine"), "status"),
        (lambda doc: doc["cards"][0].update(confidence=1.5), "confidence"),
        (lambda doc: doc["cards"][0].update(center_px=[1.5, 2]), "整数"),
        (lambda doc: doc["cards"][0].update(marker_ids=[0]), "marker_ids"),
        (lambda doc: doc["cards"][0].pop("orientation_quadrant"), "orientation_quadrant"),
        (lambda doc: doc["cards"].append(copy.deepcopy(doc["cards"][0])), "candidate_id"),
        (lambda doc: doc["cards"][0].update(card_face=None), "card_face"),
        (lambda doc: doc.update(diagnostics=[{"code": "X"}]), "诊断对象"),
    ],
)
def test_card_document_violations_are_rejected(recognized, mutate, message: str) -> None:
    _markers, cards = normal_documents(recognized)
    broken = copy.deepcopy(cards)
    mutate(broken)
    with pytest.raises(ValueError) as error:
        validate_card_candidate_list(broken)
    assert message in str(error.value)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda doc: doc.update(kind="Cards"), "kind"),
        (lambda doc: doc["markers"][0].update(marker_id=0), "marker_id"),
        (lambda doc: doc["markers"][0].update(corners_px=[[1, 2], [3, 4]]), "corners_px"),
        (lambda doc: doc["markers"][0].update(angle_deg="0"), "angle_deg"),
        (lambda doc: doc["markers"][0].update(confidence=-0.1), "confidence"),
        (lambda doc: doc["markers"][0].update(pose={"x_mm": 1.0}), "pose"),
        (lambda doc: doc.pop("dictionary"), "dictionary"),
    ],
)
def test_marker_document_violations_are_rejected(recognized, mutate, message: str) -> None:
    markers, _cards = normal_documents(recognized)
    broken = copy.deepcopy(markers)
    mutate(broken)
    with pytest.raises(ValueError) as error:
        validate_marker_candidate_list(broken)
    assert message in str(error.value)


def test_unknown_extra_fields_are_tolerated(recognized) -> None:
    """协议 §0：消费者必须容忍新增字段。"""
    _markers, cards = normal_documents(recognized)
    extended = copy.deepcopy(cards)
    extended["future_field"] = {"anything": [1, 2, 3]}
    extended["cards"][0]["future_card_field"] = "ok"
    validate_card_candidate_list(extended)
