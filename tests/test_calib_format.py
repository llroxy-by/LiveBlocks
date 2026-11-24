"""LB-031 标定文件格式（``liveblocks.calib.format``）的测试。

三件事：往返一致、规范化文本可当 golden（逐字节稳定）、校验器真的会拒绝坏文件（``LB-B008``）。
"""

from __future__ import annotations

import json

import pytest

from liveblocks.calib.format import (
    CalibrationFile,
    CalibrationFileError,
    validate_calibration,
    validation_diagnostics,
)

CREATED_AT = "2026-11-24T00:00:00Z"


def _document(**overrides):
    """一份最小但完整的标定文件（可被逐字段覆盖，用于构造坏样例）。"""
    document = {
        "schema_version": "0.0.1",
        "kind": "Calibration",
        "calibration_id": "calib-test",
        "created_at": CREATED_AT,
        "status": "synthetic",
        "target": {
            "name": "checkerboard-6x5-20mm",
            "kind": "checkerboard",
            "squares_x": 6,
            "squares_y": 5,
            "square_mm": 20.0,
            "margin_mm": 15.0,
        },
        "image": {"width": 240, "height": 180},
        "image_count": 5,
        "point_count": 100,
        "intrinsics": {"fx": 230.0, "fy": 231.0, "cx": 119.5, "cy": 89.5},
        "distortion": {
            "model": "plumb_bob",
            "k1": -0.2,
            "k2": 0.05,
            "p1": 0.004,
            "p2": -0.003,
        },
        "reprojection_error": {
            "rms_px": 0.079,
            "max_px": 0.286,
            "point_count": 100,
            "per_view_rms_px": [0.07, 0.09],
        },
        "views": [{"view_id": "view-00", "corners": 20, "rms_px": 0.07}],
        "source": {"kind": "synthetic", "preset": "small"},
        "notes": ["合成数据"],
    }
    document.update(overrides)
    return document


def test_valid_document_has_no_problems() -> None:
    assert validate_calibration(_document()) == []


def test_document_round_trips_through_the_dataclass() -> None:
    calibration = CalibrationFile.from_dict(_document())
    assert calibration.calibration_id == "calib-test"
    assert calibration.image_count == 5
    assert calibration.reprojection_error.rms_px == pytest.approx(0.079)
    again = CalibrationFile.from_dict(json.loads(calibration.to_json()))
    assert again.to_json() == calibration.to_json()


def test_canonical_json_is_byte_stable_and_has_a_trailing_newline() -> None:
    calibration = CalibrationFile.from_dict(_document())
    first = calibration.to_json()
    assert first == calibration.to_json()
    assert first.endswith("\n")
    assert json.loads(first)["kind"] == "Calibration"


@pytest.mark.parametrize(
    "patch",
    [
        {"missing": "intrinsics"},
        {"kind": "Calibraton"},
        {"calibration_id": ""},
        {"created_at": "2026-11-24 00:00"},
        {"image_count": 0},
        {"image_count": "5"},
        {"intrinsics": {"fx": 0.0, "fy": 231.0, "cx": 119.5, "cy": 89.5}},
        {"intrinsics": {"fx": 230.0, "fy": 231.0, "cx": 119.5}},
        {"distortion": {"model": "fisheye", "k1": 0.0, "k2": 0.0, "p1": 0.0, "p2": 0.0}},
        {"distortion": {"model": "plumb_bob", "k1": 0.0, "k2": 0.0, "p1": 0.0}},
        {"reprojection_error": {"rms_px": -0.1, "max_px": 0.2}},
        {"reprojection_error": {"rms_px": 0.5, "max_px": 0.1}},
        {"image": {"width": 0, "height": 180}},
        {"target": {"name": "x", "kind": "triangle", "squares_x": 6, "squares_y": 5, "square_mm": 20.0}},
        {"status": "guessed"},
    ],
)
def test_validator_rejects_bad_documents(patch: dict) -> None:
    document = _document()
    for key, value in patch.items():
        if key == "missing":
            document.pop(value, None)
        else:
            document[key] = value
    problems = validate_calibration(document)
    assert problems, f"应当被拒绝：{patch}"
    with pytest.raises(CalibrationFileError):
        CalibrationFile.from_dict(document)
    codes = {entry["code"] for entry in validation_diagnostics(document)}
    assert codes == {"LB-B008"}


def test_load_reports_json_errors_as_calibration_file_errors(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CalibrationFileError, match="不是合法 JSON"):
        CalibrationFile.load(path)


def test_write_then_load_round_trip(tmp_path) -> None:
    calibration = CalibrationFile.from_dict(_document())
    path = calibration.write(tmp_path / "calib.json")
    reloaded = CalibrationFile.load(path)
    assert reloaded.to_json() == calibration.to_json()
    assert "calib-test" in reloaded.describe()


def test_committed_goldens_are_valid_and_synthetic(calib_dir) -> None:
    files = sorted(calib_dir.glob("*.json"))
    assert files, f"{calib_dir} 里没有标定文件 golden"
    for path in files:
        calibration = CalibrationFile.load(path)
        assert validate_calibration(json.loads(path.read_text(encoding="utf-8"))) == []
        assert calibration.source.get("kind") == "synthetic"
        assert calibration.image_count >= 3
        assert calibration.reprojection_error.rms_px > 0.0
        assert calibration.created_at.endswith("Z")
        # 合成 golden 必须如实标注「不是真实摄像头结论」
        assert any("合成" in note for note in calibration.notes)
