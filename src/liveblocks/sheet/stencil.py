"""手绘降级版：没有打印机时，把标记按模块格子抄到卡纸上的**文字版说明**。

`liveblocks cardsheet stencil` 给每张卡印一张 8×8 的字符网格：

- 外圈 1 格 = 静区（留白，不画）；
- 内圈 6×6 格 = 标记本身：外圈 1 格是**黑边**（要涂黑），中间 4×4 格是 ArUco 数据位
  （``#`` 涂黑、``.`` 不涂）；
- 每个格的边长 = 标记边长 ÷ 6 = 4 mm（``card-60x40`` 版式），于是「一格 4 mm 的方格纸」
  就能照着抄；抄完卡片上边朝上，与打印版的朝向 0° 一致。

网格是**从位表直接生成**的，因此手抄版的每个 ``#``/``.`` 都能追到
``src/liveblocks/data/aruco_dictionaries.json`` 的码字：``parse_stencil`` 能从文本还原
6×6 位串，``tests/test_sheet_stencil.py`` 用它逐卡与位表比对（手绘说明不是摆设）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..synth.dictionaries import ArucoDictionary
from .spec import CardSheetSpec

#: 每格用两个字符，画出来才接近方格（一个字符偏窄）。
CELL_BLACK = "##"
CELL_WHITE = ".."
CELL_QUIET = "  "
CELL_CHARS = 2

SYMBOL_BLACK = "#"
SYMBOL_WHITE = "."

#: 手绘说明里每张卡的抬头（``C01（marker_id=0，card_face=move-forward）``）。
_HEADER = re.compile(r"^([0-9A-Za-z_]+)（marker_id=\d+")


@dataclass(frozen=True)
class StencilBlock:
    """一张卡的手绘网格：``rows`` 是 8×8 个格符号（``#`` / ``.`` / ``空格``）。"""

    card_id: str
    marker_id: int
    card_face: str
    rows: tuple[str, ...]
    module_mm: float
    quiet_zone_mm: float

    def text(self) -> str:
        width = len(self.rows[0]) * CELL_CHARS
        lines = [
            f"{self.card_id}（marker_id={self.marker_id}，card_face={self.card_face}）",
            f"  每格 {self.module_mm:g} mm，6×6 格；四周再留 {self.quiet_zone_mm:g} mm 静区不画",
            "  # = 涂黑，. = 不涂，空 = 静区（留白）",
            "  +" + "-" * width + "+",
        ]
        for row in self.rows:
            cells = "".join(_cell_text(value) for value in row)
            lines.append(f"  |{cells}|")
        lines.append("  +" + "-" * width + "+")
        lines.append("  卡片上边朝上 = 朝向 0°；抄完沿 6 mm 的方格外沿剪下")
        return "\n".join(lines) + "\n"


def _cell_text(value: str) -> str:
    if value == SYMBOL_BLACK:
        return CELL_BLACK
    if value == SYMBOL_WHITE:
        return CELL_WHITE
    return CELL_QUIET


def marker_code(aruco: ArucoDictionary, marker_id: int) -> str:
    """标记的 6×6 位串（含 1 模块黑边；``"1"`` = 不涂），手绘网格的机器可比对形式。"""
    modules = aruco.marker_modules + 2 * aruco.border_bits
    bits = aruco.matrix(marker_id)
    rows: list[str] = []
    for row in range(modules):
        line: list[str] = []
        for col in range(modules):
            is_border = (
                row < aruco.border_bits
                or col < aruco.border_bits
                or row >= modules - aruco.border_bits
                or col >= modules - aruco.border_bits
            )
            if is_border:
                line.append("0")
                continue
            line.append(
                "1"
                if bits[row - aruco.border_bits][col - aruco.border_bits] == "1"
                else "0"
            )
        rows.append("".join(line))
    return "".join(rows)


def grid_for_marker(aruco: ArucoDictionary, marker_id: int) -> tuple[str, ...]:
    """标记的 8×8 字符网格（含 1 格静区、1 格黑边）。"""
    modules = aruco.marker_modules + 2 * aruco.border_bits
    size = modules + 2
    rows: list[str] = []
    for row in range(size):
        line: list[str] = []
        for col in range(size):
            inner_row, inner_col = row - 1, col - 1
            if not (0 <= inner_row < modules and 0 <= inner_col < modules):
                line.append(" ")
                continue
            is_border = (
                inner_row < aruco.border_bits
                or inner_col < aruco.border_bits
                or inner_row >= modules - aruco.border_bits
                or inner_col >= modules - aruco.border_bits
            )
            if is_border:
                line.append(SYMBOL_BLACK)
                continue
            bit = aruco.matrix(marker_id)[inner_row - aruco.border_bits][inner_col - aruco.border_bits]
            line.append(SYMBOL_WHITE if bit == "1" else SYMBOL_BLACK)
        rows.append("".join(line))
    return tuple(rows)


def stencil_blocks(spec: CardSheetSpec, card_ids: list[str] | None = None) -> tuple[StencilBlock, ...]:
    """按卡片字典的顺序给每张卡生成一张手绘网格。"""
    wanted = set(card_ids) if card_ids else None
    blocks: list[StencilBlock] = []
    for card in spec.cards:
        if wanted is not None and card.card_id not in wanted:
            continue
        blocks.append(
            StencilBlock(
                card_id=card.card_id,
                marker_id=card.marker_id,
                card_face=card.card_face,
                rows=grid_for_marker(spec.aruco, card.marker_id),
                module_mm=spec.module_mm,
                quiet_zone_mm=spec.quiet_zone_rule_mm,
            )
        )
    return tuple(blocks)


def format_stencil(spec: CardSheetSpec, card_ids: list[str] | None = None) -> str:
    """把全部卡片的手绘网格拼成一份说明。"""
    blocks = stencil_blocks(spec, card_ids)
    header = (
        f"LB-032 手绘降级版（layout {spec.layout_name}，批次 {spec.batch_id}，"
        f"{len(blocks)} 张卡）\n"
        "没有打印机时用它：按下面的网格把黑格涂满，静区留白，沿外沿剪下即可。\n"
        "  —— 抄错一个格就会变成别的码字（或读不出来），抄完请用 "
        "`liveblocks recognize detect --image <拍的照片>` 复核。\n"
    )
    body = "\n".join(block.text() for block in blocks)
    return header + "\n" + body


def parse_stencil_blocks(text: str) -> dict[str, str]:
    """把一份手绘说明按卡片拆开，返回 ``{card_id: 6×6 位串}``。"""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        header = _HEADER.match(stripped)
        if header:
            current = header.group(1)
            blocks[current] = []
            continue
        if current is not None and stripped.startswith("|"):
            blocks[current].append(line)
    return {
        card_id: parse_stencil("\n".join(lines))
        for card_id, lines in blocks.items()
        if lines
    }


def parse_stencil(text: str) -> str:
    """从一张手绘网格里还原 6×6 位串（含黑边，``"1"`` = 不涂 = 白模块）。

    与 :func:`marker_code` 的输出可直接逐字符比对。
    """
    rows: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = stripped[1:-1]
        if len(cells) % CELL_CHARS:
            raise ValueError(f"网格行宽度不对：{stripped!r}")
        values: list[str] = []
        for index in range(0, len(cells), CELL_CHARS):
            cell = cells[index : index + CELL_CHARS]
            if cell == CELL_BLACK:
                values.append(SYMBOL_BLACK)
            elif cell == CELL_WHITE:
                values.append(SYMBOL_WHITE)
            elif cell == CELL_QUIET:
                values.append(" ")
            else:
                raise ValueError(f"无法识别的格子 {cell!r}")
        rows.append("".join(values))
    if len(rows) != 8 or any(len(row) != 8 for row in rows):
        raise ValueError(f"网格必须是 8×8（含 1 格静区），实际 {len(rows)} 行")
    code: list[str] = []
    for row in rows[1:7]:
        for value in row[1:7]:
            code.append("1" if value == SYMBOL_WHITE else "0")
    return "".join(code)


__all__ = [
    "StencilBlock",
    "format_stencil",
    "grid_for_marker",
    "marker_code",
    "parse_stencil",
    "parse_stencil_blocks",
    "stencil_blocks",
]
