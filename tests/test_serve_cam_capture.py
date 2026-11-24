"""采集仓库：人工标注的校验、四角来源、refused 归档、清单与确定性。

这里最要紧的一条是**不猜坐标**：拿不到四角的卡不进 ``scene.json``（它会污染
``recognize report`` 的统计），只留在 ``capture.json`` 的 ``rejected_annotations`` 里并给警告。
第二要紧的是**不拿检测结论冒充真值**：``status`` 缺省值固定 ``ok`` 并标 ``status_assumed``，
``center_px`` 的来源写在 ``center_source`` 里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.serve_cam import ServeCamApp, ServeCamConfig, TestClient
from liveblocks.vision import evaluate_directory

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"


def _frame(client, scene: str = "normal", query: str = "") -> dict:
    png = (SCENES_DIR / f"{scene}.png").read_bytes()
    response = client.post_png(f"/api/frame{query}", png, filename=f"{scene}.png")
    assert response.status == 200, response.text
    return response.json()


def _capture(client, body: dict) -> dict:
    response = client.post_json("/api/capture", body)
    assert response.status == 201, response.text
    return response.json()["capture"]


# --- 标注校验 -------------------------------------------------------------------------


def test_capture_requires_at_least_one_annotation(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    response = serve_cam.client.post_json("/api/capture", {"frame_token": frame["frame_token"]})
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C006"


def test_bad_status_enum_is_rejected(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    response = serve_cam.client.post_json(
        "/api/capture",
        {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01", "status": "wat"}]},
    )
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C006"


def test_corners_must_be_four_points(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    for corners in ([[0, 0], [1, 1]], "not json", [[0, 0], [1, 1], [2, 2], ["x", "y"]]):
        response = serve_cam.client.post_json(
            "/api/capture",
            {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01", "corners_px": corners}]},
        )
        assert response.status == 400, corners
        assert response.json()["error"]["code"] == "LB-C006"


def test_bad_outcome_is_rejected(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    response = serve_cam.client.post_json(
        "/api/capture",
        {"frame_token": frame["frame_token"], "outcome": "maybe", "annotations": [{"card_id": "C01"}]},
    )
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C006"


def test_refused_is_accepted_as_a_synonym_of_misrecognised(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "outcome": "refused", "annotations": [{"card_id": "C01"}]},
    )
    assert capture["outcome"] == "misrecognised"
    assert "refused" in Path(capture["directory"]).parts


# --- 四角来源 -------------------------------------------------------------------------


def test_detected_corners_are_recorded_as_such(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]})
    card = capture["cards"][0]
    assert card["corner_source"] == "detected"
    assert card["corners_px"] is None  # 人工没给角点，真值角点来自检出
    assert len(card["marker_corners_px"]) == 4
    assert card["center_source"] == "card-geometry-from-marker-quad"
    assert card["status_assumed"] is True


def test_manual_corners_win_over_detected_ones(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    corners = [[290, 200], [350, 200], [350, 260], [290, 260]]
    capture = _capture(
        serve_cam.client,
        {
            "frame_token": frame["frame_token"],
            "annotations": [{"card_id": "C01", "corners_px": corners}],
        },
    )
    card = capture["cards"][0]
    assert card["corner_source"] == "manual"
    assert card["corners_px"] == corners
    assert card["marker_corners_px"] == corners


def test_manual_corners_string_form_is_parsed(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {
            "frame_token": frame["frame_token"],
            "annotations": [{"card_id": "C01", "corners": "[[290,200],[350,200],[350,260],[290,260]]"}],
        },
    )
    assert capture["cards"][0]["corner_source"] == "manual"


def test_undetected_marker_without_corners_is_reported_not_guessed(serve_cam) -> None:
    """两张标注、只检出一个标记 → 没有四角的那张**不进真值**，只留警告与 rejected 记录。"""
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {
            "frame_token": frame["frame_token"],
            "annotations": [{"card_id": "C01"}, {"card_id": "C05"}],
        },
    )
    assert [card["card_id"] for card in capture["cards"]] == ["C01"]
    assert capture["rejected_annotations"][0]["card_id"] == "C05"
    assert "不猜坐标" in capture["rejected_annotations"][0]["reason"]
    assert any("缺四角" in warning for warning in capture["warnings"])

    truth = json.loads((Path(capture["directory"]) / "scene.json").read_text(encoding="utf-8"))
    assert [card["card_id"] for card in truth["cards"]] == ["C01"]
    record = json.loads((Path(capture["directory"]) / "capture.json").read_text(encoding="utf-8"))
    assert [entry["report_usable"] for entry in record["annotations"]] == [True, False]


def test_single_annotation_borrows_the_only_marker_quad_with_a_warning(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C05"}]})
    card = capture["cards"][0]
    assert card["corner_source"] == "detected-single-marker"
    assert card["match"] is False  # 检出的是 C01，人标注是 C05
    assert any("借用了画面里唯一一个标记候选" in warning for warning in capture["warnings"])


def test_unknown_card_id_is_archived_but_kept_out_of_the_truth(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "Z99"}]})
    assert capture["cards"] == []
    assert capture["report_usable"] is False
    assert capture["recognize_report_command"] is None
    assert capture["rejected_annotations"][0]["reason"].startswith("卡片 id 不在")
    truth = json.loads((Path(capture["directory"]) / "scene.json").read_text(encoding="utf-8"))
    assert truth["cards"] == []


def test_role_mismatch_is_a_warning_not_an_error(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01", "role": "control"}]},
    )
    assert capture["cards"][0]["card_type"] == "action"  # 真值以字典为准
    assert any("角色" in warning and "不一致" in warning for warning in capture["warnings"])


def test_duplicate_annotations_are_kept_with_a_warning(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}, {"card_id": "C01"}]},
    )
    assert len(capture["cards"]) == 2
    assert any("出现了两次" in warning for warning in capture["warnings"])


def test_annotation_notes_are_stored(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    capture = _capture(
        serve_cam.client,
        {
            "frame_token": frame["frame_token"],
            "notes": "室内 40 cm",
            "annotations": [{"card_id": "C01", "notes": "前景卡"}],
        },
    )
    record = json.loads((Path(capture["directory"]) / "capture.json").read_text(encoding="utf-8"))
    assert record["notes"] == "室内 40 cm"
    assert record["annotations"][0]["notes"] == "前景卡"


# --- 多卡样本 -------------------------------------------------------------------------


def test_multi_row_scene_can_be_annotated_card_by_card(serve_cam) -> None:
    """``multi-row`` 有 6 张卡：逐张标注后，采集目录应能被 recognize report 消化到 6/6。"""
    frame = _frame(serve_cam.client, "multi-row")
    recognized = [card["card_id"] for card in frame["card_candidate_list"]["cards"]]
    capture = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "annotations": [{"card_id": card_id} for card_id in recognized]},
    )
    assert len(capture["cards"]) == len(recognized)
    report = evaluate_directory(Path(capture["directory"]))
    assert report.scenes[0].expected == len(recognized)
    assert report.scenes[0].identified == len(recognized)


# --- 清单（GET /api/captures） ---------------------------------------------------------


def test_captures_list_reports_both_archives(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    good = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]})
    frame = _frame(serve_cam.client)
    bad = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "outcome": "misrecognised", "annotations": [{"card_id": "C01"}]},
    )

    payload = serve_cam.client.get("/api/captures").json()
    assert payload["count"] == 2
    assert payload["counts"] == {"accepted": 1, "misrecognised": 1}
    ids = [entry["capture_id"] for entry in payload["captures"]]
    assert set(ids) == {good["capture_id"], bad["capture_id"]}
    entry = next(item for item in payload["captures"] if item["capture_id"] == good["capture_id"])
    assert entry["card_ids"] == ["C01"]
    assert entry["matches"] == [True]
    assert entry["report_usable"] is True
    assert entry["frame_url"].endswith("/frame.png")
    assert entry["width"] == 640 and entry["height"] == 480

    only_refused = serve_cam.client.get("/api/captures?outcome=misrecognised").json()
    assert [item["capture_id"] for item in only_refused["captures"]] == [bad["capture_id"]]
    assert serve_cam.client.get("/api/captures?outcome=nope").status == 400


def test_capture_ids_are_unique_and_timestamped(serve_cam) -> None:
    frame = _frame(serve_cam.client)
    first = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]})
    second = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]})
    assert first["capture_id"] != second["capture_id"]
    assert first["capture_id"].startswith("20261124T051500Z-")
    assert first["created_at"] == "2026-11-24T05:15:00Z"


def test_capture_with_inline_frame_does_not_need_a_token(serve_cam) -> None:
    import base64

    png = (SCENES_DIR / "normal.png").read_bytes()
    response = serve_cam.client.post_json(
        "/api/capture",
        {
            "png_base64": base64.b64encode(png).decode("ascii"),
            "frame_id": "f-inline",
            "annotations": [{"card_id": "C01"}],
        },
    )
    assert response.status == 201, response.text
    capture = response.json()["capture"]
    record = json.loads((Path(capture["directory"]) / "capture.json").read_text(encoding="utf-8"))
    assert record["frame"]["frame_id"] == "f-inline"
    assert evaluate_directory(Path(capture["directory"])).scenes[0].identified == 1


def test_capture_on_a_different_card_layout_is_recorded(serve_cam) -> None:
    frame = _frame(serve_cam.client, query="?card_layout=card-40x40")
    capture = _capture(
        serve_cam.client,
        {"frame_token": frame["frame_token"], "card_layout": "card-40x40", "annotations": [{"card_id": "C01"}]},
    )
    truth = json.loads((Path(capture["directory"]) / "scene.json").read_text(encoding="utf-8"))
    assert truth["card_layout"] == "card-40x40"
    assert evaluate_directory(Path(capture["directory"])).scenes[0].expected == 1


def test_write_failure_is_reported_as_lb_c009(tmp_path: Path) -> None:
    blocker = tmp_path / "live"
    blocker.write_text("not a directory", encoding="utf-8")
    client = TestClient(ServeCamApp(ServeCamConfig(live_dir=blocker)))
    frame = _frame(client)
    response = client.post_json("/api/capture", {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]})
    assert response.status == 500
    assert response.json()["error"]["code"] == "LB-C009"


@pytest.mark.parametrize("scene", ["normal", "slight-rotation", "clipped"])
def test_capture_of_each_scene_is_report_consumable(serve_cam, scene: str) -> None:
    frame = _frame(serve_cam.client, scene)
    first = frame["card_candidate_list"]["cards"][0]["card_id"]
    capture = _capture(serve_cam.client, {"frame_token": frame["frame_token"], "annotations": [{"card_id": first}]})
    report = evaluate_directory(Path(capture["directory"]))
    assert report.scenes[0].expected == 1
    assert report.scenes[0].identified == 1
