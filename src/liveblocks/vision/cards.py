"""卡片映射：标记候选 → 卡片候选（``CardCandidateList``）。

这一层只做两件事（对应 LB-003 的范围，不做排序 / 解析 / 执行）：

1. **字典映射**：``marker_id`` → 卡片字典里的 ``card_id / card_type / card_face``；查不到就是
   ``unknown_card``（协议 §3.2 的枚举值），并在诊断里点名。
2. **卡片范围与状态**：用「标记 + 卡片注册版式」外推卡片四角，判断是否越出画面（``clipped``）；
   标记译码有残余位错时判 ``occluded``。

关于「卡片范围从哪来」：协议 §11 明确「卡片字典的登记格式」尚未定义，LB-002 的卡面版式
（``data/synth_layouts.json`` 的 ``card_layouts``：卡片尺寸、标记尺寸、标记中心）已经是现成的
物理注册信息，因此识别层**复用**它，并把它当作**采集配置**（等价于 ``Frame.calibration_id``：
跑之前就知道用的是哪种卡纸/版式），而不是从真值里读答案。逐卡版式/朝向的覆盖能力留在
:class:`CardRegistry` 的参数里，等 Q1/Q2 定了再进数据文件（见 D-014）。

状态优先级（单字段只能给一个值，其余事实进诊断，D-014）：``unknown_card`` > ``clipped`` >
``occluded`` > ``ok``；身份无法确定的候选给 ``occluded`` / ``ambiguous``，此时方向未知，
**不输出** ``orientation_quadrant``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..synth.dictionaries import (
    ArucoDictionary,
    CardDictionary,
    CardEntry,
    load_aruco_dictionaries,
    load_card_dictionary,
)
from ..synth.geometry import apply_homography
from ..synth.spec import load_layouts
from ..synth.texture import CardLayout
from .frame import SCHEMA_VERSION, Frame
from .image import Point, outside_frame
from .markers import MarkerCandidate, MarkerCandidateList
from .pose import homography_from_marker_frame
from .protocol import (
    DIAG_CARD_CLIPPED,
    DIAG_UNKNOWN_CARD,
    CARD_STATUSES,
    diagnostic,
)

#: 身份未知时统一用的占位值（协议 §0：未知枚举值降级为 ``unknown``）。
UNKNOWN = "unknown"


@dataclass(frozen=True)
class CardRegistry:
    """卡片注册表：卡片字典 + 卡面版式（物理尺寸注册）+ 采集级/卡片级覆盖。"""

    card_dictionary: CardDictionary
    layouts: dict[str, CardLayout]
    default_layout: str
    layout_version: str = "0.0.1"
    capture_layout: str | None = None
    card_layout_overrides: dict[str, str] = field(default_factory=dict)
    orientation_quadrants: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        *,
        card_dictionary: CardDictionary | None = None,
        capture_layout: str | None = None,
        card_layout_overrides: dict[str, str] | None = None,
        orientation_quadrants: dict[str, int] | None = None,
    ) -> "CardRegistry":
        dictionaries = load_aruco_dictionaries()
        dictionary = card_dictionary or load_card_dictionary(dictionaries)
        layouts, _presets, layout_version = load_layouts()
        registry = cls(
            card_dictionary=dictionary,
            layouts=layouts,
            default_layout=_default_layout_name(),
            layout_version=layout_version,
            capture_layout=capture_layout,
            card_layout_overrides=dict(card_layout_overrides or {}),
            orientation_quadrants=dict(orientation_quadrants or {}),
        )
        registry.validate()
        return registry

    def validate(self) -> None:
        aruco = self.aruco
        self.card_dictionary.validate(aruco)
        if self.default_layout not in self.layouts:
            raise ValueError(f"默认卡面版式 {self.default_layout!r} 不在版式定义里")
        for card_id, layout in self.card_layout_overrides.items():
            if layout not in self.layouts:
                raise ValueError(f"{card_id}: 覆盖的卡面版式 {layout!r} 不在版式定义里")
        for card_id, quadrant in self.orientation_quadrants.items():
            if not 0 <= int(quadrant) <= 3:
                raise ValueError(f"{card_id}: orientation_quadrant 必须是 0..3，实际 {quadrant}")
        if self.capture_layout is not None and self.capture_layout not in self.layouts:
            raise ValueError(
                f"采集版式 {self.capture_layout!r} 不在版式定义里；"
                f"可用：{', '.join(sorted(self.layouts))}"
            )

    @property
    def aruco(self) -> ArucoDictionary:
        dictionaries = load_aruco_dictionaries()
        return dictionaries[self.card_dictionary.aruco_dictionary]

    @property
    def marker_size_mm(self) -> float:
        """采集版式的标记边长（毫米）；未指定采集版式时用默认版式。"""
        return self.layout_for(None).marker_size_mm

    def entry_for_marker(self, marker_id: int) -> CardEntry | None:
        for card in self.card_dictionary.cards:
            if card.marker_id == marker_id:
                return card
        return None

    def layout_for(self, card_id: str | None) -> CardLayout:
        if card_id is not None and card_id in self.card_layout_overrides:
            name = self.card_layout_overrides[card_id]
        elif self.capture_layout is not None:
            name = self.capture_layout
        else:
            name = self.default_layout
        return self.layouts[name]

    def quadrant_for(self, card_id: str) -> int:
        return int(self.orientation_quadrants.get(card_id, 0))

    def with_capture_layout(self, layout: str | None) -> "CardRegistry":
        """换一个采集版式（同一份卡片字典 + 桌面换了卡纸）。"""
        clone = CardRegistry(
            card_dictionary=self.card_dictionary,
            layouts=self.layouts,
            default_layout=self.default_layout,
            layout_version=self.layout_version,
            capture_layout=layout,
            card_layout_overrides=dict(self.card_layout_overrides),
            orientation_quadrants=dict(self.orientation_quadrants),
        )
        clone.validate()
        return clone


def load_card_dictionary_file(path: Path) -> CardDictionary:
    """从外部 JSON 读一份卡片字典（格式与 ``data/card_dictionary.json`` 相同）。

    用于验证「标记在 ArUco 字典内、但没在卡片字典登记 → ``unknown_card``」这条路径：
    换一份少了某张卡的字典即可复现。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    dictionaries = load_aruco_dictionaries()
    cards = tuple(
        CardEntry(
            card_id=str(spec["card_id"]),
            marker_id=int(spec["marker_id"]),
            card_type=str(spec["card_type"]),
            card_face=str(spec["card_face"]),
            label=str(spec.get("label", "")),
        )
        for spec in raw["cards"]
    )
    dictionary = CardDictionary(
        version=str(raw.get("dictionary_version", "0.0.1")),
        aruco_dictionary=str(raw["aruco_dictionary"]),
        cards=cards,
    )
    dictionary.validate(dictionaries[dictionary.aruco_dictionary])
    return dictionary


def _default_layout_name() -> str:
    from ..data import read_json

    raw = read_json("synth_layouts.json")
    name = str(raw.get("default_card_layout", ""))
    if not name:
        raise ValueError("synth_layouts.json 缺少 default_card_layout")
    return name


@dataclass(frozen=True)
class CardCandidate:
    """一张卡片的候选（对应 §3.2 ``cards[]`` 的一项）。"""

    candidate_id: str
    marker_ids: tuple[str, ...]
    card_id: str
    card_type: str
    card_face: str
    orientation_quadrant: int | None
    center_px: Point
    confidence: float
    status: str
    marker_index: int
    card_corners_px: tuple[Point, ...] | None = None
    diagnostics: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "marker_ids": list(self.marker_ids),
            "card_id": self.card_id,
            "card_type": self.card_type,
            "card_face": self.card_face,
        }
        if self.orientation_quadrant is not None:
            entry["orientation_quadrant"] = int(self.orientation_quadrant)
        entry["center_px"] = [int(round(self.center_px[0])), int(round(self.center_px[1]))]
        entry["confidence"] = round(float(self.confidence), 3)
        entry["status"] = self.status
        return entry


@dataclass(frozen=True)
class CardCandidateList:
    """§3.2 ``CardCandidateList`` + 加法式扩展 ``diagnostics``。"""

    frame_id: str
    cards: tuple[CardCandidate, ...]
    diagnostics: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "CardCandidateList",
            "frame_id": self.frame_id,
            "cards": [card.to_dict() for card in self.cards],
            "diagnostics": [dict(entry) for entry in self.diagnostics],
        }


def _rotate(point: Point, quadrant: int) -> Point:
    """在标记平面上把坐标按 ``quadrant`` × 90°（顺时针，y 向下）旋转。"""
    u, v = point
    q = quadrant % 4
    if q == 0:
        return (u, v)
    if q == 1:
        return (-v, u)
    if q == 2:
        return (-u, -v)
    return (v, -u)


def card_geometry(
    layout: CardLayout, quadrant: int = 0
) -> tuple[tuple[Point, Point, Point, Point], Point]:
    """返回（卡片四角在标记坐标系下的毫米坐标, 卡片中心在标记坐标系下的毫米坐标）。

    标记坐标系：原点在标记中心、x 向右、y 向下、单位毫米。``quadrant`` 是卡片相对于标记的
    90° 倍数旋转（协议 §3.2 ``orientation_quadrant``）。
    """
    marker_x, marker_y = layout.marker_center_mm
    card_width, card_height = layout.image_width_mm, layout.image_height_mm
    center: Point = (card_width / 2.0 - marker_x, card_height / 2.0 - marker_y)
    half_w, half_h = card_width / 2.0, card_height / 2.0
    local = (
        (-half_w, -half_h),
        (half_w, -half_h),
        (half_w, half_h),
        (-half_w, half_h),
    )
    corners = tuple(
        (
            center[0] + _rotate(offset, quadrant)[0],
            center[1] + _rotate(offset, quadrant)[1],
        )
        for offset in local
    )
    return corners, center


def _build_identified(
    frame: Frame,
    marker: MarkerCandidate,
    registry: CardRegistry,
    candidate_id: str,
) -> CardCandidate:
    assert marker.marker_id is not None
    entry = registry.entry_for_marker(marker.marker_id)
    layout = registry.layout_for(None if entry is None else entry.card_id)
    quadrant = 0 if entry is None else registry.quadrant_for(entry.card_id)

    card_corners: tuple[Point, ...] | None = None
    center = marker.center_px
    matrix = None
    try:
        matrix = homography_from_marker_frame(list(marker.corners_px), layout.marker_size_mm)
    except ValueError:
        matrix = None
    if matrix is not None:
        mm_corners, mm_center = card_geometry(layout, quadrant)
        card_corners = tuple(apply_homography(matrix, u, v) for u, v in mm_corners)
        center = apply_homography(matrix, mm_center[0], mm_center[1])

    diagnostics: list[dict[str, Any]] = []
    card_clipped = False
    if card_corners is not None:
        card_clipped = any(outside_frame(list(card_corners), frame.width, frame.height))
        if card_clipped:
            diagnostics.append(
                diagnostic(
                    DIAG_CARD_CLIPPED,
                    "warning",
                    f"候选 {candidate_id}：由标记外推的卡片范围越出画面（{frame.width}x{frame.height}）",
                    candidate_ids=[candidate_id],
                )
            )

    if entry is None:
        status = "unknown_card"
        diagnostics.append(
            diagnostic(
                DIAG_UNKNOWN_CARD,
                "warning",
                f"候选 {candidate_id}：标记 {marker.marker_id} 在 ArUco 字典内但未在卡片字典登记",
                candidate_ids=[candidate_id],
            )
        )
        card_id = UNKNOWN
        card_type = UNKNOWN
        card_face = UNKNOWN
    else:
        card_id = entry.card_id
        card_type = entry.card_type
        card_face = entry.card_face
        if card_clipped or marker.touches_border:
            status = "clipped"
        elif marker.decode.match.bit_errors > 0:
            status = "occluded"
        else:
            status = "ok"

    confidence = marker.confidence
    if entry is None:
        confidence = min(confidence, 0.9)  # 身份已知但语义未登记：略降，仍高于「完全未知」
    return CardCandidate(
        candidate_id=candidate_id,
        marker_ids=(str(marker.marker_id),),
        card_id=card_id,
        card_type=card_type,
        card_face=card_face,
        orientation_quadrant=quadrant if entry is not None else None,
        center_px=center,
        confidence=confidence,
        status=status,
        marker_index=marker.index,
        card_corners_px=card_corners,
        diagnostics=tuple(diagnostics),
    )


def _build_unidentified(candidate_id: str, marker: MarkerCandidate) -> CardCandidate:
    """检出形状但身份未定的候选：身份字段降级为 ``unknown``，不输出朝向。"""
    status = "ambiguous" if marker.decode.match.ambiguous else "occluded"
    diagnostics: list[dict[str, Any]] = []
    if status == "ambiguous":
        tied = ", ".join(str(value) for value in marker.decode.match.tied_with)
        diagnostics.append(
            diagnostic(
                "LB-V005",
                "warning",
                f"候选 {candidate_id}：码字在标记 [{tied}] 之间无法区分，按 ambiguous 输出",
                candidate_ids=[candidate_id],
            )
        )
    return CardCandidate(
        candidate_id=candidate_id,
        marker_ids=(),
        card_id=UNKNOWN,
        card_type=UNKNOWN,
        card_face=UNKNOWN,
        orientation_quadrant=None,
        center_px=marker.center_px,
        confidence=marker.confidence,
        status=status,
        marker_index=marker.index,
        card_corners_px=None,
        diagnostics=tuple(diagnostics),
    )


def build_card_candidates(
    frame: Frame,
    markers: MarkerCandidateList,
    registry: CardRegistry,
) -> CardCandidateList:
    """把标记候选映射成卡片候选（保持检测顺序，不排序、不丢弃）。"""
    cards: list[CardCandidate] = []
    diagnostics: list[dict[str, Any]] = list(markers.diagnostics)
    for marker in markers.markers:
        candidate_id = f"c-{len(cards)}"
        if marker.marker_id is None:
            candidate = _build_unidentified(candidate_id, marker)
        else:
            candidate = _build_identified(frame, marker, registry, candidate_id)
        if candidate.status not in CARD_STATUSES:  # pragma: no cover - 内部一致性
            raise AssertionError(f"非法 status：{candidate.status}")
        diagnostics.extend(candidate.diagnostics)
        cards.append(candidate)
    return CardCandidateList(
        frame_id=frame.frame_id,
        cards=tuple(cards),
        diagnostics=tuple(diagnostics),
    )


__all__ = [
    "UNKNOWN",
    "CardCandidate",
    "CardCandidateList",
    "CardRegistry",
    "build_card_candidates",
    "card_geometry",
    "load_card_dictionary_file",
]
