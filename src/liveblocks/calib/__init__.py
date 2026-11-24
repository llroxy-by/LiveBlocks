"""LB-031 标定：棋盘格 / ChArUco 两条流程、标定文件格式、``Frame`` 的填充路径。

模块划分：

- :mod:`liveblocks.synth.lens`：镜头畸变模型（径向 + 切向）与重采样，合成器与校正共用；
- :mod:`liveblocks.synth.calibtarget`：标定板定义与「带畸变的合成拍摄图」渲染（含角点真值）；
- :mod:`liveblocks.calib.corners`：角点检测（棋盘格 / ChArUco，纯标准库，复用 LB-003 的亚像素细化）；
- :mod:`liveblocks.calib.solve`：单应 → Zhang 闭式初值 → 联合 LM（Schur 补）精化；
- :mod:`liveblocks.calib.format`：标定文件（JSON）的格式、校验与读写；
- :mod:`liveblocks.calib.frames`：把标定填进 ``Frame.intrinsics/distortion``、按标定校正图像；
- :mod:`liveblocks.calib.dataset`：从目录里的图像跑标定（真实采集与合成共用）；
- :mod:`liveblocks.calib.report`：``liveblocks calibrate report`` 的验收报告（合成场景）。

**边界**：本包只做算法与文件格式。真实摄像头的标定数字必须由人按 ``docs/CALIB.md`` §8 的步骤
实测，代理不得代填（见 ``docs/PROGRESS.md`` 的「待人工验证」）。
"""

from __future__ import annotations

from .corners import CornerSet, detect_corners, detect_squares
from .dataset import DatasetError, DirectoryOutcome, calibrate_directory, summarize
from .format import (
    CalibrationFile,
    CalibrationFileError,
    ReprojectionError,
    ViewRecord,
    validate_calibration,
    validation_diagnostics,
)
from .frames import (
    apply_to_frame,
    camera_lens,
    distortion_of,
    frame_fits_calibration,
    intrinsics_of,
    undistort_frame,
)
from .presets import CalibPreset, load_preset, load_presets, preset_names
from .report import (
    CalibrationReport,
    PresetOutcome,
    ReportError,
    build_report,
    format_report,
    run_preset,
    write_calibrations,
)
from .solve import (
    CalibrationError,
    CalibrationResult,
    Observation,
    ViewPose,
    calibrate,
    homography_dlt,
    joint_refine,
    project_point,
    zhang_intrinsics,
)

__all__ = [
    "CalibPreset",
    "CalibrationError",
    "CalibrationFile",
    "CalibrationFileError",
    "CalibrationReport",
    "CalibrationResult",
    "CornerSet",
    "DatasetError",
    "DirectoryOutcome",
    "Observation",
    "PresetOutcome",
    "ReprojectionError",
    "ReportError",
    "ViewPose",
    "ViewRecord",
    "apply_to_frame",
    "build_report",
    "calibrate",
    "calibrate_directory",
    "camera_lens",
    "detect_corners",
    "detect_squares",
    "distortion_of",
    "format_report",
    "frame_fits_calibration",
    "homography_dlt",
    "intrinsics_of",
    "joint_refine",
    "load_preset",
    "load_presets",
    "preset_names",
    "project_point",
    "run_preset",
    "summarize",
    "undistort_frame",
    "validate_calibration",
    "validation_diagnostics",
    "write_calibrations",
    "zhang_intrinsics",
]
