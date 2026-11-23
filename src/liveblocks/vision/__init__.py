"""识别层（LB-003）：ArUco 标记候选 → 卡片候选。

对外两个层次：

- :func:`liveblocks.vision.detect_markers` —— 标记检测 + 译码（§3.1 ``MarkerCandidateList``）；
- :func:`liveblocks.vision.recognize` / :func:`liveblocks.vision.recognize_path` —— 加上卡片
  字典映射与状态判定（§3.2 ``CardCandidateList``）。

识别层**只输出候选**：不排序、不解析、不执行、不丢弃低置信候选（见 ``docs/DECISIONS.md``
D-008）。排列（LB-004）、解析（LB-005）、执行（LB-007）在各自的模块里做。
"""

from __future__ import annotations

from .cards import (
    UNKNOWN,
    CardCandidate,
    CardCandidateList,
    CardRegistry,
    build_card_candidates,
    card_geometry,
)
from .decode import DecodeResult, decode_marker, homography, homography_from_unit_square, sample_modules
from .frame import SCHEMA_VERSION, Frame, Intrinsics, frame_id_for, load_frame
from .image import GrayImage, adaptive_binary, connected_components, convex_hull, simplify_to_quad
from .markers import MarkerCandidate, MarkerCandidateList, detect_markers
from .pose import (
    homography_from_marker_frame,
    marker_corners_mm,
    pose_from_homography,
    reprojection_error,
)
from .protocol import (
    CARD_STATUSES,
    validate_card_candidate_list,
    validate_marker_candidate_list,
)
from .recognize import RecognitionResult, recognize, recognize_path
from .stats import (
    EvaluationReport,
    SceneEvaluation,
    evaluate_directory,
    evaluate_scene,
    format_report,
)

__all__ = [
    "CARD_STATUSES",
    "CardCandidate",
    "CardCandidateList",
    "CardRegistry",
    "DecodeResult",
    "EvaluationReport",
    "Frame",
    "GrayImage",
    "Intrinsics",
    "MarkerCandidate",
    "MarkerCandidateList",
    "RecognitionResult",
    "SCHEMA_VERSION",
    "SceneEvaluation",
    "UNKNOWN",
    "adaptive_binary",
    "build_card_candidates",
    "card_geometry",
    "connected_components",
    "convex_hull",
    "decode_marker",
    "detect_markers",
    "evaluate_directory",
    "evaluate_scene",
    "format_report",
    "frame_id_for",
    "homography",
    "homography_from_marker_frame",
    "homography_from_unit_square",
    "load_frame",
    "marker_corners_mm",
    "pose_from_homography",
    "recognize",
    "recognize_path",
    "reprojection_error",
    "sample_modules",
    "simplify_to_quad",
    "validate_card_candidate_list",
    "validate_marker_candidate_list",
]
