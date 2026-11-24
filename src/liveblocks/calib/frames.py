"""把标定文件填进 ``Frame.intrinsics/distortion``，以及按标定把图校正成理想针孔图。

这两件事就是 LB-031 里「``Frame.intrinsics/distortion`` 的填充路径」：

- :func:`camera_lens` / :func:`apply_to_frame`：从标定文件取出内参与畸变，写进一帧的
  ``intrinsics`` / ``distortion`` / ``calibration_id``（**不猜**：标定文件里没有的数就留空）；
- :func:`undistort_frame`：把帧的像素按同一个模型校正成理想针孔图，并把该帧的
  ``distortion`` 归零（校正后这一帧已经不满足原畸变模型）。

帧的 ``image_ref`` 会被换成一个后缀 ``#undistorted`` 的引用，避免把校正图与原始图混为一谈。
"""

from __future__ import annotations

from ..synth.lens import LensModel, undistort_raster
from ..synth.raster import Raster
from ..vision.frame import Distortion, Frame, Intrinsics
from ..vision.image import GrayImage
from .format import CalibrationFile

#: 校正后帧 ``image_ref`` 的后缀。
UNDISTORTED_SUFFIX = "#undistorted"


def camera_lens(calibration: CalibrationFile) -> LensModel:
    """标定文件 → :class:`liveblocks.synth.lens.LensModel`（内参 + 畸变）。"""
    return calibration.lens


def intrinsics_of(calibration: CalibrationFile) -> Intrinsics:
    return Intrinsics(
        fx=calibration.lens.fx,
        fy=calibration.lens.fy,
        cx=calibration.lens.cx,
        cy=calibration.lens.cy,
    )


def distortion_of(calibration: CalibrationFile) -> Distortion:
    return Distortion(
        k1=calibration.lens.k1,
        k2=calibration.lens.k2,
        p1=calibration.lens.p1,
        p2=calibration.lens.p2,
        model=calibration.lens.model,
    )


def frame_fits_calibration(frame: Frame, calibration: CalibrationFile) -> bool:
    """这一帧的尺寸是否与标定时的图像尺寸一致（不一致就不能套用这份标定）。"""
    return (frame.width, frame.height) == (
        calibration.image_width,
        calibration.image_height,
    )


def apply_to_frame(frame: Frame, calibration: CalibrationFile) -> Frame:
    """把标定填进帧：``intrinsics`` / ``distortion`` / ``calibration_id``。

    尺寸不一致时抛 :class:`ValueError`——把 480×360 的标定套到 640×480 的帧上是**错的**，
    正确做法是按比例缩内参（不在本单范围，见 ``docs/CALIB.md`` §7 的待办）。
    """
    if not frame_fits_calibration(frame, calibration):
        raise ValueError(
            f"帧 {frame.frame_id} 是 {frame.width}×{frame.height}，"
            f"而标定 {calibration.calibration_id} 是 "
            f"{calibration.image_width}×{calibration.image_height}：不能直接套用"
        )
    return Frame(
        frame_id=frame.frame_id,
        image=frame.image,
        image_ref=frame.image_ref,
        source=frame.source,
        intrinsics=intrinsics_of(calibration),
        distortion=distortion_of(calibration),
        captured_at_ms=frame.captured_at_ms,
        calibration_id=calibration.calibration_id,
    )


def undistort_frame(frame: Frame, calibration: CalibrationFile) -> Frame:
    """把帧校正成理想针孔图（畸变归零、内参不变、``image_ref`` 加后缀）。"""
    if not frame_fits_calibration(frame, calibration):
        raise ValueError(
            f"帧 {frame.frame_id} 是 {frame.width}×{frame.height}，"
            f"与标定 {calibration.calibration_id} 的 "
            f"{calibration.image_width}×{calibration.image_height} 不一致"
        )
    raster = Raster(frame.width, frame.height, bytearray(frame.image.pixels))
    corrected = undistort_raster(raster, calibration.lens)
    pixels = corrected.as_bytes()
    image = GrayImage(frame.width, frame.height, pixels)
    reference = frame.image_ref
    if not reference.endswith(UNDISTORTED_SUFFIX):
        reference = reference + UNDISTORTED_SUFFIX
    return Frame(
        frame_id=frame.frame_id,
        image=image,
        image_ref=reference,
        source=frame.source,
        intrinsics=intrinsics_of(calibration),
        distortion=Distortion(),
        captured_at_ms=frame.captured_at_ms,
        calibration_id=calibration.calibration_id,
    )
