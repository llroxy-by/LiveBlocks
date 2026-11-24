"""LB-032 卡片规范（草案）：标记页的版式几何与**数字溯源**。

本模块只做两件事，都不画像素：

1. **版式几何**：把「纸张 + 卡面版式 + 页边距 + 卡间距」算成一张 A4 上的卡槽网格
   （几行几列、每张卡占哪块矩形、标记在哪、裁切线画在哪）。所有尺寸都从**卡片字典 /
   ArUco 位表 / 卡面版式定义**推导，不在代码里硬写卡片尺寸（见 :func:`spec_numbers`）。
2. **数字溯源**：``docs/CARD-SPEC.md`` 里的每个数字都必须能追到「字典」或「静区规则」或
   由它们算出的**派生量**；追不到的（基材、打印工艺、实体卡上的可识别距离…）一律标
   ``待实测``（``measured=False``），由 ``tests/test_sheet_docs.py`` 双向核对
   （文档缺数字 / 文档多出无处可追的数字，都会报缺口）。

**静区规则**（本单的唯一定义，见 ``docs/CARD-SPEC.md`` §3）：

    静区下限 = max(卡面版式声明的 marker_quiet_zone_mm, 1 个模块边长)
    1 个模块边长 = marker_size_mm / (marker_modules + 2 × border_bits)

「1 个模块」来自 ArUco 字典（``aruco_dictionaries.json``）；卡面版式声明的静区来自
``synth_layouts.json``（LB-002 的卡面定义，也是 LB-003 识别时用的物理注册信息）。
两者谁大取谁，是「印刷后至少有一圈模块宽的净白」这条规则的最小实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..synth.dictionaries import (
    ArucoDictionary,
    CardDictionary,
    CardEntry,
    load_aruco_dictionaries,
    load_card_dictionary,
)
from ..synth.spec import load_layouts
from ..synth.texture import CardLayout

#: 纸张尺寸（毫米）。A4 是 ISO 216 的**标称**尺寸，不是我们的测量结果。
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
}
DEFAULT_PAPER = "A4"

#: 生成参数（设计选择，非实测）：页边距、卡间距、裁切线宽与被裁对象之间的空隙、朝向三角形边长。
DEFAULT_MARGIN_MM = 8.0
DEFAULT_GUTTER_MM = 6.0
CUT_LINE_MM = 0.2
CUT_GAP_MM = 1.0
ORIENTATION_MARK_MM = 3.0

#: 渲染分辨率（像素/毫米）。4 px/mm ≈ 102 dpi；选它的理由是合成管线的默认自适应窗口
#: 21 px 与 16 px 的模块边长配合（见 ``docs/CARD-SPEC.md`` §8 与 §11 的已知限制），
#: 不是「打印质量」的结论（打印质量 = 待实测）。
DEFAULT_PIXELS_PER_MM = 4.0

#: 批次号缺省值。批次号是**人给**的标签，不由时钟生成——否则「同参数两次字节一致」不成立。
DEFAULT_BATCH_ID = "lb-b0001"

#: 静区规则里的「至少 1 个模块」。
QUIET_ZONE_MODULES = 1

SHEET_VERSION = "0.0.1"


class SheetError(ValueError):
    """标记页规格不成立（带 ``LB-S0xx`` 码，见 :mod:`liveblocks.sheet.codes`）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code} {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SpecNumber:
    """一条「数字 + 它的出处」。

    ``kind`` 取值：``dictionary``（卡片字典 / ArUco 位表）、``layout``（卡面版式定义）、
    ``quiet_zone``（静区规则）、``derived``（由上面三类算出的量）、``paper``（纸张标称尺寸）、
    ``generation``（本单的生成参数，设计选择）、``unmeasured``（无实测依据，待实测）。
    """

    key: str
    label: str
    value: str
    unit: str
    source: str
    kind: str
    measured: bool

    @property
    def value_text(self) -> str:
        return f"{self.value} {self.unit}".strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "kind": self.kind,
            "measured": self.measured,
        }


@dataclass(frozen=True)
class Slot:
    """一个卡槽：页面上的位置（毫米，原点在纸张左上角，x 向右、y 向下）。"""

    index: int
    page: int
    row: int
    col: int
    card: CardEntry


@dataclass(frozen=True)
class CardSheetSpec:
    """一张标记页的完整版式规格。"""

    layout_name: str
    layout: CardLayout
    cards: tuple[CardEntry, ...]
    paper: str
    paper_width_mm: float
    paper_height_mm: float
    margin_mm: float
    gutter_mm: float
    pixels_per_mm: float
    batch_id: str
    aruco: ArucoDictionary
    dictionary: CardDictionary
    cut_line_mm: float = CUT_LINE_MM
    cut_gap_mm: float = CUT_GAP_MM
    orientation_mark_mm: float = ORIENTATION_MARK_MM
    layout_version: str = ""

    # --- 从字典 / 版式推导出来的量 ------------------------------------------------------

    @property
    def module_count(self) -> int:
        """标记的模块总数（含黑边）：``marker_modules + 2 × border_bits``。"""
        return self.aruco.marker_modules + 2 * self.aruco.border_bits

    @property
    def module_mm(self) -> float:
        return self.layout.marker_size_mm / self.module_count

    @property
    def quiet_zone_face_mm(self) -> float:
        """卡面版式在标记四周声明的静区（毫米）。"""
        return self.layout.marker_quiet_zone_mm

    @property
    def quiet_zone_rule_mm(self) -> float:
        """静区规则给出的下限：至少一个模块宽（见模块文档）。"""
        return max(self.quiet_zone_face_mm, QUIET_ZONE_MODULES * self.module_mm)

    @property
    def quiet_zone_gap_mm(self) -> float:
        """卡片之间的净距是否满足静区规则（不足时 ``validate`` 报 ``LB-S001``）。"""
        return self.gutter_mm

    @property
    def columns(self) -> int:
        usable = self.paper_width_mm - 2.0 * self.margin_mm
        return max(0, int((usable + self.gutter_mm) // (self.layout.image_width_mm + self.gutter_mm)))

    @property
    def rows(self) -> int:
        usable = self.paper_height_mm - 2.0 * self.margin_mm
        return max(0, int((usable + self.gutter_mm) // (self.layout.image_height_mm + self.gutter_mm)))

    @property
    def capacity(self) -> int:
        return self.columns * self.rows

    @property
    def page_count(self) -> int:
        if self.capacity <= 0:
            return 0
        return (len(self.cards) + self.capacity - 1) // self.capacity

    @property
    def width_px(self) -> int:
        return int(round(self.paper_width_mm * self.pixels_per_mm))

    @property
    def height_px(self) -> int:
        return int(round(self.paper_height_mm * self.pixels_per_mm))

    # --- 几何 ---------------------------------------------------------------------------

    def slot_rect_mm(self, index: int) -> tuple[float, float, float, float]:
        """第 ``index`` 张卡的卡面矩形 ``(x0, y0, x1, y1)``，单位毫米。"""
        if not 0 <= index < len(self.cards):
            raise IndexError(f"卡槽下标越界：{index}（共 {len(self.cards)} 张）")
        row, col = self._row_col(index)
        x0 = self.margin_mm + col * (self.layout.image_width_mm + self.gutter_mm)
        y0 = self.margin_mm + (row - self._page_of(index) * self.rows) * (
            self.layout.image_height_mm + self.gutter_mm
        )
        return (
            x0,
            y0,
            x0 + self.layout.image_width_mm,
            y0 + self.layout.image_height_mm,
        )

    def marker_rect_mm(self, index: int) -> tuple[float, float, float, float]:
        """第 ``index`` 张卡的标记矩形（不含静区）。"""
        x0, y0, _x1, _y1 = self.slot_rect_mm(index)
        half = self.layout.marker_size_mm / 2.0
        cx = x0 + self.layout.marker_center_mm[0]
        cy = y0 + self.layout.marker_center_mm[1]
        return (cx - half, cy - half, cx + half, cy + half)

    def quiet_zone_rect_mm(self, index: int) -> tuple[float, float, float, float]:
        """标记 + 卡面声明静区的矩形（页面上实际被刷白的范围）。"""
        x0, y0, x1, y1 = self.marker_rect_mm(index)
        pad = self.quiet_zone_face_mm
        return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    def orientation_mark_rect_mm(self, index: int) -> tuple[float, float, float, float]:
        """朝向标记（实心三角形）的外接矩形：卡面左下角内侧。"""
        x0, y0, _x1, y1 = self.slot_rect_mm(index)
        side = self.orientation_mark_mm
        bottom_pad = 2.0
        left_pad = 3.0
        return (x0 + left_pad, y1 - bottom_pad - side, x0 + left_pad + side, y1 - bottom_pad)

    def _page_of(self, index: int) -> int:
        return index // self.capacity if self.capacity else 0

    def _row_col(self, index: int) -> tuple[int, int]:
        within = index % self.capacity if self.capacity else 0
        return divmod(within, self.columns)

    def slots(self) -> tuple[Slot, ...]:
        out: list[Slot] = []
        for index, card in enumerate(self.cards):
            row, col = self._row_col(index)
            out.append(
                Slot(index=index, page=self._page_of(index), row=row, col=col, card=card)
            )
        return tuple(out)

    # --- 校验 ---------------------------------------------------------------------------

    def validate(self) -> None:
        if self.paper not in PAPER_SIZES_MM:
            raise SheetError(
                "LB-S001", f"不支持的纸张 {self.paper!r}；可用：{', '.join(sorted(PAPER_SIZES_MM))}"
            )
        if self.pixels_per_mm <= 0.0:
            raise SheetError("LB-S001", f"pixels_per_mm 必须为正：{self.pixels_per_mm}")
        if float(self.pixels_per_mm) != int(round(self.pixels_per_mm)):
            raise SheetError(
                "LB-S001",
                f"pixels_per_mm 必须是整数（卡面纹理按整数倍分辨率渲染）：{self.pixels_per_mm}",
            )
        if self.margin_mm < 0.0:
            raise SheetError("LB-S001", f"页边距不能为负：{self.margin_mm}")
        if self.gutter_mm < 0.0:
            raise SheetError("LB-S001", f"卡间距不能为负：{self.gutter_mm}")
        if self.cut_line_mm <= 0.0:
            raise SheetError("LB-S001", f"裁切线宽必须为正：{self.cut_line_mm}")
        if self.cut_gap_mm < 0.0:
            raise SheetError("LB-S001", f"裁切线与卡片的间距不能为负：{self.cut_gap_mm}")
        if not self.batch_id.strip():
            raise SheetError("LB-S001", "批次号不能为空")
        if self.module_count <= 0:
            raise SheetError("LB-S002", f"标记模块数必须为正：{self.module_count}")
        if self.layout.marker_size_mm + 2.0 * self.quiet_zone_face_mm > min(
            self.layout.image_width_mm, self.layout.image_height_mm
        ):
            raise SheetError(
                "LB-S002",
                f"标记 {self.layout.marker_size_mm:g} mm + 静区 "
                f"{self.quiet_zone_face_mm:g} mm ×2 放不进 "
                f"{self.layout.image_width_mm:g}×{self.layout.image_height_mm:g} mm 的卡面",
            )
        module_px = self.layout.marker_size_mm * self.pixels_per_mm / self.module_count
        if module_px < 4.0:
            raise SheetError(
                "LB-S001",
                f"每模块只有 {module_px:.2f} 像素（>= 4 才够识别）；"
                f"请提高 pixels_per_mm（当前 {self.pixels_per_mm:g}）",
            )
        if self.gutter_mm + 1e-9 < self.quiet_zone_rule_mm:
            raise SheetError(
                "LB-S001",
                f"卡间距 {self.gutter_mm:g} mm 小于静区规则 {self.quiet_zone_rule_mm:g} mm"
                f"（相邻卡片的墨迹会侵入静区）",
            )
        if self.capacity <= 0:
            raise SheetError(
                "LB-S003",
                f"{self.paper} 纸（{self.paper_width_mm:g}×{self.paper_height_mm:g} mm）上按"
                f"页边距 {self.margin_mm:g} mm、卡间距 {self.gutter_mm:g} mm 连一张卡都放不下",
            )
        if len(self.cards) > self.capacity * max(1, self.page_count):
            raise SheetError("LB-S003", "卡片数量超出可排版的页数")  # pragma: no cover - 防御

    # --- 序列化 -------------------------------------------------------------------------

    def page_dict(self) -> dict[str, Any]:
        return {
            "paper": self.paper,
            "width_mm": round(self.paper_width_mm, 3),
            "height_mm": round(self.paper_height_mm, 3),
            "margin_mm": round(self.margin_mm, 3),
            "gutter_mm": round(self.gutter_mm, 3),
            "columns": self.columns,
            "rows": self.rows,
            "capacity": self.capacity,
            "page_count": self.page_count,
            "pixels_per_mm": round(float(self.pixels_per_mm), 3),
            "width_px": self.width_px,
            "height_px": self.height_px,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sheet_version": SHEET_VERSION,
            "layout": self.layout_name,
            "layout_version": self.layout_version,
            "card_dictionary_version": self.dictionary.version,
            "aruco_dictionary": self.aruco.name,
            "batch_id": self.batch_id,
            "page": self.page_dict(),
            "quiet_zone": {
                "face_mm": round(self.quiet_zone_face_mm, 3),
                "rule_mm": round(self.quiet_zone_rule_mm, 3),
                "module_mm": round(self.module_mm, 3),
                "modules": QUIET_ZONE_MODULES,
                "card_gap_mm": round(self.gutter_mm, 3),
            },
            "marker": {
                "size_mm": round(self.layout.marker_size_mm, 3),
                "modules": self.module_count,
                "data_modules": self.aruco.marker_modules,
                "border_bits": self.aruco.border_bits,
                "module_mm": round(self.module_mm, 3),
            },
            "cut": {
                "line_mm": round(self.cut_line_mm, 3),
                "gap_mm": round(self.cut_gap_mm, 3),
            },
            "orientation_mark_mm": round(self.orientation_mark_mm, 3),
            "cards": [
                {
                    "serial": f"{self.batch_id}-p{slot.page + 1:02d}-{slot.index + 1:02d}",
                    "slot": slot.index,
                    "page": slot.page + 1,
                    "row": slot.row,
                    "col": slot.col,
                    "card_id": slot.card.card_id,
                    "marker_id": slot.card.marker_id,
                    "card_type": slot.card.card_type,
                    "card_face": slot.card.card_face,
                    "rect_mm": [round(value, 3) for value in self.slot_rect_mm(slot.index)],
                    "marker_rect_mm": [round(value, 3) for value in self.marker_rect_mm(slot.index)],
                    "orientation_quadrant": 0,
                }
                for slot in self.slots()
            ],
        }

    # --- 构造 ---------------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        layout_name: str | None = None,
        paper: str = DEFAULT_PAPER,
        margin_mm: float = DEFAULT_MARGIN_MM,
        gutter_mm: float = DEFAULT_GUTTER_MM,
        pixels_per_mm: float = DEFAULT_PIXELS_PER_MM,
        batch_id: str = DEFAULT_BATCH_ID,
        card_ids: list[str] | None = None,
        cut_line_mm: float = CUT_LINE_MM,
        cut_gap_mm: float = CUT_GAP_MM,
        orientation_mark_mm: float = ORIENTATION_MARK_MM,
        context: "SheetContext | None" = None,
    ) -> "CardSheetSpec":
        """按卡片字典 + 卡面版式构造一张标记页的规格（并校验）。"""
        ctx = context or SheetContext.load()
        layouts = ctx.layouts
        name = layout_name or ctx.default_layout
        if name not in layouts:
            raise SheetError(
                "LB-S001",
                f"没有卡面版式 {name!r}；可用：{', '.join(sorted(layouts))}",
            )
        layout = layouts[name]
        if card_ids:
            by_id = ctx.dictionary.by_id
            unknown = [card_id for card_id in card_ids if card_id not in by_id]
            if unknown:
                raise SheetError(
                    "LB-S001",
                    f"卡片字典里没有 {', '.join(sorted(unknown))}；"
                    f"可用：{', '.join(sorted(by_id))}",
                )
            cards = tuple(by_id[card_id] for card_id in card_ids)
        else:
            cards = ctx.dictionary.cards
        width_mm, height_mm = PAPER_SIZES_MM.get(paper, PAPER_SIZES_MM[DEFAULT_PAPER])
        spec = cls(
            layout_name=name,
            layout=layout,
            cards=cards,
            paper=paper,
            paper_width_mm=width_mm,
            paper_height_mm=height_mm,
            margin_mm=margin_mm,
            gutter_mm=gutter_mm,
            pixels_per_mm=float(pixels_per_mm),
            batch_id=batch_id,
            aruco=ctx.aruco,
            dictionary=ctx.dictionary,
            cut_line_mm=cut_line_mm,
            cut_gap_mm=cut_gap_mm,
            orientation_mark_mm=orientation_mark_mm,
            layout_version=ctx.layout_version,
        )
        spec.validate()
        return spec


@dataclass(frozen=True)
class SheetContext:
    """标记页需要的全部外部定义（数据文件解析后的不可变视图）。"""

    layouts: dict[str, CardLayout]
    default_layout: str
    aruco_dictionaries: dict[str, ArucoDictionary]
    dictionary: CardDictionary
    layout_version: str

    @classmethod
    def load(cls) -> "SheetContext":
        layouts, _presets, layout_version = load_layouts()
        aruco_dictionaries = load_aruco_dictionaries()
        dictionary = load_card_dictionary(aruco_dictionaries)
        from ..data import read_json

        default_layout = str(read_json("synth_layouts.json")["default_card_layout"])
        return cls(
            layouts=layouts,
            default_layout=default_layout,
            aruco_dictionaries=aruco_dictionaries,
            dictionary=dictionary,
            layout_version=layout_version,
        )

    @property
    def aruco(self) -> ArucoDictionary:
        return self.aruco_dictionaries[self.dictionary.aruco_dictionary]


def spec_numbers(spec: CardSheetSpec, *, layouts: dict[str, CardLayout] | None = None) -> tuple[SpecNumber, ...]:
    """**可溯源**的数字：每一项都能追到字典、位表、卡面版式、静区规则，或它们的算术派生。

    ``layouts`` 给出时列出这些卡面版式（文档要写明每一种）；``None`` 时只列本页用的那一种。
    """
    aruco = spec.aruco
    dictionary = spec.dictionary
    numbers: list[SpecNumber] = []

    numbers.append(
        SpecNumber(
            key="card_count",
            label="卡片数量",
            value=str(len(dictionary.cards)),
            unit="张",
            source="字典 card_dictionary.json#cards",
            kind="dictionary",
            measured=True,
        )
    )
    numbers.append(
        SpecNumber(
            key="aruco_dictionary",
            label="ArUco 字典",
            value=aruco.name,
            unit="",
            source="字典 card_dictionary.json#aruco_dictionary + aruco_dictionaries.json#dictionaries[].name",
            kind="dictionary",
            measured=True,
        )
    )
    numbers.append(
        SpecNumber(
            key="data_modules",
            label="标记数据模块",
            value=f"{aruco.marker_modules}×{aruco.marker_modules}",
            unit="模块",
            source=f"位表 aruco_dictionaries.json#{aruco.name}.marker_modules",
            kind="dictionary",
            measured=True,
        )
    )
    numbers.append(
        SpecNumber(
            key="border_bits",
            label="黑边宽度",
            value=str(aruco.border_bits),
            unit="模块",
            source=f"位表 aruco_dictionaries.json#{aruco.name}.border_bits",
            kind="dictionary",
            measured=True,
        )
    )
    numbers.append(
        SpecNumber(
            key="module_count",
            label="标记模块总数",
            value=f"{spec.module_count}×{spec.module_count}",
            unit="模块",
            source=f"派生：{aruco.marker_modules} + 2×{aruco.border_bits}",
            kind="derived",
            measured=True,
        )
    )
    numbers.append(
        SpecNumber(
            key="marker_id_range",
            label="标记编号范围",
            value=f"0..{aruco.max_marker_id}",
            unit="",
            source=f"位表 aruco_dictionaries.json#{aruco.name}.codes（{len(aruco.codes)} 个码字）",
            kind="dictionary",
            measured=True,
        )
    )

    names = list(layouts) if layouts else [spec.layout_name]
    for name in names:
        layout = layouts[name] if layouts else spec.layout
        module_mm = layout.marker_size_mm / (aruco.marker_modules + 2 * aruco.border_bits)
        rule = max(layout.marker_quiet_zone_mm, QUIET_ZONE_MODULES * module_mm)
        numbers.extend(
            [
                SpecNumber(
                    key=f"card_size.{name}",
                    label=f"卡面尺寸（{name}）",
                    value=f"{layout.image_width_mm:g}×{layout.image_height_mm:g}",
                    unit="mm",
                    source=f"版式 synth_layouts.json#card_layouts.{name}.image_width_mm/image_height_mm",
                    kind="layout",
                    measured=True,
                ),
                SpecNumber(
                    key=f"marker_size.{name}",
                    label=f"标记边长（{name}）",
                    value=f"{layout.marker_size_mm:g}",
                    unit="mm",
                    source=f"版式 synth_layouts.json#card_layouts.{name}.marker_size_mm",
                    kind="layout",
                    measured=True,
                ),
                SpecNumber(
                    key=f"quiet_zone_face.{name}",
                    label=f"卡面声明静区（{name}）",
                    value=f"{layout.marker_quiet_zone_mm:g}",
                    unit="mm",
                    source=f"版式 synth_layouts.json#card_layouts.{name}.marker_quiet_zone_mm",
                    kind="layout",
                    measured=True,
                ),
                SpecNumber(
                    key=f"module_mm.{name}",
                    label=f"标记模块边长（{name}）",
                    value=f"{round(module_mm, 3):g}",
                    unit="mm",
                    source=(
                        f"派生：标记边长 {layout.marker_size_mm:g} mm ÷ 模块总数 "
                        f"{aruco.marker_modules + 2 * aruco.border_bits}"
                    ),
                    kind="derived",
                    measured=True,
                ),
                SpecNumber(
                    key=f"quiet_zone_rule.{name}",
                    label=f"静区规则下限（{name}）",
                    value=f"{round(rule, 3):g}",
                    unit="mm",
                    source=(
                        f"静区规则：max(卡面声明 {layout.marker_quiet_zone_mm:g} mm, "
                        f"{QUIET_ZONE_MODULES} 个模块 {round(module_mm, 3):g} mm)"
                    ),
                    kind="quiet_zone",
                    measured=True,
                ),
            ]
        )

    page = spec.page_dict()
    numbers.extend(
        [
            SpecNumber(
                key="paper_size",
                label="纸张尺寸（A4）",
                value=f"{spec.paper_width_mm:g}×{spec.paper_height_mm:g}",
                unit="mm",
                source="ISO 216 A4 标称尺寸（纸张标准；打印出来的实际尺寸偏差见待实测项）",
                kind="paper",
                measured=True,
            ),
            SpecNumber(
                key="columns",
                label="每行卡片数",
                value=str(page["columns"]),
                unit="张",
                source=(
                    f"派生：⌊(纸张宽 {spec.paper_width_mm:g} − 2×页边距 {spec.margin_mm:g} + 卡间距 "
                    f"{spec.gutter_mm:g}) ÷ (卡面宽 {spec.layout.image_width_mm:g} + 卡间距 {spec.gutter_mm:g})⌋"
                ),
                kind="derived",
                measured=True,
            ),
            SpecNumber(
                key="rows",
                label="每列卡片数",
                value=str(page["rows"]),
                unit="张",
                source=(
                    f"派生：⌊(纸张高 {spec.paper_height_mm:g} − 2×页边距 {spec.margin_mm:g} + 卡间距 "
                    f"{spec.gutter_mm:g}) ÷ (卡面高 {spec.layout.image_height_mm:g} + 卡间距 {spec.gutter_mm:g})⌋"
                ),
                kind="derived",
                measured=True,
            ),
            SpecNumber(
                key="capacity",
                label="单页容量",
                value=str(page["capacity"]),
                unit="张",
                source=f"派生：每行 {page['columns']} × 每列 {page['rows']}",
                kind="derived",
                measured=True,
            ),
            SpecNumber(
                key="page_count",
                label="页数",
                value=str(page["page_count"]),
                unit="页",
                source=f"派生：⌈卡片数量 {len(spec.cards)} ÷ 单页容量 {page['capacity']}⌉",
                kind="derived",
                measured=True,
            ),
        ]
    )
    return tuple(numbers)


def generation_numbers(spec: CardSheetSpec) -> tuple[SpecNumber, ...]:
    """标记页的**排版参数**：本单自定的设计默认值，**没有实测依据**（文档里标「待实测」）。

    它们不是对世界的声明，而是「这一页按什么参数生成」。对应的实体问题（可打印边距多少、
    细线能否打印、这个分辨率打印出来够不够清楚）都要打印之后才谈得上结论，
    见 :func:`unmeasured_items` 与 ``docs/CARD-SPEC.md`` §11。
    """
    return (
        SpecNumber(
            key="margin",
            label="页边距",
            value=f"{spec.margin_mm:g}",
            unit="mm",
            source="设计默认值 liveblocks.sheet.spec.DEFAULT_MARGIN_MM（与打印机的不可打印边距无关）",
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="gutter",
            label="卡间距",
            value=f"{spec.gutter_mm:g}",
            unit="mm",
            source=(
                "设计默认值 liveblocks.sheet.spec.DEFAULT_GUTTER_MM；"
                f"约束：必须不小于静区规则 {spec.quiet_zone_rule_mm:g} mm（校验里强制）"
            ),
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="pixels_per_mm",
            label="渲染分辨率",
            value=f"{spec.pixels_per_mm:g}",
            unit="px/mm",
            source=(
                "设计默认值 liveblocks.sheet.spec.DEFAULT_PIXELS_PER_MM（≈ 102 dpi）；"
                "与 LB-003 默认自适应窗口 21 px、模块 16 px 配合。不是打印质量的结论"
            ),
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="cut_line",
            label="裁切线宽",
            value=f"{spec.cut_line_mm:g}",
            unit="mm",
            source="设计默认值 liveblocks.sheet.spec.CUT_LINE_MM",
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="cut_gap",
            label="裁切线与卡片间距",
            value=f"{spec.cut_gap_mm:g}",
            unit="mm",
            source="设计默认值 liveblocks.sheet.spec.CUT_GAP_MM",
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="orientation_mark",
            label="朝向标记边长",
            value=f"{spec.orientation_mark_mm:g}",
            unit="mm",
            source="设计默认值 liveblocks.sheet.spec.ORIENTATION_MARK_MM",
            kind="generation",
            measured=False,
        ),
        SpecNumber(
            key="batch_id",
            label="批次号缺省值",
            value=spec.batch_id,
            unit="",
            source="设计默认值 liveblocks.sheet.spec.DEFAULT_BATCH_ID（由人给，不由时钟生成）",
            kind="generation",
            measured=False,
        ),
    )


def unmeasured_items() -> tuple[SpecNumber, ...]:
    """**没有实测依据**的规范项：文档里必须逐条标「待实测」，代理不得代填数值。"""
    return (
        SpecNumber(
            key="substrate",
            label="基材（纸 / 卡纸 / PVC / 亚克力）",
            value="—",
            unit="",
            source="无实测依据：不同基材的厚度、白度、不透明度与翘曲都会影响打印与识别",
            kind="unmeasured",
            measured=False,
        ),
        SpecNumber(
            key="print_process",
            label="打印工艺（激光 / 喷墨 / 热转印 / 外协印刷）",
            value="—",
            unit="",
            source="无实测依据：本单没有打印机、没有出样、没有测过最小可打印模块",
            kind="unmeasured",
            measured=False,
        ),
        SpecNumber(
            key="printed_module_error",
            label="打印后模块边长误差",
            value="—",
            unit="mm",
            source="无实测依据：需要打印后用卡尺/放大镜量（步骤见 §11）",
            kind="unmeasured",
            measured=False,
        ),
        SpecNumber(
            key="face_quiet_zone_enough",
            label="卡面 2.5 mm 静区在实体卡上是否够用",
            value="—",
            unit="",
            source=(
                "合成图上够用（本单 verify 有证据），但是否够用是打印+拍摄的问题，"
                "只能实测（§11）"
            ),
            kind="unmeasured",
            measured=False,
        ),
        SpecNumber(
            key="recognisable_distance",
            label="可识别距离",
            value="—",
            unit="mm",
            source="无实测依据：合成管线没有景深、运动模糊与真实光照",
            kind="unmeasured",
            measured=False,
        ),
        SpecNumber(
            key="paper_scale_error",
            label="打印缩放误差（100% 比例是否准）",
            value="—",
            unit="mm",
            source="无实测依据：不同打印机的「实际尺寸 / 适应页面」选项会缩放页面",
            kind="unmeasured",
            measured=False,
        ),
    )


#: **待人工验证**的条目（``(条目, 人怎么验)``）。代理不得代替人填结论，也不得把相关项
#: 从「待硬件验证」移到「已验证」；文档 §11 与清单里的 ``human_verification`` 都引这一份。
HUMAN_VERIFICATION: tuple[tuple[str, str], ...] = (
    (
        "实体卡打印尺寸（是否 100% 原尺寸）",
        "① 生成：`liveblocks cardsheet render --out sheet.png --pdf sheet.pdf`；"
        "② 打印时关掉「适应页面 / Fit to page」，按 100%（实际尺寸）出纸；"
        "③ 卡尺量任意 3 张卡的长边与短边（卡面版式 card-60x40 标称 60×40 mm）与任一标记边长"
        "（标称 24 mm），把实测值连打印机型号一起记进 Issue；"
        "④ 偏差超过 1 mm 先查打印缩放，**不要**回头改标称值。",
    ),
    (
        "打印后模块边长与边缘质量",
        "① 打印同一张页；② 用放大镜或手机微距看任一标记的 6×6 模块：黑边是否连贯、最小模块"
        "有没有断线或糊边；③ 卡尺量 6 个模块的总长（标称 24 mm）并记实测；"
        "④ 结论写进 `docs/PROGRESS.md` 的「待硬件验证」，不要写成「已验证」。",
    ),
    (
        "卡面 2.5 mm 静区在实体卡上是否够用",
        "卡面声明静区 2.5 mm **小于**静区规则 4 mm（1 个模块）。① 把打印好的卡紧贴另一张卡、"
        "再紧贴深色桌面/深色物件摆放；② 用 `liveblocks serve-cam serve` 抓 10 帧以上，"
        "看 `liveblocks recognize detect` 是否仍给出正确 `marker_id`；③ 若漏检，把「静区不足」"
        "记进 Issue，再决定是否提高版式的 `marker_quiet_zone_mm`（改版式要重生成 fixtures）。",
    ),
    (
        "基材与打印工艺",
        "① 选一种基材（80 g 纸 / 200 g 卡纸 / PVC）与一种工艺（激光 / 喷墨 / 外协）；"
        "② 按上一条打印并测量；③ 记录翘曲、反面透印、墨粉反光对识别的影响；"
        "④ 只有实测记录才能把「基材」「打印工艺」两项从「待实测」移出。",
    ),
    (
        "可识别距离与角度",
        "① 实体卡平放桌面，用 `liveblocks serve-cam serve` 在 0.3 / 0.5 / 0.8 m 与 0° / 15° / 30° "
        "倾角各抓 10 帧；② 记检出率与漏检帧；③ 结论必须带设备型号、分辨率、距离、帧数，"
        "缺一项就不算实测。",
    ),
    (
        "裁切线是否实用",
        "① 按灰线手剪一张卡；② 量剪出来的卡与标称尺寸的偏差；③ 若裁切线压在卡面墨迹上或离得"
        "太远，记下实际手剪偏差，再决定是否调整 `CUT_GAP_MM`（属下一轮的设计改动，不在本单）。",
    ),
)


def all_numbers(spec: CardSheetSpec, *, layouts: dict[str, CardLayout] | None = None) -> tuple[SpecNumber, ...]:
    """可溯源数字 + 排版参数 + 待实测项，供文档核对与 ``liveblocks cardsheet spec`` 打印。

    ``layouts`` 为 ``None`` 时列出 ``synth_layouts.json`` 里的**全部**卡面版式（文档要一并写明）。
    """
    if layouts is None:
        layouts = SheetContext.load().layouts
    return spec_numbers(spec, layouts=layouts) + generation_numbers(spec) + unmeasured_items()


__all__ = [
    "CUT_GAP_MM",
    "HUMAN_VERIFICATION",
    "CUT_LINE_MM",
    "DEFAULT_BATCH_ID",
    "DEFAULT_GUTTER_MM",
    "DEFAULT_MARGIN_MM",
    "DEFAULT_PAPER",
    "DEFAULT_PIXELS_PER_MM",
    "ORIENTATION_MARK_MM",
    "PAPER_SIZES_MM",
    "QUIET_ZONE_MODULES",
    "SHEET_VERSION",
    "CardSheetSpec",
    "SheetContext",
    "SheetError",
    "Slot",
    "SpecNumber",
    "all_numbers",
    "generation_numbers",
    "spec_numbers",
    "unmeasured_items",
]
