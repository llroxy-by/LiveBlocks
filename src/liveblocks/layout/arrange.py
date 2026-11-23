"""排列还原（LB-004）：``CardCandidateList`` → ``Layout``（`docs/PROTOCOL.md` §4）。

排列是纯几何 + 规则问题，本模块只做 Issue #11 范围内的五件事，顺序固定、每步都留痕：

1. **过滤**：身份未知（``unknown_card`` / ``occluded`` / ``ambiguous``）的候选不参与排列，
   逐个用 ``LB-L003`` 点名（协议 §3.2 要求识别层不丢弃候选，因此「不采用」必须在排列层写清）。
2. **去重**：同一身份、位置几乎重合的候选只保留置信度最高的一个，被移除的用 ``LB-L002`` 留痕
   （§4 明确要求）。
3. **分簇**：沿阅读方向的「换行轴」做一维间隙聚类，得到行。
4. **排序**：行按换行轴坐标（自上而下 / 自左而右）排序，槽位按阅读轴坐标排序
   （``rtl_ttb`` 反向）。槽位带 ``candidate_id``，因此每个排序结论都能追回候选。
5. **朝向归一 + 歧义诊断**：把候选的 ``orientation_quadrant`` 折算成「相对阅读方向的正立方向」，
   非 0 用 ``LB-L005`` 提示；行内相邻槽位间距过小 → ``LB-L001``；行分组边界可疑 → ``LB-L004``。

**尺度从哪来**：§4 与 §3.2 都没给卡片物理尺寸，候选里只有 ``center_px``。因此本实现用
**候选集自身的典型卡距**当尺度：``pitch_px`` = 各候选到最近邻欧氏距离的中位数（去重后再估一次）。
所有阈值都是 ``pitch_px`` 的倍数，可用 ``--pitch-px`` 显式覆盖。这是本实现必须报告的协议缺口
（`docs/DECISIONS.md` D-015）：只有两张卡、或两行贴得比卡距还近时，自适应尺度无法判歧义。

**朝向归一的口径**（§4 只说「具体与阅读方向的关系由 S3 解释」，本实现定义为）：
槽位上的 ``orientation_quadrant`` = ``(候选象限 - 阅读方向的正立象限) mod 4``，0 表示「按当前阅读
方向正立可读」。水平阅读（``ltr_ttb`` / ``rtl_ttb``）的正立象限是 0（图像正立）；
``ttb_ltr``（竖直阅读：自上而下成列、列自左而右）的正立象限是 1，即卡片顺时针转 90° 才是正立。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..vision.protocol import diagnostic
from .protocol import (
    DIAG_DUPLICATE_REMOVED,
    DIAG_IDENTITY_UNKNOWN,
    DIAG_NOT_UPRIGHT,
    DIAG_ROW_BOUNDARY,
    DIAG_SPACING_AMBIGUOUS,
    READING_DIRECTIONS,
    Layout,
    LayoutRow,
    LayoutSlot,
)

#: 身份未知时上游（LB-003）用的占位值。
UNKNOWN = "unknown"

#: 只有这两种状态的候选能进排列：身份与朝向都已知，位置可信。
#: ``unknown_card`` / ``occluded`` / ``ambiguous`` 一律不采用（见 D-015）。
USABLE_STATUSES = frozenset({"ok", "clipped"})


@dataclass(frozen=True)
class Candidate:
    """排列层的输入项（§3.2 ``cards[]`` 的投影，加 ``card_id`` 供去重时辨认身份）。"""

    candidate_id: str
    card_id: str
    card_type: str
    card_face: str
    orientation_quadrant: int | None
    x_px: float
    y_px: float
    confidence: float
    status: str

    @classmethod
    def from_dict(cls, entry: dict[str, Any]) -> "Candidate":
        """从 §3.2 的 ``cards[]`` 一项构造；缺 ``card_id``（LB-003 的加法式字段）时退回 ``card_face``。"""
        center = entry["center_px"]
        face = str(entry["card_face"])
        return cls(
            candidate_id=str(entry["candidate_id"]),
            card_id=str(entry.get("card_id", face)),
            card_type=str(entry.get("card_type", UNKNOWN)),
            card_face=face,
            orientation_quadrant=(
                None if "orientation_quadrant" not in entry else int(entry["orientation_quadrant"])
            ),
            x_px=float(center[0]),
            y_px=float(center[1]),
            confidence=float(entry["confidence"]),
            status=str(entry["status"]),
        )

    @classmethod
    def from_vision(cls, card: Any) -> "Candidate":
        """从 LB-003 的 :class:`liveblocks.vision.CardCandidate` 构造（不读它的内部几何字段）。"""
        return cls(
            candidate_id=str(card.candidate_id),
            card_id=str(card.card_id),
            card_type=str(card.card_type),
            card_face=str(card.card_face),
            orientation_quadrant=(
                None if card.orientation_quadrant is None else int(card.orientation_quadrant)
            ),
            x_px=float(card.center_px[0]),
            y_px=float(card.center_px[1]),
            confidence=float(card.confidence),
            status=str(card.status),
        )

    @property
    def identity(self) -> str:
        """去重时用的身份键：优先 ``card_id``，没有就退回 ``card_face``。"""
        return self.card_id if self.card_id and self.card_id != UNKNOWN else self.card_face

    @property
    def usable(self) -> bool:
        return self.status in USABLE_STATUSES

    @property
    def quadrant(self) -> int:
        return 0 if self.orientation_quadrant is None else int(self.orientation_quadrant) % 4

    @property
    def point(self) -> tuple[float, float]:
        return (self.x_px, self.y_px)


@dataclass(frozen=True)
class ReadingFrame:
    """一种阅读方向在图像坐标里的几何约定（图像 x 向右、y 向下，§0）。"""

    line_axis: int  # 0 = 行与行沿 x 分开（竖排），1 = 沿 y 分开（横排）
    order_axis: int  # 行内槽位沿哪个轴推进
    order_sign: int  # +1 递增 / -1 递减
    upright_quadrant: int  # 卡片「正立」时相对图像正立的 90° 倍数（顺时针）
    description: str


#: ``reading_direction`` → 几何约定。行/列都叫 ``rows``：``ttb_ltr`` 的「一行」在画面上是一列。
READING_FRAMES: dict[str, ReadingFrame] = {
    "ltr_ttb": ReadingFrame(1, 0, +1, 0, "行自上而下，行内自左向右"),
    "rtl_ttb": ReadingFrame(1, 0, -1, 0, "行自上而下，行内自右向左"),
    "ttb_ltr": ReadingFrame(0, 1, +1, 1, "列自左而右，列内自上而下（卡片顺时针转 90° 为正立）"),
}


@dataclass(frozen=True)
class LayoutOptions:
    """排列参数。所有阈值都是 ``pitch_px``（典型卡距）的倍数。"""

    reading_direction: str = "ltr_ttb"
    pitch_px: float | None = None
    duplicate_gap_ratio: float = 0.35
    row_gap_ratio: float = 0.75
    row_span_ratio: float = 1.0
    row_ambiguous_ratio: float = 1.2
    slot_gap_ratio: float = 0.6

    def validate(self) -> None:
        if self.reading_direction not in READING_DIRECTIONS:
            raise ValueError(
                f"reading_direction 必须是 {sorted(READING_DIRECTIONS)} 之一，实际 {self.reading_direction!r}"
            )
        if self.pitch_px is not None and float(self.pitch_px) <= 0:
            raise ValueError(f"pitch_px 必须是正数，实际 {self.pitch_px!r}")
        for name in (
            "duplicate_gap_ratio",
            "row_gap_ratio",
            "row_span_ratio",
            "row_ambiguous_ratio",
            "slot_gap_ratio",
        ):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} 必须是正数，实际 {getattr(self, name)!r}")

    def thresholds(self, pitch: float) -> dict[str, float]:
        """把比例换算成像素阈值（``row_ambiguous`` 相对行切分阈值，其余相对卡距）。"""
        return {
            "duplicate": self.duplicate_gap_ratio * pitch,
            "row_gap": self.row_gap_ratio * pitch,
            "row_span": self.row_span_ratio * pitch,
            "row_ambiguous": self.row_ambiguous_ratio * self.row_gap_ratio * pitch,
            "slot_gap": self.slot_gap_ratio * pitch,
        }


def estimate_pitch(candidates: Sequence[Candidate]) -> float | None:
    """估计典型卡距：各候选到最近邻距离的中位数。少于 2 个候选（或全重合）时返回 ``None``。"""
    points = [candidate.point for candidate in candidates]
    if len(points) < 2:
        return None
    nearest: list[float] = []
    for index, point in enumerate(points):
        nearest.append(
            min(math.dist(point, other) for other_index, other in enumerate(points) if other_index != index)
        )
    median = statistics.median(nearest)
    return float(median) if median > 0 else None


def dedupe(
    candidates: Sequence[Candidate], duplicate_px: float
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """同一身份、圆心距离 ≤ ``duplicate_px`` 的候选只留置信度最高的一个。

    返回（保留下来的候选，按输入顺序；``LB-L002`` 诊断）。只有「身份相同」才会被当成重复：
    同一种卡面在桌面上摆两张是合法的（例如两张「数字 1」），只要它们不重合。
    """
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault(candidate.identity, []).append(candidate)

    kept_ids: set[str] = set()
    removed: dict[str, list[Candidate]] = {}
    for identity, members in groups.items():
        # 置信度高者优先；同置信度时靠「更靠上、更靠左、id 更小」保证确定性。
        ordered = sorted(
            members, key=lambda c: (-c.confidence, c.y_px, c.x_px, c.candidate_id)
        )
        winners: list[Candidate] = []
        for candidate in ordered:
            if any(math.dist(candidate.point, winner.point) <= duplicate_px for winner in winners):
                winner = min(
                    (w for w in winners if math.dist(candidate.point, w.point) <= duplicate_px),
                    key=lambda w: math.dist(candidate.point, w.point),
                )
                removed.setdefault(winner.candidate_id, []).append(candidate)
                continue
            winners.append(candidate)
            kept_ids.add(candidate.candidate_id)

    kept = [candidate for candidate in candidates if candidate.candidate_id in kept_ids]
    diagnostics: list[dict[str, Any]] = []
    for candidate in kept:
        losers = removed.get(candidate.candidate_id)
        if not losers:
            continue
        loser_ids = [loser.candidate_id for loser in losers]
        diagnostics.append(
            diagnostic(
                DIAG_DUPLICATE_REMOVED,
                "info",
                f"候选 {candidate.candidate_id}（{candidate.card_face}）与 {('、'.join(loser_ids))} "
                f"是同一张卡的重复检出（圆心距 ≤ {duplicate_px:.1f}px）：保留置信度最高的 "
                f"{candidate.candidate_id}（{candidate.confidence:.2f}），移除 {('、'.join(loser_ids))}",
                candidate_ids=[candidate.candidate_id, *loser_ids],
            )
        )
    return kept, diagnostics


def _coord(candidate: Candidate, axis: int) -> float:
    return candidate.x_px if axis == 0 else candidate.y_px


def _cluster(candidates: Sequence[Candidate], frame: ReadingFrame, gap_px: float) -> list[list[Candidate]]:
    """沿换行轴做一维间隙聚类；相邻候选的换行轴坐标差大于阈值就起新的一行。"""
    ordered = sorted(
        candidates,
        key=lambda c: (_coord(c, frame.line_axis), _coord(c, frame.order_axis), c.candidate_id),
    )
    groups: list[list[Candidate]] = [[ordered[0]]]
    for candidate in ordered[1:]:
        previous = groups[-1][-1]
        if _coord(candidate, frame.line_axis) - _coord(previous, frame.line_axis) > gap_px:
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    return groups


def _slot_key(frame: ReadingFrame):
    return lambda c: (
        frame.order_sign * _coord(c, frame.order_axis),
        _coord(c, frame.line_axis),
        c.candidate_id,
    )


def _row_boundary_diagnostics(
    groups: Sequence[Sequence[Candidate]],
    frame: ReadingFrame,
    thresholds: dict[str, float],
) -> list[dict[str, Any]]:
    """行分组的两类边界歧义（ARCHITECTURE §3.2 要求显式处理）。"""
    entries: list[dict[str, Any]] = []
    # (a) 相邻两行的间隙接近切分阈值 → 可能是同一行被切开
    for index in range(len(groups) - 1):
        upper = max(groups[index], key=lambda c: _coord(c, frame.line_axis))
        lower = min(groups[index + 1], key=lambda c: _coord(c, frame.line_axis))
        gap = _coord(lower, frame.line_axis) - _coord(upper, frame.line_axis)
        if gap < thresholds["row_ambiguous"]:
            entries.append(
                diagnostic(
                    DIAG_ROW_BOUNDARY,
                    "warning",
                    f"行 {index} 与行 {index + 1} 的间隙 {gap:.1f}px 接近行切分阈值 "
                    f"{thresholds['row_gap']:.1f}px，行分组存疑",
                    candidate_ids=[upper.candidate_id, lower.candidate_id],
                )
            )
    # (b) 行内换行轴跨度超过典型卡距 → 可能把两行并成了一行
    for index, group in enumerate(groups):
        coords = [_coord(c, frame.line_axis) for c in group]
        spread = max(coords) - min(coords)
        if spread > thresholds["row_span"]:
            top = min(group, key=lambda c: _coord(c, frame.line_axis))
            bottom = max(group, key=lambda c: _coord(c, frame.line_axis))
            entries.append(
                diagnostic(
                    DIAG_ROW_BOUNDARY,
                    "warning",
                    f"行 {index} 内换行轴跨度 {spread:.1f}px 超过阈值 {thresholds['row_span']:.1f}px，"
                    f"可能把两行并成了一行",
                    candidate_ids=[top.candidate_id, bottom.candidate_id],
                )
            )
    return entries


def _spacing_diagnostics(
    ordered: Sequence[Candidate],
    row_index: int,
    frame: ReadingFrame,
    limit_px: float | None,
) -> list[dict[str, Any]]:
    """行内相邻槽位间距低于阈值 → ``LB-L001``（§4 示例的语义）。"""
    if limit_px is None:
        return []
    entries: list[dict[str, Any]] = []
    for left, right in zip(ordered, ordered[1:]):
        gap = abs(_coord(right, frame.order_axis) - _coord(left, frame.order_axis))
        if gap < limit_px:
            entries.append(
                diagnostic(
                    DIAG_SPACING_AMBIGUOUS,
                    "warning",
                    f"候选 {left.candidate_id} 与 {right.candidate_id} 间距 {gap:.1f}px 低于阈值 "
                    f"{limit_px:.1f}px（行 {row_index}），排列顺序存疑",
                    candidate_ids=[left.candidate_id, right.candidate_id],
                )
            )
    return entries


def build_layout(
    candidates: Iterable[Candidate],
    *,
    frame_id: str = "",
    options: LayoutOptions | None = None,
) -> Layout:
    """把卡片候选还原成 §4 的 ``Layout``（确定性：同一输入 → 同一输出）。"""
    opts = options or LayoutOptions()
    opts.validate()
    frame = READING_FRAMES[opts.reading_direction]
    items = list(candidates)

    usable = [candidate for candidate in items if candidate.usable]
    excluded = [candidate for candidate in items if not candidate.usable]
    diagnostics: list[dict[str, Any]] = []

    if excluded:
        detail = "、".join(f"{c.candidate_id}（{c.status}）" for c in excluded)
        diagnostics.append(
            diagnostic(
                DIAG_IDENTITY_UNKNOWN,
                "warning",
                f"{len(excluded)} 个候选身份未知，未纳入排列：{detail}；"
                f"上游已按 LB-V001 / LB-V005 报告原因，这里保留候选 id 以便追溯",
                candidate_ids=[c.candidate_id for c in excluded],
            )
        )

    pitch = float(opts.pitch_px) if opts.pitch_px is not None else estimate_pitch(usable)
    removed_by_dedupe = 0
    if pitch is not None and len(usable) > 1:
        before = len(usable)
        kept, dedupe_diagnostics = dedupe(usable, opts.duplicate_gap_ratio * pitch)
        removed_by_dedupe = before - len(kept)
        if dedupe_diagnostics:
            usable = kept
            diagnostics.extend(dedupe_diagnostics)
            if opts.pitch_px is None:
                pitch = estimate_pitch(usable) or pitch

    thresholds = {} if pitch is None else opts.thresholds(pitch)
    row_boundary: list[dict[str, Any]] = []
    spacing: list[dict[str, Any]] = []
    upright: list[dict[str, Any]] = []

    if not usable:
        groups: list[list[Candidate]] = []
    elif pitch is None:
        # 只有一个可用候选：没有尺度就不能判间距/分组歧义，直接单行。
        groups = [list(usable)]
    else:
        groups = _cluster(usable, frame, thresholds["row_gap"])
        row_boundary = _row_boundary_diagnostics(groups, frame, thresholds)

    rows: list[LayoutRow] = []
    basis_rows: list[dict[str, Any]] = []
    for row_index, group in enumerate(groups):
        ordered = sorted(group, key=_slot_key(frame))
        spacing.extend(
            _spacing_diagnostics(ordered, row_index, frame, thresholds.get("slot_gap"))
        )
        slots: list[LayoutSlot] = []
        for slot_index, candidate in enumerate(ordered):
            quadrant = (candidate.quadrant - frame.upright_quadrant) % 4
            if quadrant != 0:
                upright.append(
                    diagnostic(
                        DIAG_NOT_UPRIGHT,
                        "warning",
                        f"候选 {candidate.candidate_id}（{candidate.card_face}）相对阅读方向 "
                        f"{opts.reading_direction} 的朝向为 {quadrant}×90°，非正立，解析层可能无法读取",
                        candidate_ids=[candidate.candidate_id],
                    )
                )
            slots.append(
                LayoutSlot(
                    slot_index=slot_index,
                    candidate_id=candidate.candidate_id,
                    card_face=candidate.card_face,
                    orientation_quadrant=quadrant,
                    x_px=int(round(candidate.x_px)),
                    y_px=int(round(candidate.y_px)),
                    confidence=candidate.confidence,
                )
            )
        rows.append(LayoutRow(row_index=row_index, slots=tuple(slots)))
        basis_rows.append(
            {
                "row_index": row_index,
                "candidate_ids": [slot.candidate_id for slot in slots],
                "line_center_px": round(
                    statistics.fmean(_coord(c, frame.line_axis) for c in ordered), 3
                ),
                "order_span_px": [
                    round(min(_coord(c, frame.order_axis) for c in ordered), 3),
                    round(max(_coord(c, frame.order_axis) for c in ordered), 3),
                ],
            }
        )

    diagnostics.extend(row_boundary)
    diagnostics.extend(spacing)
    diagnostics.extend(upright)

    basis: dict[str, Any] = {
        "pitch_px": None if pitch is None else round(pitch, 3),
        "thresholds_px": {key: round(value, 3) for key, value in thresholds.items()},
        "reading_frame": {
            "line_axis": "x" if frame.line_axis == 0 else "y",
            "order_axis": "x" if frame.order_axis == 0 else "y",
            "order_sign": frame.order_sign,
            "upright_quadrant": frame.upright_quadrant,
            "description": frame.description,
        },
        "counts": {
            "input": len(items),
            "placed": sum(len(row.slots) for row in rows),
            "removed_duplicates": removed_by_dedupe,
            "excluded_unknown_identity": len(excluded),
        },
        "rows": basis_rows,
    }

    return Layout(
        frame_id=frame_id,
        reading_direction=opts.reading_direction,
        rows=tuple(rows),
        diagnostics=tuple(diagnostics),
        basis=basis,
    )


def candidates_from_document(document: dict[str, Any]) -> list[Candidate]:
    """读 §3.2 ``CardCandidateList`` 文档 → 排列层候选（先按 LB-003 的校验器核对字段）。"""
    from ..vision.protocol import validate_card_candidate_list

    validate_card_candidate_list(document)
    return [Candidate.from_dict(entry) for entry in document["cards"]]


def layout_from_document(
    document: dict[str, Any], *, options: LayoutOptions | None = None
) -> Layout:
    """从 §3.2 ``CardCandidateList`` 文档直接得到 ``Layout``（frame_id 沿用输入）。"""
    candidates = candidates_from_document(document)
    return build_layout(candidates, frame_id=str(document.get("frame_id", "")), options=options)


def layout_from_card_list(
    cards: Any, *, options: LayoutOptions | None = None
) -> Layout:
    """从 LB-003 的 :class:`liveblocks.vision.CardCandidateList` 得到 ``Layout``。"""
    return build_layout(
        (Candidate.from_vision(card) for card in cards.cards),
        frame_id=str(cards.frame_id),
        options=options,
    )


__all__ = [
    "Candidate",
    "LayoutOptions",
    "READING_FRAMES",
    "ReadingFrame",
    "UNKNOWN",
    "USABLE_STATUSES",
    "build_layout",
    "candidates_from_document",
    "dedupe",
    "estimate_pitch",
    "layout_from_card_list",
    "layout_from_document",
]
