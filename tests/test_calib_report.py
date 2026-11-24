"""LB-031 验收报告（``liveblocks.calib.report``）的测试：一条命令里的数字必须能复现。

用 ``conftest.make_small_preset()`` 的小预设真跑一遍（约 2.5 秒，会话内缓存），检查：

- 四条验收条目全部通过，且报告里真的印出了重投影误差与校正前后的均值/最大值；
- 同一预设跑两次，标定文件与全部数字**逐字节一致**（可作 golden）；
- 预设数据文件（``data/calib_presets.json``）自洽：板存在、阈值合理、视图够多、每个位姿都能看全。
"""

from __future__ import annotations

import json

import pytest

from liveblocks.calib.presets import load_presets
from liveblocks.calib.report import (
    DEFAULT_CREATED_AT,
    HUMAN_VERIFICATION,
    build_report,
    format_report,
    run_preset,
    write_calibrations,
)
from liveblocks.synth.calibtarget import render_capture

REQUIRED_TEXT = ("重投影误差", "校正前", "校正后", "均值", "最大", "待人工验证")


def test_small_preset_passes_every_acceptance_line(calib_small_outcome) -> None:
    outcome = calib_small_outcome
    assert outcome.passes()
    lines = outcome.acceptance()
    assert len(lines) == 4
    assert all(ok for ok, _criterion, _evidence in lines)
    assert any("LB-B006" in criterion for _ok, criterion, _e in lines)
    assert any("LB-B007" in criterion for _ok, criterion, _e in lines)


def test_reprojection_error_is_below_the_stated_threshold(calib_small_outcome) -> None:
    outcome = calib_small_outcome
    assert outcome.calibration.reprojection_error.rms_px <= outcome.preset.thresholds.reprojection_rms_px
    assert outcome.calibration.reprojection_error.max_px >= outcome.calibration.reprojection_error.rms_px


def test_repeatability_is_reported_before_and_after_undistortion(calib_small_outcome) -> None:
    outcome = calib_small_outcome
    raw = outcome.raw_repeatability
    fixed = outcome.corrected_repeatability
    assert raw.repeats == outcome.preset.static.repeats
    assert fixed.repeats == raw.repeats
    assert raw.corners == fixed.corners == len(outcome.preset.target.inner_corners())
    for value in (raw.mean_px, raw.max_px, fixed.mean_px, fixed.max_px):
        assert value >= 0.0
    assert raw.max_px >= raw.mean_px
    assert fixed.max_px >= fixed.mean_px
    # 校正的真正效果：一个针孔单应能解释的程度（残差明显下降）
    assert outcome.pinhole_fit.corrected_rms_px < outcome.pinhole_fit.raw_rms_px


def test_estimated_intrinsics_are_close_to_truth(calib_small_outcome) -> None:
    outcome = calib_small_outcome
    truth = outcome.preset.true_lens
    assert outcome.result.lens.fx == pytest.approx(truth.fx, rel=0.03)
    assert outcome.result.lens.fy == pytest.approx(truth.fy, rel=0.03)
    assert outcome.accuracy.principal_point_px <= outcome.preset.thresholds.principal_point_px


def test_calibration_file_carries_all_required_metadata(calib_small_outcome) -> None:
    document = calib_small_outcome.calibration.to_dict()
    assert document["kind"] == "Calibration"
    assert document["image_count"] == len(calib_small_outcome.preset.views)
    assert document["image"] == {
        "width": calib_small_outcome.preset.width,
        "height": calib_small_outcome.preset.height,
    }
    assert document["created_at"] == DEFAULT_CREATED_AT
    assert set(document["distortion"]) == {"model", "k1", "k2", "p1", "p2"}
    assert document["reprojection_error"]["rms_px"] > 0.0
    assert document["source"]["kind"] == "synthetic"


def test_report_text_contains_the_numbers_and_the_human_verification(calib_small_outcome) -> None:
    from liveblocks.calib.report import CalibrationReport

    report = CalibrationReport(
        outcomes=(calib_small_outcome,), created_at=DEFAULT_CREATED_AT, wall_ms=1.0
    )
    text = format_report(report)
    for needle in REQUIRED_TEXT:
        assert needle in text
    assert "通过" in text
    for item, _why, _steps in HUMAN_VERIFICATION:
        assert item in text


def test_report_is_reproducible(calib_small_preset) -> None:
    """同一预设跑两次：标定文件逐字节一致，重复性数字也一致（可回归）。"""
    first = run_preset(calib_small_preset)
    second = run_preset(calib_small_preset)
    assert first.calibration.to_json() == second.calibration.to_json()
    assert first.raw_repeatability.to_dict() == second.raw_repeatability.to_dict()
    assert first.corrected_repeatability.to_dict() == second.corrected_repeatability.to_dict()
    assert first.pinhole_fit.to_dict() == second.pinhole_fit.to_dict()


def test_write_calibrations_writes_valid_files(calib_small_outcome, tmp_path) -> None:
    from liveblocks.calib.format import CalibrationFile
    from liveblocks.calib.report import CalibrationReport

    outcome = calib_small_outcome

    calibration_report = CalibrationReport(
        outcomes=(outcome,), created_at=DEFAULT_CREATED_AT, wall_ms=1.0
    )
    paths = write_calibrations(calibration_report, tmp_path)
    assert len(paths) == 1
    reloaded = CalibrationFile.load(tmp_path / "small.json")
    assert reloaded.to_json() == outcome.calibration.to_json()


def test_preset_data_file_is_self_consistent() -> None:
    presets, version = load_presets()
    assert version == "0.0.1"
    assert set(presets) == {"checkerboard", "charuco"}
    for preset in presets.values():
        assert preset.target.kind in ("checkerboard", "charuco")
        assert len(preset.views) >= 3
        assert preset.static.repeats >= 2
        thresholds = preset.thresholds
        assert 0.0 < thresholds.reprojection_rms_px
        assert thresholds.repeatability_mean_px <= thresholds.repeatability_max_px
        assert 0.0 < thresholds.focal_relative_error < 1.0
        assert preset.true_lens.has_distortion
        assert preset.width > 100 and preset.height > 100


@pytest.mark.parametrize("preset_name", ["checkerboard", "charuco"])
def test_every_preset_view_keeps_all_inner_corners_inside(preset_name: str) -> None:
    """每个标定位姿都必须让**全部内角点**入画（外圈白边被切一点不影响标定）。"""
    presets, _version = load_presets()
    preset = presets[preset_name]
    for index, pose in enumerate(preset.views):
        capture = render_capture(
            preset.target,
            pose=pose,
            lens=preset.true_lens,
            width=preset.width,
            height=preset.height,
            noise_sigma=0.0,
            seed=index,
        )
        corners = capture.ground_truth["corners"]
        outside = [entry["corner_id"] for entry in corners if not entry["inside_frame"]]
        assert outside == [], f"{preset_name} 视图 {index} 有角点出画：{outside}"
        assert len(corners) == len(preset.target.inner_corners())
