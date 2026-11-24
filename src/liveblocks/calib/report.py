"""标定的验收报告：一条命令复现「重投影误差 + 校正前后角点重复性」的全部数字。

一次报告做三件事，全部基于**合成**数据（真值内参/畸变已知，因此可以说「标定把真值找回来了多少」）：

1. **标定**：按预设置渲染 ``N`` 张带畸变的标定图 → 检角点 → 解内参/畸变 → 打印重投影误差；
2. **重复性**：同一个静态位姿连拍 ``M`` 次（只有噪声种子不同，场景完全不动）→ 在**原始图**上
   检角点、在**校正后**的图像上再检角点 → 给出两种情况下同一角点跨次测量的偏差（均值 / 最大值，像素）；
3. **真值对照**：估计内参与真值的偏差、校正后角点与理想针孔网格的偏差。

**这不是真实摄像头的标定结论**：``待人工验证`` 一节列出人该怎么在本机摄像头上验。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from ..synth.calibtarget import CalibTarget, CalibrationCapture, render_capture
from ..synth.geometry import Camera, Pose, plane_homography
from ..synth.geometry import apply_homography as apply_plane_homography
from ..synth.lens import LensModel, undistort_raster
from ..synth.raster import Raster
from ..vision.image import GrayImage
from .corners import CornerSet, detect_corners
from .format import (
    CalibrationFile,
    ReprojectionError,
    ViewRecord,
)
from .presets import CalibPreset, load_presets
from .solve import (
    CalibrationError,
    CalibrationResult,
    apply_homography,
    calibrate,
    homography_dlt,
)

#: ``report`` 默认使用的固定时间戳：让报告可以当 golden 快照（真实标定请用 ``--created-at``）。
DEFAULT_CREATED_AT = "2026-11-24T00:00:00Z"

#: 待人工验证的条目（真实摄像头相关的结论，代理不得代替人填）。
HUMAN_VERIFICATION: tuple[tuple[str, str, str], ...] = (
    (
        "真实摄像头重投影误差",
        "本报告的数字全部来自合成图。要声称某个真实摄像头的重投影误差，必须由人在实物上采集并跑一遍标定。",
        "① 按 `liveblocks calibrate targets` 给出的参数打印标定板，并用卡尺量 3 处方块边长；"
        "② 用 LB-030 采集台（`liveblocks serve-cam serve`）抓 15 张以上、覆盖画面四角、带 20°~30° 倾角的板图，"
        "落进 `fixtures/live/<时间戳>/`；③ 运行 `liveblocks calibrate run --dir fixtures/live/<时间戳> "
        "--target <板名> --out calib-<设备>.json`；④ 把打印出的重投影 RMS 连同设备型号、分辨率记进 "
        "`docs/PROGRESS.md` 的「待硬件验证」，人确认后才能移到「已验证」。",
    ),
    (
        "可用拍摄距离 / 识别距离",
        "本单不声明任何真实设备的可用距离或景深；合成图里的 z 值只是渲染参数。",
        "在真实摄像头上按目标距离摆板，跑 `liveblocks recognize report` 记录检出率与角点误差，"
        "结论必须附实测记录（设备型号 + 距离 + 帧数 + 漏检数）。",
    ),
    (
        "板面平整度与打印尺寸误差",
        "合成板是理想平面、方块边长完全精确；打印板或亚克力板的实际平整度与尺寸误差会影响真实标定。",
        "用卡尺量方块边长（记 3 处以上）并与 `calib_presets.json` 里的 `square_mm` 对比，"
        "把实测值写进 Issue，不要沿用标称值当作已实测。",
    ),
)


class ReportError(RuntimeError):
    """报告跑不下去（视图不够、检不到角点、标定数值失败）。"""


@dataclass(frozen=True)
class ViewOutcome:
    """一个标定视图的处理结果。"""

    index: int
    corners: int
    rms_px: float
    method: str
    markers: int
    marker_matches: int
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "corners": self.corners,
            "rms_px": round(self.rms_px, 6),
            "method": self.method,
            "markers": self.markers,
            "marker_matches": self.marker_matches,
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class Repeatability:
    """同一静态场景的角点重复性（像素）与跟真值的偏差（像素）。"""

    repeats: int
    corners: int
    mean_px: float
    max_px: float
    truth_mean_px: float = 0.0
    truth_max_px: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "repeats": self.repeats,
            "corners": self.corners,
            "mean_px": round(self.mean_px, 6),
            "max_px": round(self.max_px, 6),
            "truth_mean_px": round(self.truth_mean_px, 6),
            "truth_max_px": round(self.truth_max_px, 6),
        }


@dataclass(frozen=True)
class PinholeFit:
    """「画面能不能用一个针孔单应解释」的残差（像素）：校正的意义就在这里。"""

    raw_rms_px: float
    corrected_rms_px: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_rms_px": round(self.raw_rms_px, 6),
            "corrected_rms_px": round(self.corrected_rms_px, 6),
        }


@dataclass(frozen=True)
class Accuracy:
    """估计内参与真值的偏差。"""

    focal_relative_error: float
    principal_point_px: float
    distortion_absolute_error: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "focal_relative_error": round(self.focal_relative_error, 6),
            "principal_point_px": round(self.principal_point_px, 6),
            "distortion_absolute_error": round(self.distortion_absolute_error, 6),
        }


@dataclass(frozen=True)
class PresetOutcome:
    """一个预设的完整结果。"""

    preset: CalibPreset
    calibration: CalibrationFile
    result: CalibrationResult
    views: tuple[ViewOutcome, ...]
    raw_repeatability: Repeatability
    corrected_repeatability: Repeatability
    pinhole_fit: PinholeFit
    accuracy: Accuracy
    capture_ms: float
    calibrate_ms: float
    diagnostics: tuple[str, ...] = ()

    def acceptance(self) -> list[tuple[bool, str, str]]:
        """验收三条 + 真值对照三条；返回 ``(是否通过, 条目, 依据)``。"""
        thresholds = self.preset.thresholds
        rms = self.calibration.reprojection_error.rms_px
        raw = self.raw_repeatability
        fixed = self.corrected_repeatability
        return [
            (
                rms <= thresholds.reprojection_rms_px,
                f"LB-B006 重投影误差低于阈值（≤ {thresholds.reprojection_rms_px:g} px）",
                f"RMS {rms:.4f} px，最大 {self.calibration.reprojection_error.max_px:.4f} px，"
                f"{self.calibration.image_count} 张标定图 / {self.calibration.reprojection_error.point_count} 个角点",
            ),
            (
                fixed.mean_px <= thresholds.repeatability_mean_px
                and fixed.max_px <= thresholds.repeatability_max_px,
                "LB-B007 校正后角点重复性在阈值内"
                f"（均值 ≤ {thresholds.repeatability_mean_px:g} px，最大 ≤ {thresholds.repeatability_max_px:g} px）",
                f"校正前 {raw.mean_px:.4f} / {raw.max_px:.4f} px → "
                f"校正后 {fixed.mean_px:.4f} / {fixed.max_px:.4f} px（均值 / 最大值，{fixed.repeats} 次重复）",
            ),
            (
                self.accuracy.focal_relative_error <= thresholds.focal_relative_error
                and self.accuracy.principal_point_px <= thresholds.principal_point_px,
                "估计内参接近真值"
                f"（焦距相对误差 ≤ {thresholds.focal_relative_error:.0%}，主点偏差 ≤ "
                f"{thresholds.principal_point_px:g} px）",
                f"fx/fy 相对误差 {self.accuracy.focal_relative_error:.2%}，"
                f"主点偏差 {self.accuracy.principal_point_px:.2f} px，"
                f"畸变系数最大绝对误差 {self.accuracy.distortion_absolute_error:.5f}",
            ),
            (
                self.pinhole_fit.corrected_rms_px <= thresholds.repeatability_mean_px,
                "校正后画面可以用一个针孔单应解释（单应拟合残差 ≤ "
                f"{thresholds.repeatability_mean_px:g} px）",
                f"校正前单应残差 {self.pinhole_fit.raw_rms_px:.4f} px → "
                f"校正后 {self.pinhole_fit.corrected_rms_px:.4f} px",
            ),
        ]

    def passes(self) -> bool:
        return all(ok for ok, _criterion, _evidence in self.acceptance())


@dataclass(frozen=True)
class CalibrationReport:
    """多个预设的报告。"""

    outcomes: tuple[PresetOutcome, ...]
    created_at: str
    wall_ms: float
    golden: dict[str, str] = field(default_factory=dict)

    def passes(self) -> bool:
        if self.golden and any(not ok for ok in self.golden.values()):
            return False
        return all(outcome.passes() for outcome in self.outcomes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "CalibrationReport",
            "schema_version": "0.0.1",
            "created_at": self.created_at,
            "wall_ms": round(self.wall_ms, 3),
            "presets": [
                {
                    "preset": outcome.preset.name,
                    "target": outcome.preset.target.to_dict(),
                    "image": {
                        "width": outcome.preset.width,
                        "height": outcome.preset.height,
                    },
                    "calibration": outcome.calibration.to_dict(),
                    "true_lens": outcome.preset.true_lens.to_dict(),
                    "views": [view.to_dict() for view in outcome.views],
                    "repeatability": {
                        "raw": outcome.raw_repeatability.to_dict(),
                        "corrected": outcome.corrected_repeatability.to_dict(),
                    },
                    "pinhole_fit": outcome.pinhole_fit.to_dict(),
                    "accuracy": outcome.accuracy.to_dict(),
                    "capture_ms": round(outcome.capture_ms, 3),
                    "calibrate_ms": round(outcome.calibrate_ms, 3),
                    "diagnostics": list(outcome.diagnostics),
                    "acceptance": [
                        {"ok": ok, "criterion": criterion, "evidence": evidence}
                        for ok, criterion, evidence in outcome.acceptance()
                    ],
                }
                for outcome in self.outcomes
            ],
            "golden": dict(self.golden),
            "human_verification": [
                {"item": item, "why": why, "steps": steps} for item, why, steps in HUMAN_VERIFICATION
            ],
        }


def _capture(preset: CalibPreset, pose: Pose, *, seed: int, capture_id: str) -> CalibrationCapture:
    return render_capture(
        preset.target,
        pose=pose,
        lens=preset.true_lens,
        width=preset.width,
        height=preset.height,
        capture_id=capture_id,
        gain=preset.lighting.gain,
        vignette=preset.lighting.vignette,
        noise_sigma=preset.lighting.noise_sigma,
        blur_radius_px=preset.lighting.blur_radius_px,
        seed=seed,
    )


def _ideal_corners(
    target: CalibTarget, lens: LensModel, pose: Pose, *, distorted: bool
) -> dict[int, tuple[float, float]]:
    """静态场景里每个角点的「理想位置」：真值内参的针孔投影（或再叠上真值畸变）。"""
    camera = Camera(fx=lens.fx, fy=lens.fy, cx=lens.cx, cy=lens.cy)
    homography = plane_homography(camera, pose)
    out: dict[int, tuple[float, float]] = {}
    for corner_id, x_mm, y_mm in target.inner_corners():
        point = apply_plane_homography(homography, x_mm, y_mm)
        out[corner_id] = lens.distort(*point) if distorted else point
    return out


def _spread(measurements: list[dict[int, tuple[float, float]]]) -> tuple[float, float, int]:
    """同一角点多次测量的偏差：相对各次均值，返回 ``(均值, 最大值, 角点数)``。"""
    common = set(measurements[0])
    for entry in measurements[1:]:
        common &= set(entry)
    if not common:
        return (0.0, 0.0, 0)
    deviations: list[float] = []
    for corner_id in sorted(common):
        xs = [entry[corner_id][0] for entry in measurements]
        ys = [entry[corner_id][1] for entry in measurements]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        for x, y in zip(xs, ys):
            deviations.append(math.hypot(x - mean_x, y - mean_y))
    return (
        sum(deviations) / len(deviations),
        max(deviations),
        len(common),
    )


def _truth_deviation(
    measurements: list[dict[int, tuple[float, float]]],
    truth: dict[int, tuple[float, float]],
) -> tuple[float, float]:
    deviations: list[float] = []
    for entry in measurements:
        for corner_id, point in entry.items():
            if corner_id not in truth:
                continue
            tx, ty = truth[corner_id]
            deviations.append(math.hypot(point[0] - tx, point[1] - ty))
    if not deviations:
        return (0.0, 0.0)
    return (sum(deviations) / len(deviations), max(deviations))


def _pinhole_fit_rms(corners: dict[int, tuple[float, float]], target: CalibTarget) -> float:
    """用**一个针孔单应**（不含畸变）解释这批角点，返回残差 RMS（像素）。"""
    object_points = target.object_points()
    common = [corner_id for corner_id in sorted(corners) if corner_id in object_points]
    if len(common) < 4:
        return float("nan")
    source = [object_points[corner_id] for corner_id in common]
    target_points = [corners[corner_id] for corner_id in common]
    try:
        homography = homography_dlt(source, target_points)
    except (CalibrationError, ValueError):
        return float("nan")
    squared = 0.0
    for (x_mm, y_mm), (u, v) in zip(source, target_points):
        pu, pv = apply_homography(homography, x_mm, y_mm)
        squared += (pu - u) ** 2 + (pv - v) ** 2
    return math.sqrt(squared / len(common))


def _detect_on(image_pixels: bytes, width: int, height: int, target: CalibTarget) -> CornerSet:
    return detect_corners(GrayImage(width, height, image_pixels), target)


def run_preset(preset: CalibPreset, *, created_at: str = DEFAULT_CREATED_AT) -> PresetOutcome:
    """跑一个预设：采集 → 标定 → 重复性 → 汇总。"""
    started = time.perf_counter()
    corner_sets: list[CornerSet] = []
    images: list[CalibrationCapture] = []
    for index, pose in enumerate(preset.views):
        capture = _capture(
            preset, pose, seed=preset.view_seed(index), capture_id=f"view-{index:02d}"
        )
        images.append(capture)
        corner_sets.append(_detect_on(capture.pixels, capture.width, capture.height, preset.target))
    capture_ms = (time.perf_counter() - started) * 1000.0

    usable = [item for item in corner_sets if len(item.corners) >= 6]
    if len(usable) < 3:
        raise ReportError(
            f"LB-B005 预设 {preset.name}：只有 {len(usable)} 个视图检到 ≥6 个角点（至少要 3 个）"
        )
    diagnostics: list[str] = []
    for index, corner_set in enumerate(usable):
        for entry in corner_set.diagnostics:
            if entry["severity"] in ("error", "warning"):
                diagnostics.append(f"视图 {index}：{entry['code']} {entry['message']}")

    observations = [
        corner_set.observation(width=preset.width, height=preset.height) for corner_set in usable
    ]
    started = time.perf_counter()
    try:
        result = calibrate(observations, preset.target.object_points())
    except CalibrationError as error:
        raise ReportError(f"预设 {preset.name}：标定失败（{error}）") from None
    calibrate_ms = (time.perf_counter() - started) * 1000.0

    per_view_rms = result.per_view_rms_px()
    views = tuple(
        ViewOutcome(
            index=index,
            corners=len(corner_set.corners),
            rms_px=per_view_rms[index],
            method=corner_set.method,
            markers=corner_set.markers,
            marker_matches=corner_set.marker_matches,
            diagnostics=tuple(
                f"{entry['code']} {entry['message']}"
                for entry in corner_set.diagnostics
                if entry["severity"] in ("error", "warning")
            ),
        )
        for index, corner_set in enumerate(usable)
    )

    calibration = CalibrationFile(
        calibration_id=f"calib-{preset.name}-synthetic",
        created_at=created_at,
        target=preset.target,
        lens=result.lens,
        image_width=preset.width,
        image_height=preset.height,
        image_count=len(usable),
        reprojection_error=ReprojectionError(
            rms_px=result.reprojection_rms_px,
            max_px=result.reprojection_max_px,
            per_view_rms_px=tuple(per_view_rms),
            point_count=result.point_count,
        ),
        views=tuple(
            ViewRecord(
                view_id=f"view-{view.index:02d}",
                corners=view.corners,
                rms_px=view.rms_px,
                markers=view.markers,
                marker_matches=view.marker_matches,
                method=view.method,
            )
            for view in views
        ),
        source={
            "kind": "synthetic",
            "preset": preset.name,
            "preset_version": preset.version,
            "seed": preset.seed,
            "generator": "liveblocks.synth",
            "note": "合成数据，非真实摄像头标定结论",
        },
        notes=(
            "本文件由合成场景生成：真值内参/畸变已知，只用于回归与算法自检。",
            "真实摄像头的标定数字需要人按 docs/CALIB.md §8 的步骤实测，代理不得代填。",
        ),
    )

    # --- 重复性：同一静态场景连拍 M 次 ---
    static_pose = preset.static.pose
    raw_measurements: list[dict[int, tuple[float, float]]] = []
    corrected_measurements: list[dict[int, tuple[float, float]]] = []
    for repeat in range(preset.static.repeats):
        seed = preset.static_seed + repeat
        capture = _capture(
            preset, static_pose, seed=seed, capture_id=f"static-{repeat:02d}"
        )
        raw_set = _detect_on(capture.pixels, capture.width, capture.height, preset.target)
        if not raw_set.corners:
            continue
        raw_measurements.append(dict(raw_set.corners))
        raster = Raster(capture.width, capture.height, bytearray(capture.pixels))
        corrected = undistort_raster(raster, result.lens)
        corrected_set = _detect_on(corrected.as_bytes(), capture.width, capture.height, preset.target)
        if corrected_set.corners:
            corrected_measurements.append(dict(corrected_set.corners))
    if not raw_measurements or not corrected_measurements:
        raise ReportError(f"预设 {preset.name}：静态场景检不出角点，无法给出重复性")

    raw_mean, raw_max, raw_count = _spread(raw_measurements)
    raw_truth_mean, raw_truth_max = _truth_deviation(
        raw_measurements, _ideal_corners(preset.target, preset.true_lens, static_pose, distorted=True)
    )
    fixed_mean, fixed_max, fixed_count = _spread(corrected_measurements)
    fixed_truth_mean, fixed_truth_max = _truth_deviation(
        corrected_measurements, _ideal_corners(preset.target, preset.true_lens, static_pose, distorted=False)
    )
    raw_repeatability = Repeatability(
        repeats=len(raw_measurements),
        corners=raw_count,
        mean_px=raw_mean,
        max_px=raw_max,
        truth_mean_px=raw_truth_mean,
        truth_max_px=raw_truth_max,
    )
    corrected_repeatability = Repeatability(
        repeats=len(corrected_measurements),
        corners=fixed_count,
        mean_px=fixed_mean,
        max_px=fixed_max,
        truth_mean_px=fixed_truth_mean,
        truth_max_px=fixed_truth_max,
    )

    pinhole_fit = PinholeFit(
        raw_rms_px=_pinhole_fit_rms(raw_measurements[0], preset.target),
        corrected_rms_px=_pinhole_fit_rms(corrected_measurements[0], preset.target),
    )

    truth = preset.true_lens
    accuracy = Accuracy(
        focal_relative_error=max(
            abs(result.lens.fx - truth.fx) / truth.fx, abs(result.lens.fy - truth.fy) / truth.fy
        ),
        principal_point_px=max(abs(result.lens.cx - truth.cx), abs(result.lens.cy - truth.cy)),
        distortion_absolute_error=max(
            abs(result.lens.k1 - truth.k1),
            abs(result.lens.k2 - truth.k2),
            abs(result.lens.p1 - truth.p1),
            abs(result.lens.p2 - truth.p2),
        ),
    )

    return PresetOutcome(
        preset=preset,
        calibration=calibration,
        result=result,
        views=views,
        raw_repeatability=raw_repeatability,
        corrected_repeatability=corrected_repeatability,
        pinhole_fit=pinhole_fit,
        accuracy=accuracy,
        capture_ms=capture_ms,
        calibrate_ms=calibrate_ms,
        diagnostics=tuple(diagnostics),
    )


def build_report(
    names: list[str] | None = None,
    *,
    created_at: str = DEFAULT_CREATED_AT,
    golden_dir=None,
) -> CalibrationReport:
    """跑一个或多个预设；``golden_dir`` 给出时把生成的标定文件与目录里的 golden 逐字节比对。"""
    from pathlib import Path

    presets, _version = load_presets()
    selected = names or sorted(presets)
    outcomes: list[PresetOutcome] = []
    started = time.perf_counter()
    for name in selected:
        if name not in presets:
            raise ReportError(f"没有标定预设 {name!r}；可用：{', '.join(sorted(presets))}")
        outcomes.append(run_preset(presets[name], created_at=created_at))
    golden: dict[str, str] = {}
    if golden_dir is not None:
        directory = Path(golden_dir)
        for outcome in outcomes:
            path = directory / f"{outcome.preset.name}.json"
            if not path.is_file():
                golden[outcome.preset.name] = f"缺少 golden：{path}"
                continue
            expected = path.read_text(encoding="utf-8")
            golden[outcome.preset.name] = (
                "字节一致" if expected == outcome.calibration.to_json() else "不一致（重新生成 golden？）"
            )
    wall_ms = (time.perf_counter() - started) * 1000.0
    return CalibrationReport(
        outcomes=tuple(outcomes), created_at=created_at, wall_ms=wall_ms, golden=golden
    )


def _format_point(value: float) -> str:
    return f"{value:.4f}"


def format_report(report: CalibrationReport) -> str:
    """把报告渲染成给人看的文本（数字全部是本机真实跑出来的）。"""
    lines: list[str] = []
    lines.append(
        f"标定报告：{len(report.outcomes)} 个合成预设，时间戳 {report.created_at}，"
        f"总耗时 {report.wall_ms:.0f} ms（实测，不是性能结论）"
    )
    lines.append(
        "说明：全部数字来自**合成图**（真值内参/畸变已知）。真实摄像头的标定结论不在本命令内，"
        "见文末「待人工验证」。"
    )
    for outcome in report.outcomes:
        preset = outcome.preset
        calibration = outcome.calibration
        lines.append("")
        lines.append("=" * 78)
        lines.append(f"[{preset.name}] {preset.description}")
        lines.append(f"  标定板：{preset.target.describe()}")
        lines.append(
            f"  图像：{preset.width}×{preset.height}，{calibration.image_count} 张标定图，"
            f"{calibration.reprojection_error.point_count} 个角点；"
            f"采集 {outcome.capture_ms:.0f} ms / 求解 {outcome.calibrate_ms:.0f} ms"
        )
        lines.append("  逐视图：")
        for view in outcome.views:
            extra = ""
            if view.markers:
                extra = f"，标记 {view.markers}（id 对上 {view.marker_matches}）"
            lines.append(
                f"    view-{view.index:02d}  角点 {view.corners:>3}  残差 RMS {view.rms_px:.4f} px"
                f"  方法 {view.method}{extra}"
            )
        lines.append(
            f"  重投影误差：RMS {_format_point(calibration.reprojection_error.rms_px)} px，"
            f"最大 {_format_point(calibration.reprojection_error.max_px)} px"
        )
        lines.append(f"  估计内参：{calibration.lens.describe()}")
        lines.append(f"  真值内参：{preset.true_lens.describe()}")
        raw = outcome.raw_repeatability
        fixed = outcome.corrected_repeatability
        lines.append(
            f"  角点重复性（同一静态场景 {raw.repeats} 次重复，{raw.corners} 个角点，像素）："
        )
        lines.append(
            f"    校正前：均值 {_format_point(raw.mean_px)}，最大 {_format_point(raw.max_px)}"
        )
        lines.append(
            f"    校正后：均值 {_format_point(fixed.mean_px)}，最大 {_format_point(fixed.max_px)}"
        )
        lines.append(
            f"  角点与真值（理想针孔投影）的偏差（像素）：校正前 均值 "
            f"{_format_point(raw.truth_mean_px)} / 最大 {_format_point(raw.truth_max_px)}；"
            f"校正后 均值 {_format_point(fixed.truth_mean_px)} / 最大 "
            f"{_format_point(fixed.truth_max_px)}"
        )
        lines.append(
            f"  单个针孔单应能解释多少（残差 RMS，像素）：校正前 "
            f"{_format_point(outcome.pinhole_fit.raw_rms_px)} → 校正后 "
            f"{_format_point(outcome.pinhole_fit.corrected_rms_px)}"
        )
        for note in outcome.diagnostics:
            lines.append(f"  ! {note}")
        lines.append("  验收对照：")
        for ok, criterion, evidence in outcome.acceptance():
            lines.append(f"    [{'通过' if ok else '未通过'}] {criterion} —— {evidence}")
    if report.golden:
        lines.append("")
        lines.append("  与入库 golden 的比对：")
        for name, verdict in report.golden.items():
            lines.append(f"    {name}: {verdict}")
    lines.append("")
    lines.append("待人工验证（真实摄像头相关，代理不得代填）：")
    for index, (item, why, steps) in enumerate(HUMAN_VERIFICATION, start=1):
        lines.append(f"  {index}. {item}：{why}")
        lines.append(f"     人工步骤：{steps}")
    lines.append("")
    lines.append(
        "结论：" + ("全部验收条目通过（仅限合成场景）" if report.passes() else "有验收条目未通过")
    )
    return "\n".join(lines) + "\n"


def write_calibrations(report: CalibrationReport, out_dir) -> list[str]:
    """把每个预设的标定文件写到目录里，返回写出的路径。"""
    from pathlib import Path

    directory = Path(out_dir)
    paths: list[str] = []
    for outcome in report.outcomes:
        path = outcome.calibration.write(directory / f"{outcome.preset.name}.json")
        paths.append(str(path))
    return paths
