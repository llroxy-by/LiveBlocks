"""LB-031 命令行（``liveblocks calibrate …``）的测试。

覆盖每条子命令的「能跑 + 输出形状对」：``targets`` / ``corners`` / ``run`` / ``frame`` /
``undistort`` / ``validate`` / ``report``。其中 ``report`` 那条是**验收命令本身**：
一条命令复现全部数字、退出码 0，并与入库 golden 逐字节比对。
"""

from __future__ import annotations

import json

import pytest

from liveblocks import cli
from liveblocks.calib.format import CalibrationFile
from liveblocks.calib.presets import load_presets
from liveblocks.synth.calibtarget import render_capture
from liveblocks.synth.lens import LensModel
from liveblocks.synth.geometry import Pose


@pytest.fixture(scope="module")
def capture_dir(tmp_path_factory: pytest.TempPathFactory):
    """一个「真实采集目录」的替身：4 张合成的棋盘图（同尺寸、不同位姿）。"""
    presets, _version = load_presets()
    preset = presets["checkerboard"]
    directory = tmp_path_factory.mktemp("calib-captures")
    for index, pose in enumerate(preset.views[:4]):
        capture = render_capture(
            preset.target,
            pose=pose,
            lens=preset.true_lens,
            width=preset.width,
            height=preset.height,
            noise_sigma=1.0,
            seed=900 + index,
            capture_id=f"capture-{index}",
        )
        (directory / f"capture-{index}.png").write_bytes(capture.png)
    return directory


@pytest.fixture(scope="module")
def one_capture(tmp_path_factory: pytest.TempPathFactory):
    presets, _version = load_presets()
    preset = presets["checkerboard"]
    capture = render_capture(
        preset.target,
        pose=Pose(x_mm=0, y_mm=0, z_mm=360, yaw_deg=4, pitch_deg=20, roll_deg=-6),
        lens=preset.true_lens,
        width=preset.width,
        height=preset.height,
        noise_sigma=1.0,
        seed=7,
        capture_id="single",
    )
    path = tmp_path_factory.mktemp("calib-one") / "single.png"
    path.write_bytes(capture.png)
    return path


def test_calibrate_targets_lists_boards_and_presets(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["calibrate", "targets"]) == 0
    out = capsys.readouterr().out
    assert "checkerboard-9x6-25mm" in out
    assert "charuco-8x6-20mm" in out
    assert "checkerboard" in out and "charuco" in out


def test_calibrate_corners_prints_corner_json(
    one_capture, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            ["calibrate", "corners", "--image", str(one_capture), "--target", "checkerboard-9x6-25mm"]
        )
        == 0
    )
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["kind"] == "CalibrationCorners"
    assert document["detected"] == document["expected"] == 40
    assert "checkerboard-grid" in captured.err


def test_calibrate_run_writes_a_valid_calibration_file(
    capture_dir, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "calib.json"
    code = cli.main(
        [
            "calibrate",
            "run",
            "--dir",
            str(capture_dir),
            "--target",
            "checkerboard-9x6-25mm",
            "--out",
            str(out),
            "--created-at",
            "2026-11-24T00:00:00Z",
            "--source-kind",
            "synthetic",
            "--calibration-id",
            "calib-cli-test",
        ]
    )
    assert code == 0
    calibration = CalibrationFile.load(out)
    assert calibration.calibration_id == "calib-cli-test"
    assert calibration.image_count == 4
    assert calibration.source["kind"] == "synthetic"
    assert calibration.reprojection_error.rms_px < 1.0
    assert "重投影 RMS" in capsys.readouterr().out


def test_calibrate_run_rejects_unknown_target(capture_dir) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            ["calibrate", "run", "--dir", str(capture_dir), "--target", "no-such-board"]
        )
    assert "未知标定板" in str(excinfo.value.code)


def test_calibrate_validate_accepts_golden_and_rejects_broken(
    calib_dir, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = sorted(calib_dir.glob("*.json"))[0]
    assert cli.main(["calibrate", "validate", "--calib", str(golden)]) == 0
    assert "合格" in capsys.readouterr().out

    broken = tmp_path / "broken.json"
    document = json.loads(golden.read_text(encoding="utf-8"))
    document["intrinsics"]["fx"] = 0.0
    broken.write_text(json.dumps(document), encoding="utf-8")
    assert cli.main(["calibrate", "validate", "--calib", str(broken)]) == 1
    assert "LB-B008" in capsys.readouterr().err


def test_calibrate_frame_fills_intrinsics_and_distortion(
    calib_dir, one_capture, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = calib_dir / "checkerboard.json"
    if not golden.is_file():
        pytest.skip("缺少 checkerboard golden")
    assert (
        cli.main(["calibrate", "frame", "--calib", str(golden), "--image", str(one_capture)]) == 0
    )
    document = json.loads(capsys.readouterr().out)
    calibration = CalibrationFile.load(golden)
    assert document["kind"] == "Frame"
    assert document["calibration_id"] == calibration.calibration_id
    assert document["intrinsics"]["fx"] == pytest.approx(round(calibration.lens.fx, 6))
    assert document["distortion"]["model"] == "plumb_bob"
    assert document["distortion"]["k1"] == pytest.approx(round(calibration.lens.k1, 9))


def test_calibrate_undistort_writes_a_png(
    calib_dir, one_capture, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    golden = calib_dir / "checkerboard.json"
    if not golden.is_file():
        pytest.skip("缺少 checkerboard golden")
    out = tmp_path / "undistorted.png"
    assert (
        cli.main(
            ["calibrate", "undistort", "--calib", str(golden), "--image", str(one_capture), "--out", str(out)]
        )
        == 0
    )
    assert out.read_bytes().startswith(b"\x89PNG")
    assert "已写出校正图" in capsys.readouterr().out


def test_calibrate_report_reproduces_the_golden(
    calib_dir, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """验收命令：一条命令打印全部数字、与 golden 一致、退出码 0（约 16 秒）。"""
    report_json = tmp_path / "report.json"
    code = cli.main(
        [
            "calibrate",
            "report",
            "--preset",
            "checkerboard",
            "--golden",
            str(calib_dir),
            "--json",
            str(report_json),
        ]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.out + captured.err
    assert "重投影误差" in captured.out
    assert "校正前" in captured.out and "校正后" in captured.out
    assert "字节一致" in captured.out
    assert "待人工验证" in captured.out
    document = json.loads(report_json.read_text(encoding="utf-8"))
    preset = document["presets"][0]
    assert preset["preset"] == "checkerboard"
    presets, _version = load_presets()
    assert preset["calibration"]["image_count"] == len(presets["checkerboard"].views)
    assert all(entry["ok"] for entry in preset["acceptance"])
