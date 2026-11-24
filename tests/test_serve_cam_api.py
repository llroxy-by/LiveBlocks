"""``/api/*`` 的形状与错误模型：状态码、``LB-C0xx`` 码、媒体类型、白名单与令牌过期。

口径：**可预期的坏请求一定给结构化 JSON 错误**（``kind=ServeCamError`` + ``LB-C0xx``），
不是 500、不是空响应、不是 Python traceback；真正的内部错误（不可预期）才走 ``LB-C011``。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from liveblocks.serve_cam import ServeCamApp, ServeCamConfig, TestClient
from liveblocks.synth.png import decode_gray

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"


def _scene_png(name: str = "normal") -> bytes:
    return (SCENES_DIR / f"{name}.png").read_bytes()


# --- 存活与元数据 ---------------------------------------------------------------------


def test_health_reports_service_and_capture_counts(serve_cam) -> None:
    payload = serve_cam.client.get("/api/health").json()
    assert payload["kind"] == "ServeCamHealth"
    assert payload["service"] == "lb-030-serve-cam"
    assert payload["ok"] is True
    assert payload["captures"] == {"accepted": 0, "misrecognised": 0}
    assert payload["refused_dir"].endswith("/refused")

    png = _scene_png()
    frame = serve_cam.client.post_png("/api/frame", png, filename="normal.png").json()
    serve_cam.client.post_json(
        "/api/capture", {"frame_token": frame["frame_token"], "annotations": [{"card_id": "C01"}]}
    )
    assert serve_cam.client.get("/api/health").json()["captures"]["accepted"] == 1


def test_info_lists_layouts_cards_and_routes(serve_cam) -> None:
    payload = serve_cam.client.get("/api/info").json()
    assert payload["kind"] == "ServeCamInfo"
    assert payload["default_card_layout"] == "card-60x40"
    assert payload["card_layout"] == "card-60x40"
    assert payload["card_layouts"] == ["card-40x40", "card-60x40"]
    assert payload["routes"]["frame"] == "/api/frame"
    assert payload["routes"]["capture"] == "/api/capture"
    card_ids = [card["card_id"] for card in payload["cards"]]
    assert "C01" in card_ids and "N01" in card_ids
    assert set(payload["outcomes"]) == {"accepted", "misrecognised"}
    assert "application/x-liveblocks-gray" in payload["media_types"]


# --- 路由与方法的错误 ---------------------------------------------------------------


def test_unknown_route_is_a_structured_404(serve_cam) -> None:
    response = serve_cam.client.get("/api/nope")
    assert response.status == 404
    document = response.json()
    assert document["kind"] == "ServeCamError"
    assert document["error"]["code"] == "LB-C004"
    assert document["error"]["title"] == "未知路由"


def test_wrong_method_is_a_structured_405(serve_cam) -> None:
    response = serve_cam.client.get("/api/frame")
    assert response.status == 405
    assert response.json()["error"]["code"] == "LB-C005"
    response = serve_cam.client.post("/api/captures", b"{}", content_type="application/json")
    assert response.status == 405


def test_missing_body_is_lb_c001(serve_cam) -> None:
    response = serve_cam.client.post("/api/frame", b"", content_type="image/png")
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C001"


def test_unsupported_media_type_is_lb_c002(serve_cam) -> None:
    response = serve_cam.client.post("/api/frame", b"hello", content_type="text/plain")
    assert response.status == 415
    assert response.json()["error"]["code"] == "LB-C002"


def test_broken_png_is_lb_c003(serve_cam) -> None:
    response = serve_cam.client.post("/api/frame", b"\x89PNG\r\n\x1a\nbroken", content_type="image/png")
    assert response.status == 400
    error = response.json()["error"]
    assert error["code"] == "LB-C003"
    assert "8bit 灰度" in error["message"]


def test_raw_gray_size_mismatch_is_lb_c003(serve_cam) -> None:
    response = serve_cam.client.post_gray("/api/frame", 4, 4, b"\x00\x01\x02")
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C003"


def test_oversized_body_is_lb_c010(tmp_path: Path) -> None:
    app = ServeCamApp(ServeCamConfig(live_dir=tmp_path / "live", max_body_bytes=64))
    response = TestClient(app).post("/api/frame", b"x" * 200, content_type="image/png")
    assert response.status == 413
    assert response.json()["error"]["code"] == "LB-C010"


def test_json_body_payload_shape_is_lb_c001(serve_cam) -> None:
    response = serve_cam.client.post("/api/frame", b"[1,2,3]", content_type="application/json")
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C001"
    response = serve_cam.client.post_json("/api/frame", {"width": 4, "height": 4})
    assert response.status == 400
    assert response.json()["error"]["code"] == "LB-C001"


# --- 三种请求体 -----------------------------------------------------------------------


def test_json_body_accepts_png_base64_and_matches_the_png_path(serve_cam) -> None:
    png = _scene_png()
    from_png = serve_cam.client.post_png("/api/frame", png, filename="normal.png").json()
    encoded = base64.b64encode(png).decode("ascii")
    response = serve_cam.client.post_json(
        "/api/frame", {"png_base64": encoded, "frame_id": "f-normal"}
    )
    assert response.status == 200, response.text
    assert response.json()["card_candidate_list"] == from_png["card_candidate_list"]


def test_json_body_accepts_gray_base64(serve_cam) -> None:
    width, height, pixels = decode_gray(_scene_png())
    encoded = base64.b64encode(pixels).decode("ascii")
    response = serve_cam.client.post_json(
        "/api/frame",
        {"gray_base64": encoded, "width": width, "height": height, "frame_id": "f-normal"},
    )
    assert response.status == 200, response.text
    assert response.json()["frame"]["width"] == width
    assert response.json()["card_candidate_list"]["frame_id"] == "f-normal"


def test_frame_id_falls_back_to_a_generated_camera_id(serve_cam) -> None:
    width, height, pixels = decode_gray(_scene_png())
    payload = serve_cam.client.post_gray("/api/frame", width, height, pixels).json()
    assert payload["frame"]["frame_id"].startswith("f-cam-")
    assert payload["frame"]["source"] == "camera"


def test_card_layout_can_be_overridden_per_request(serve_cam) -> None:
    payload = serve_cam.client.post_png("/api/frame?card_layout=card-40x40", _scene_png(), filename="normal.png").json()
    assert payload["card_layout"] == "card-40x40"
    assert payload["card_candidate_list"]["kind"] == "CardCandidateList"

    bad = serve_cam.client.post_png("/api/frame?card_layout=nope", _scene_png(), filename="normal.png")
    assert bad.status == 400
    assert bad.json()["error"]["code"] == "LB-C006"


# --- 帧令牌 ---------------------------------------------------------------------------


def test_frame_token_expires_by_capacity(tmp_path: Path) -> None:
    app = ServeCamApp(ServeCamConfig(live_dir=tmp_path / "live", frame_capacity=1))
    client = TestClient(app)
    first = client.post_png("/api/frame", _scene_png(), filename="normal.png").json()["frame_token"]
    client.post_png("/api/frame", _scene_png(), filename="normal.png")
    response = client.post_json("/api/capture", {"frame_token": first, "annotations": [{"card_id": "C01"}]})
    assert response.status == 409
    assert response.json()["error"]["code"] == "LB-C007"


def test_unknown_frame_token_on_capture_is_lb_c007(serve_cam) -> None:
    response = serve_cam.client.post_json(
        "/api/capture", {"frame_token": "deadbeefdeadbeef", "annotations": [{"card_id": "C01"}]}
    )
    assert response.status == 409
    assert response.json()["error"]["code"] == "LB-C007"


def test_frame_file_endpoint_serves_frame_and_overlay(serve_cam) -> None:
    payload = serve_cam.client.post_png("/api/frame", _scene_png(), filename="normal.png").json()
    token = payload["frame_token"]
    overlay = serve_cam.client.get(f"/api/frames/{token}/overlay.png")
    frame = serve_cam.client.get(f"/api/frames/{token}/frame.png")
    assert overlay.status == 200 and overlay.content_type == "image/png"
    assert frame.status == 200 and frame.content_type == "image/png"
    assert frame.body == _scene_png()  # 原帧按同一份灰度像素重新编码，字节不变
    assert serve_cam.client.get(f"/api/frames/{token}/sample.json").json()["error"]["code"] == "LB-C007"
    missing = serve_cam.client.get("/api/frames/0123456789abcdef/overlay.png")
    assert missing.status == 409 and missing.json()["error"]["code"] == "LB-C007"


# --- 采集列表与文件 -------------------------------------------------------------------


def test_captures_list_starts_empty(serve_cam) -> None:
    payload = serve_cam.client.get("/api/captures").json()
    assert payload["kind"] == "LiveCaptureList"
    assert payload["captures"] == []
    assert payload["counts"] == {"accepted": 0, "misrecognised": 0}


def test_capture_file_endpoint_whitelist_and_missing_sample(serve_cam) -> None:
    png = _scene_png()
    frame = serve_cam.client.post_png("/api/frame", png, filename="normal.png").json()
    capture = serve_cam.client.post_json(
        "/api/capture",
        {
            "frame_token": frame["frame_token"],
            "annotations": [{"card_id": "C01"}],
        },
    ).json()["capture"]
    capture_id = capture["capture_id"]

    for name in ("frame.png", "scene.json", "capture.json", "index.json"):
        response = serve_cam.client.get(f"/api/captures/{capture_id}/{name}")
        assert response.status == 200, name
        assert len(response.body) > 0
    assert serve_cam.client.get(f"/api/captures/{capture_id}/scene.json").body == (
        Path(capture["files"]["scene.json"]).read_bytes()
    )

    refused_file = serve_cam.client.get(f"/api/captures/{capture_id}/nope.txt")
    assert refused_file.status == 404
    assert refused_file.json()["error"]["code"] == "LB-C008"

    missing = serve_cam.client.get("/api/captures/20260101T000000Z-abcdef/frame.png")
    assert missing.status == 404
    assert missing.json()["error"]["code"] == "LB-C008"


def test_capture_id_cannot_escape_the_live_directory(serve_cam) -> None:
    for raw in ("..", "../..", "..%2Fetc", "a/b", "."):
        response = serve_cam.client.get(f"/api/captures/{raw}/frame.png")
        assert response.status == 404, raw
        assert response.json()["error"]["code"] == "LB-C008"


def test_capture_rejects_non_json_content_type(serve_cam) -> None:
    response = serve_cam.client.post("/api/capture", b"frame=1", content_type="multipart/form-data; boundary=x")
    assert response.status == 415
    assert response.json()["error"]["code"] == "LB-C002"


def test_capture_error_document_lists_the_code_table(serve_cam) -> None:
    document = serve_cam.client.get("/api/nope").json()
    assert "LB-C001" in document["error_codes"]
    assert "LB-C011" in document["error_codes"]


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/info", "/api/captures"])
def test_json_endpoints_are_cache_free(serve_cam, path: str) -> None:
    response = serve_cam.client.get(path)
    assert response.status == 200
    assert response.headers.get("cache-control") == "no-store"
    if path != "/":
        json.loads(response.text)
