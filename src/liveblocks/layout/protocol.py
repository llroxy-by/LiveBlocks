"""S3 ``Layout``（`docs/PROTOCOL.md` §4）的数据模型、诊断码与最小校验。

§4 只定义了 ``Layout`` 的字段与示例，没有定义：排序依据怎么留痕、``reading_direction`` 各取值
下 ``rows`` / ``slots`` 的几何含义、``orientation_quadrant`` 在排列层的归一化基准、间距阈值与
重复判定口径。本模块按**加法式**（§10 规则 1）补上这些缺口：

- ``Layout`` 多一个可选 ``basis``（排序依据：卡距估计、阈值、每行的候选 id 与跨度）；
- 诊断码用 §9 的 ``LB-L0xx`` 区间（``LB-L001`` / ``LB-L002`` 沿用 §9 的语义，``LB-L003`` 起因
  本实现新增，见 :data:`DIAG_IDENTITY_UNKNOWN`）。

``validate_layout`` 与 S2 的校验同一口径：只核对 §4 定义过的字段、取值范围与枚举，不做 JSON
Schema 级别的完备校验（完整 schema 工具留给 LB-006 / LB-009）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..vision.protocol import SCHEMA_VERSION, VERSION_PATTERN, validate_diagnostics

#: §4 的 ``reading_direction`` 取值；``ltr_ttb`` 是默认值（左上→右下）。
READING_DIRECTIONS = ("ltr_ttb", "rtl_ttb", "ttb_ltr")

#: §9 已定义的排列层诊断码。
DIAG_SPACING_AMBIGUOUS = "LB-L001"  # 相邻槽位间距低于阈值，排列顺序存疑
DIAG_DUPLICATE_REMOVED = "LB-L002"  # 重复候选被去除（留痕）

#: 本实现新增的排列层诊断码（§9 的 ``LB-L0xx`` 区间，见 ``docs/DECISIONS.md`` D-015）。
DIAG_IDENTITY_UNKNOWN = "LB-L003"  # 候选身份未知，未纳入排列（不静默丢弃）
DIAG_ROW_BOUNDARY = "LB-L004"  # 行分簇边界存疑（两行判成一行 / 一行判成两行）
DIAG_NOT_UPRIGHT = "LB-L005"  # 卡片朝向相对阅读方向非正立

#: 排列层诊断码全集（按产出顺序）。
LAYOUT_CODES = (
    DIAG_IDENTITY_UNKNOWN,
    DIAG_DUPLICATE_REMOVED,
    DIAG_ROW_BOUNDARY,
    DIAG_SPACING_AMBIGUOUS,
    DIAG_NOT_UPRIGHT,
)


@dataclass(frozen=True)
class LayoutSlot:
    """§4 ``rows[].slots[]`` 的一项：一个槽位 + 它来自哪个候选。"""

    slot_index: int
    candidate_id: str
    card_face: str
    orientation_quadrant: int
    x_px: int
    y_px: int
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_index": int(self.slot_index),
            "candidate_id": self.candidate_id,
            "card_face": self.card_face,
            "orientation_quadrant": int(self.orientation_quadrant),
            "x_px": int(self.x_px),
            "y_px": int(self.y_px),
            "confidence": round(float(self.confidence), 3),
        }


@dataclass(frozen=True)
class LayoutRow:
    """§4 ``rows[]`` 的一项：一行（``reading_direction`` 为 ``ttb_ltr`` 时是「一列」）。"""

    row_index: int
    slots: tuple[LayoutSlot, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_index": int(self.row_index),
            "slots": [slot.to_dict() for slot in self.slots],
        }

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(slot.candidate_id for slot in self.slots)


@dataclass(frozen=True)
class Layout:
    """§4 ``Layout``：帧内卡片候选的有序排列 + 诊断 + 排序依据。"""

    frame_id: str
    reading_direction: str
    rows: tuple[LayoutRow, ...]
    diagnostics: tuple[dict[str, Any], ...] = ()
    basis: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "kind": "Layout",
            "frame_id": self.frame_id,
            "reading_direction": self.reading_direction,
            "rows": [row.to_dict() for row in self.rows],
        }
        if self.basis:
            document["basis"] = dict(self.basis)
        document["diagnostics"] = [dict(entry) for entry in self.diagnostics]
        return document

    @property
    def slots(self) -> tuple[LayoutSlot, ...]:
        return tuple(slot for row in self.rows for slot in row.slots)

    @property
    def candidate_order(self) -> tuple[str, ...]:
        """按排列顺序（行 → 槽位）展开的候选 id，是「排序结论可追溯」的机器可读形式。"""
        return tuple(slot.candidate_id for slot in self.slots)

    def codes(self) -> tuple[str, ...]:
        return tuple(str(entry.get("code", "")) for entry in self.diagnostics)

    def diagnostics_with(self, code: str) -> tuple[dict[str, Any], ...]:
        return tuple(entry for entry in self.diagnostics if entry.get("code") == code)


def validate_layout(document: dict[str, Any]) -> None:
    """校验 ``Layout``（§4 + 本实现的加法式 ``basis``）。"""
    if not isinstance(document, dict):
        raise ValueError("Layout 必须是对象")
    if document.get("kind") != "Layout":
        raise ValueError(f"kind 必须是 'Layout'，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    if not isinstance(document.get("frame_id"), str):
        raise ValueError("frame_id 必须是字符串")
    direction = document.get("reading_direction")
    if direction not in READING_DIRECTIONS:
        raise ValueError(
            f"reading_direction 必须是 {sorted(READING_DIRECTIONS)} 之一，实际 {direction!r}"
        )
    rows = document.get("rows")
    if not isinstance(rows, list):
        raise ValueError("rows 必须是数组")

    seen_candidates: set[str] = set()
    for row_position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("rows 的每一项必须是对象")
        row_index = row.get("row_index")
        if not isinstance(row_index, int) or isinstance(row_index, bool):
            raise ValueError(f"row_index 必须是整数，实际 {row_index!r}")
        if row_index != row_position:
            # §4 只给了示例，没说 row_index 必须从 0 连续；但「可追溯」要求它与数组顺序一致。
            raise ValueError(f"row_index 必须按数组顺序从 0 连续：位置 {row_position} 上是 {row_index}")
        slots = row.get("slots")
        if not isinstance(slots, list):
            raise ValueError("rows[].slots 必须是数组")
        for slot_position, slot in enumerate(slots):
            if not isinstance(slot, dict):
                raise ValueError("slots 的每一项必须是对象")
            slot_index = slot.get("slot_index")
            if not isinstance(slot_index, int) or isinstance(slot_index, bool):
                raise ValueError(f"slot_index 必须是整数，实际 {slot_index!r}")
            if slot_index != slot_position:
                raise ValueError(
                    f"slot_index 必须按数组顺序从 0 连续：行 {row_index} 位置 {slot_position} 上是 {slot_index}"
                )
            candidate_id = slot.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise ValueError(f"candidate_id 必须是非空字符串，实际 {candidate_id!r}")
            if candidate_id in seen_candidates:
                raise ValueError(f"candidate_id 在排列里重复出现：{candidate_id}")
            seen_candidates.add(candidate_id)
            if not isinstance(slot.get("card_face"), str):
                raise ValueError("card_face 必须是字符串")
            quadrant = slot.get("orientation_quadrant")
            if not isinstance(quadrant, int) or isinstance(quadrant, bool) or not 0 <= quadrant <= 3:
                raise ValueError(f"orientation_quadrant 必须是 0..3 的整数，实际 {quadrant!r}")
            for key in ("x_px", "y_px"):
                value = slot.get(key)
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{key} 必须是整数像素（§0），实际 {value!r}")
            confidence = slot.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise ValueError(f"confidence 必须是数值，实际 {confidence!r}")
            if not 0.0 <= float(confidence) <= 1.0:
                raise ValueError(f"confidence 必须落在 [0,1]，实际 {confidence!r}")

    validate_diagnostics(document.get("diagnostics", []))

    basis = document.get("basis")
    if basis is not None and not isinstance(basis, dict):
        raise ValueError("basis 必须是对象（本实现的加法式字段）")


__all__ = [
    "DIAG_DUPLICATE_REMOVED",
    "DIAG_IDENTITY_UNKNOWN",
    "DIAG_NOT_UPRIGHT",
    "DIAG_ROW_BOUNDARY",
    "DIAG_SPACING_AMBIGUOUS",
    "LAYOUT_CODES",
    "Layout",
    "LayoutRow",
    "LayoutSlot",
    "READING_DIRECTIONS",
    "SCHEMA_VERSION",
    "validate_layout",
]
