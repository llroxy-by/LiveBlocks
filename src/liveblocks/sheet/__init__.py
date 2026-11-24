"""LB-032 实体卡片规范（草案）与标记页模板生成器（``liveblocks cardsheet``）。

- :mod:`liveblocks.sheet.spec`：版式几何 + **数字溯源**（文档里每个数字的出处 / 待实测项）；
- :mod:`liveblocks.sheet.render`：A4 标记页光栅（卡面 + 裁切线 + 编号 + 朝向标记）→ PNG / PDF；
- :mod:`liveblocks.sheet.manifest`：清单（``CardSheet`` JSON，含批次编号与产物指纹）；
- :mod:`liveblocks.sheet.verify`：把页面重新识别一遍，逐卡核对 id / 朝向 / 位置，并核对确定性；
- :mod:`liveblocks.sheet.stencil`：手绘降级版（没有打印机时按模块格子抄）；
- :mod:`liveblocks.sheet.codes`：``LB-S0xx`` 码表。

规范文本见 ``docs/CARD-SPEC.md``（草案 ``sheet_version`` 0.0.1）。**合成管线通过 ≠ 实体卡通过**：
基材、打印工艺、静区是否够用、可识别距离都属「待实测」，人工步骤见该文档 §11。
"""

from __future__ import annotations

from .codes import CODES, diagnostic, document_gaps
from .manifest import KIND, SCHEMA_VERSION, build_manifest
from .render import SheetImage, render_page, render_sheet, rotate_quarter_turns
from .spec import (
    DEFAULT_BATCH_ID,
    DEFAULT_GUTTER_MM,
    DEFAULT_MARGIN_MM,
    DEFAULT_PIXELS_PER_MM,
    HUMAN_VERIFICATION,
    SHEET_VERSION,
    CardSheetSpec,
    SheetContext,
    SheetError,
    SpecNumber,
    all_numbers,
    spec_numbers,
    unmeasured_items,
)
from .stencil import StencilBlock, format_stencil, parse_stencil_blocks, stencil_blocks
from .verify import SheetVerification, format_report, verify_sheet

__all__ = [
    "CODES",
    "DEFAULT_BATCH_ID",
    "DEFAULT_GUTTER_MM",
    "DEFAULT_MARGIN_MM",
    "DEFAULT_PIXELS_PER_MM",
    "HUMAN_VERIFICATION",
    "KIND",
    "SCHEMA_VERSION",
    "SHEET_VERSION",
    "CardSheetSpec",
    "SheetContext",
    "SheetError",
    "SheetImage",
    "SheetVerification",
    "SpecNumber",
    "StencilBlock",
    "all_numbers",
    "build_manifest",
    "diagnostic",
    "document_gaps",
    "format_report",
    "format_stencil",
    "parse_stencil_blocks",
    "render_page",
    "render_sheet",
    "rotate_quarter_turns",
    "spec_numbers",
    "stencil_blocks",
    "unmeasured_items",
    "verify_sheet",
]
