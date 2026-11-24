"""从**目录里的图像**跑标定（真实采集与合成图共用同一条路径）。

LB-030 的采集台把样本落成 ``fixtures/live/<时间戳>/*.png``，本模块能把这样一个目录直接标定：
读 PNG（复用 LB-002 的零依赖解码器）→ 检角点 → 解内参/畸变 → 生成标定文件。

真机采集的目录里通常还有 ``.json`` 标注文件，本模块只读 ``*.png``，其余忽略。
**本模块不判断图是谁拍的**：``--source-kind`` 由调用方声明，合成与真实不能混为一谈。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..synth.calibtarget import CalibTarget
from ..synth.lens import LensModel
from ..synth.png import decode_gray
from ..vision.image import GrayImage
from .corners import CornerSet, detect_corners
from .format import CalibrationFile, ReprojectionError, ViewRecord
from .solve import CalibrationError, CalibrationResult, calibrate


@dataclass(frozen=True)
class DirectoryOutcome:
    """一个目录标定的结果。"""

    calibration: CalibrationFile
    result: CalibrationResult
    corner_sets: tuple[CornerSet, ...]
    skipped: tuple[str, ...]

    @property
    def used_images(self) -> int:
        return len(self.corner_sets)


class DatasetError(RuntimeError):
    """目录标定跑不下去。"""


def _load_image(path: Path) -> GrayImage:
    width, height, pixels = decode_gray(path.read_bytes())
    return GrayImage(width, height, pixels)


def calibrate_directory(
    directory: Path,
    target: CalibTarget,
    *,
    created_at: str,
    calibration_id: str | None = None,
    source_kind: str = "capture",
    min_corners: int = 6,
    notes: tuple[str, ...] = (),
) -> DirectoryOutcome:
    """把目录里的 ``*.png`` 当作同一块板的多次拍摄来标定。"""
    directory = Path(directory)
    if not directory.is_dir():
        raise DatasetError(f"目录不存在：{directory}")
    files = sorted(path for path in directory.glob("*.png") if path.is_file())
    if not files:
        raise DatasetError(f"{directory} 里没有 *.png")
    corner_sets: list[CornerSet] = []
    skipped: list[str] = []
    sizes: set[tuple[int, int]] = set()
    for path in files:
        image = _load_image(path)
        sizes.add((image.width, image.height))
        corner_set = detect_corners(image, target)
        if len(corner_set.corners) < min_corners:
            skipped.append(
                f"{path.name}: 只检出 {len(corner_set.corners)} 个角点（< {min_corners}）"
            )
            continue
        corner_sets.append(corner_set)
    if len(sizes) > 1:
        raise DatasetError(f"目录里的图像尺寸不一致：{sorted(sizes)}")
    if len(corner_sets) < 3:
        raise DatasetError(
            f"LB-B005 {directory}: 只有 {len(corner_sets)} 张图检到足够角点（至少要 3 张）；"
            f"跳过 {len(skipped)} 张"
        )
    width, height = next(iter(sizes))
    observations = [
        corner_set.observation(width=width, height=height) for corner_set in corner_sets
    ]
    try:
        result = calibrate(observations, target.object_points())
    except CalibrationError as error:
        raise DatasetError(f"标定失败：{error}") from None

    per_view_rms = result.per_view_rms_px()
    views = tuple(
        ViewRecord(
            view_id=f"view-{index:02d}",
            corners=len(corner_set.corners),
            rms_px=per_view_rms[index],
            markers=corner_set.markers,
            marker_matches=corner_set.marker_matches,
            method=corner_set.method,
        )
        for index, corner_set in enumerate(corner_sets)
    )
    calibration = CalibrationFile(
        calibration_id=calibration_id or f"calib-{directory.name}",
        created_at=created_at,
        target=target,
        lens=LensModel(
            fx=result.lens.fx,
            fy=result.lens.fy,
            cx=result.lens.cx,
            cy=result.lens.cy,
            k1=result.lens.k1,
            k2=result.lens.k2,
            p1=result.lens.p1,
            p2=result.lens.p2,
        ),
        image_width=width,
        image_height=height,
        image_count=len(corner_sets),
        reprojection_error=ReprojectionError(
            rms_px=result.reprojection_rms_px,
            max_px=result.reprojection_max_px,
            per_view_rms_px=tuple(per_view_rms),
            point_count=result.point_count,
        ),
        views=views,
        source={
            "kind": source_kind,
            "directory": str(directory),
            "images": len(corner_sets),
            "skipped": list(skipped),
        },
        notes=notes,
    )
    return DirectoryOutcome(
        calibration=calibration,
        result=result,
        corner_sets=tuple(corner_sets),
        skipped=tuple(skipped),
    )


def summarize(outcome: DirectoryOutcome) -> dict[str, Any]:
    """给 CLI 打印的摘要。"""
    return {
        "calibration_id": outcome.calibration.calibration_id,
        "images": outcome.used_images,
        "skipped": list(outcome.skipped),
        "reprojection_rms_px": round(outcome.calibration.reprojection_error.rms_px, 4),
        "reprojection_max_px": round(outcome.calibration.reprojection_error.max_px, 4),
        "dropped_points": outcome.result.dropped_points,
        "lens": outcome.calibration.lens.to_dict(),
    }
