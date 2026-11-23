"""标记检测：暗区连通域 → 凸包 → 四边形 → ArUco 码字译码 → ``MarkerCandidateList``。

管线（纯标准库，见 ``docs/DECISIONS.md`` D-013）：

1. 局部自适应二值化（``image.adaptive_binary``）把「黑模块」挑出来；
2. 8 连通域：一个标记的黑边 + 内圈黑模块天然连成一个连通域，所以「一个连通域 ≈ 一个标记」；
   卡片的深色边框也是一个环，但它的「填充率」（像素数 / 四边形面积）远低于标记，会在第 3 步
   被剔除，最终由码字校验兜底；
3. 凸包 → 化简成 4 个顶点 → 检查凸性、边长、长宽比、填充率；
4. 采样 6×6 模块并比对字典码字（``decode.decode_marker``），黑边必须整圈偏暗；
5. 同一 ``marker_id`` 被检出多次时按置信度保留一个，并留诊断（``LB-V004``）。

输出里**不做排序**：候选顺序就是连通域的扫描顺序（左上→右下），协议 §3.2 要求排序属于 S3。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..synth.dictionaries import ArucoDictionary, load_aruco_dictionaries
from .decode import DecodeResult, angle_deg_ccw, decode_marker
from .frame import SCHEMA_VERSION, Frame
from .image import (
    Point,
    adaptive_binary,
    connected_components,
    convex_hull,
    is_convex_quad,
    outside_frame,
    polygon_area,
    quad_iou,
    refine_quad,
    side_lengths,
    simplify_to_quad,
)
from .pose import homography_from_marker_frame, pose_from_homography
from .protocol import (
    DIAG_DUPLICATE,
    DIAG_MARKER_CLIPPED,
    DIAG_UNREADABLE,
    diagnostic,
)

#: 候选四边形的最小边长（像素）。``multi-row`` 样例里最小的标记只有约 19 px。
MIN_SIDE_PX = 8.0

#: 候选四边形的长宽比允许范围（标记是正方形，透视会让它变形但不是长条）。
MIN_ASPECT = 0.4
MAX_ASPECT = 2.5

#: 连通域像素数 / 四边形面积 的下限：黑边环约 0.25~0.5，标记本体约 0.7~0.9。
MIN_FILL_RATIO = 0.45

#: 置信度里「位错」项的除数：允许 1 个位错时置信度仍有 0.75 的相对加成。
BIT_ERROR_SCALE = 4.0
#: 未能识别的候选置信度上限（身份不明就不能算高置信）。
UNIDENTIFIED_CONFIDENCE_CAP = 0.5

#: 只有标记的可见边长**至少**这么大时才输出位姿：小标记（如 ``multi-row`` 里的 19 px 标记）
#: 用单应分解求位姿会因角点量化噪声产生成倍的深度误差，宁可不输出（见 D-014 与 PROGRESS 限制）。
POSE_MIN_SIDE_PX = 24.0


def _clean_angle(value: float) -> float:
    """把 ``-0.0`` 这类浮点负零规整成 ``0.0``，避免 JSON 里出现 ``-0.0``。"""
    rounded = round(value, 3)
    return 0.0 if rounded == 0.0 else rounded


@dataclass(frozen=True)
class MarkerCandidate:
    """一个标记候选（对应 §3.1 ``markers[]`` 的一项）。"""

    index: int
    marker_id: int | None
    quad: tuple[Point, ...]
    corners_px: tuple[Point, ...]
    center_px: Point
    angle_deg: float
    confidence: float
    decode: DecodeResult
    touches_border: bool
    pose: dict[str, float] | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()

    @property
    def marker_id_str(self) -> str:
        return "unknown" if self.marker_id is None else str(self.marker_id)

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "marker_id": self.marker_id_str,
            "corners_px": [[int(round(x)), int(round(y))] for x, y in self.corners_px],
            "center_px": [int(round(self.center_px[0])), int(round(self.center_px[1]))],
            "angle_deg": _clean_angle(self.angle_deg),
            "confidence": round(self.confidence, 3),
        }
        if self.pose is not None:
            entry["pose"] = dict(self.pose)
        return entry


@dataclass(frozen=True)
class MarkerCandidateList:
    """§3.1 ``MarkerCandidateList`` + 加法式扩展 ``diagnostics``。"""

    frame_id: str
    dictionary: str
    markers: tuple[MarkerCandidate, ...]
    diagnostics: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "MarkerCandidateList",
            "frame_id": self.frame_id,
            "dictionary": self.dictionary,
            "markers": [marker.to_dict() for marker in self.markers],
            "diagnostics": [dict(entry) for entry in self.diagnostics],
        }


def _fill_ratio(component: list[int], quad: list[Point]) -> float:
    area = polygon_area(quad)
    if area <= 0.0:
        return 0.0
    return len(component) / area


def _confidence(decode: DecodeResult) -> float:
    """置信度 = 0.6 × 模块对比度 + 0.4 × 位质（1 - 位错/4），落在 [0,1]。

    定义见 ``docs/DECISIONS.md`` D-014：低光照主要压低对比度项，遮挡主要压低位质项。
    """
    contrast = decode.contrast_term
    bit_quality = max(0.0, 1.0 - decode.match.bit_errors / BIT_ERROR_SCALE)
    value = 0.6 * contrast + 0.4 * bit_quality
    if decode.match.marker_id is None:
        value = min(value, UNIDENTIFIED_CONFIDENCE_CAP)
    return min(max(value, 0.0), 1.0)


def _candidate_from_component(
    frame: Frame,
    component: list[int],
    aruco: ArucoDictionary,
    *,
    index: int,
    max_bit_errors: int,
) -> MarkerCandidate | None:
    width, height = frame.width, frame.height
    points: list[Point] = [(float(pixel % width), float(pixel // width)) for pixel in component]
    hull = convex_hull(points)
    if len(hull) < 4:
        return None
    quad = simplify_to_quad(hull)
    if len(quad) != 4 or not is_convex_quad(quad):
        return None
    sides = side_lengths(quad)
    shortest = min(sides)
    longest = max(sides)
    if shortest < MIN_SIDE_PX or longest <= 0.0:
        return None
    if not MIN_ASPECT <= shortest / longest <= MAX_ASPECT:
        return None
    if _fill_ratio(component, quad) < MIN_FILL_RATIO:
        return None
    try:
        decode = decode_marker(frame.image, quad, aruco, max_bit_errors=max_bit_errors)
    except ValueError:
        return None  # 单应退化：顶点几乎共线
    # 用解码得到的黑白基准做亚像素边缘细化，再用更准的四边形重新解码（身份/置信度以第二次为准）。
    refined = refine_quad(
        frame.image,
        quad,
        black_level=decode.black_level,
        white_level=decode.white_level,
    )
    if refined != quad:
        try:
            refined_decode = decode_marker(
                frame.image, refined, aruco, max_bit_errors=max_bit_errors
            )
        except ValueError:
            refined_decode = None
        if refined_decode is not None:
            quad = refined
            decode = refined_decode

    rotations = decode.match.rotations
    ordered = tuple(quad[(i + rotations) % 4] for i in range(4))
    center: Point = (
        sum(point[0] for point in quad) / 4.0,
        sum(point[1] for point in quad) / 4.0,
    )
    angle = angle_deg_ccw(list(ordered)) if decode.match.marker_id is not None else 0.0
    touches_border = not all(
        not outside for outside in outside_frame(list(hull), width, height)
    ) or any(
        point[0] <= 0.0 or point[1] <= 0.0 or point[0] >= width - 1.0 or point[1] >= height - 1.0
        for point in hull
    )

    diagnostics: list[dict[str, Any]] = []
    if decode.match.marker_id is None:
        reason = "码字不可靠（可能被遮挡或对比度不足）"
        if decode.match.ambiguous:
            reason = "码字在多个标记之间无法区分"
        elif not decode.border_dark:
            reason = "黑边不完整"
        diagnostics.append(
            diagnostic(
                DIAG_UNREADABLE,
                "warning",
                f"候选 m-{index}：{reason}（最佳距离 {decode.match.bit_errors} 位）",
                candidate_ids=[f"m-{index}"],
            )
        )
    if touches_border:
        diagnostics.append(
            diagnostic(
                DIAG_MARKER_CLIPPED,
                "warning" if decode.match.marker_id is None else "info",
                f"候选 m-{index} 触及画面边界（标记被裁切）",
                candidate_ids=[f"m-{index}"],
            )
        )

    return MarkerCandidate(
        index=index,
        marker_id=decode.match.marker_id,
        quad=tuple(quad),
        corners_px=ordered,
        center_px=center,
        angle_deg=angle,
        confidence=_confidence(decode),
        decode=decode,
        touches_border=touches_border,
        pose=None,
        diagnostics=tuple(diagnostics),
    )


def detect_markers(
    frame: Frame,
    *,
    aruco: ArucoDictionary | None = None,
    marker_size_mm: float | None = None,
    block: int = 21,
    offset: int = 8,
    min_area: int = 60,
    max_bit_errors: int = 1,
) -> MarkerCandidateList:
    """在一帧里检测 ArUco 标记，返回 ``MarkerCandidateList``。

    ``marker_size_mm`` 只影响位姿估计（需要 ``frame.intrinsics``），不影响检出与译码。
    """
    dictionary = aruco or _default_aruco()
    mask = adaptive_binary(frame.image, block=block, offset=offset)
    components = connected_components(mask, frame.width, frame.height, min_area=min_area)

    candidates: list[MarkerCandidate] = []
    diagnostics: list[dict[str, Any]] = []
    for component in components:
        candidate = _candidate_from_component(
            frame,
            component,
            dictionary,
            index=len(candidates),
            max_bit_errors=max_bit_errors,
        )
        if candidate is None:
            continue
        apparent_side = min(side_lengths(list(candidate.corners_px)))
        if (
            marker_size_mm is not None
            and frame.intrinsics is not None
            and candidate.marker_id is not None
            and apparent_side >= POSE_MIN_SIDE_PX
        ):
            try:
                matrix = homography_from_marker_frame(list(candidate.corners_px), marker_size_mm)
            except ValueError:
                matrix = None
            if matrix is not None:
                candidate = _with_pose(candidate, pose_from_homography(matrix, frame.intrinsics))
        candidates.append(candidate)

    kept, dedupe_diagnostics = _deduplicate(candidates)
    diagnostics.extend(dedupe_diagnostics)
    for candidate in kept:
        diagnostics.extend(candidate.diagnostics)
    return MarkerCandidateList(
        frame_id=frame.frame_id,
        dictionary=dictionary.name,
        markers=tuple(kept),
        diagnostics=tuple(diagnostics),
    )


def _with_pose(candidate: MarkerCandidate, pose: dict[str, float]) -> MarkerCandidate:
    return MarkerCandidate(
        index=candidate.index,
        marker_id=candidate.marker_id,
        quad=candidate.quad,
        corners_px=candidate.corners_px,
        center_px=candidate.center_px,
        angle_deg=candidate.angle_deg,
        confidence=candidate.confidence,
        decode=candidate.decode,
        touches_border=candidate.touches_border,
        pose=pose,
        diagnostics=candidate.diagnostics,
    )


def _deduplicate(
    candidates: list[MarkerCandidate],
) -> tuple[list[MarkerCandidate], list[dict[str, Any]]]:
    """同一 ``marker_id`` 的重复检出只保留置信度最高的一个，并留诊断。"""
    kept: list[MarkerCandidate] = []
    diagnostics: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.marker_id is None:
            kept.append(candidate)
            continue
        duplicate_index = None
        for position, existing in enumerate(kept):
            if existing.marker_id != candidate.marker_id:
                continue
            if quad_iou(list(existing.quad), list(candidate.quad)) >= 0.5:
                duplicate_index = position
                break
        if duplicate_index is None:
            kept.append(candidate)
            continue
        existing = kept[duplicate_index]
        loser, winner = (
            (existing, candidate) if candidate.confidence > existing.confidence else (candidate, existing)
        )
        diagnostics.append(
            diagnostic(
                DIAG_DUPLICATE,
                "info",
                f"标记 {candidate.marker_id_str} 被重复检出：保留置信度 "
                f"{winner.confidence:.2f} 的候选，丢弃 {loser.confidence:.2f} 的候选",
                candidate_ids=[f"m-{winner.index}", f"m-{loser.index}"],
            )
        )
        if winner is candidate:
            kept[duplicate_index] = candidate
    return kept, diagnostics


def _default_aruco() -> ArucoDictionary:
    from ..synth.dictionaries import load_card_dictionary

    dictionaries = load_aruco_dictionaries()
    card_dictionary = load_card_dictionary(dictionaries)
    return dictionaries[card_dictionary.aruco_dictionary]


__all__ = [
    "MarkerCandidate",
    "MarkerCandidateList",
    "MIN_SIDE_PX",
    "detect_markers",
]

