"""排列层（LB-004）：卡片候选 → ``Layout``（`docs/PROTOCOL.md` §4）。

这一层只做「卡片候选的有序排列 + 朝向归一 + 去重 + 歧义诊断」，**不解析语法**（LB-005）：

- :func:`liveblocks.layout.build_layout` —— 核心：分簇、排序、朝向归一、去重、诊断；
- :func:`liveblocks.layout.layout_from_document` / :func:`liveblocks.layout.layout_from_card_list` ——
  两个现成入口（§3.2 JSON 文档 / LB-003 的 ``CardCandidateList`` 对象）；
- :class:`liveblocks.layout.LayoutOptions` —— 阅读方向与阈值（都是典型卡距的倍数）；
- :func:`liveblocks.layout.validate_layout` —— §4 的最小校验器（与 S2 校验同一口径）。

协议缺口的取舍写在模块 docstring 与 ``docs/DECISIONS.md`` D-015；协议文件本身未被改动
（由仓库所有者维护）。
"""

from __future__ import annotations

from .arrange import (
    Candidate,
    LayoutOptions,
    READING_FRAMES,
    ReadingFrame,
    USABLE_STATUSES,
    build_layout,
    candidates_from_document,
    dedupe,
    estimate_pitch,
    layout_from_card_list,
    layout_from_document,
)
from .protocol import (
    DIAG_DUPLICATE_REMOVED,
    DIAG_IDENTITY_UNKNOWN,
    DIAG_NOT_UPRIGHT,
    DIAG_ROW_BOUNDARY,
    DIAG_SPACING_AMBIGUOUS,
    LAYOUT_CODES,
    READING_DIRECTIONS,
    SCHEMA_VERSION,
    Layout,
    LayoutRow,
    LayoutSlot,
    validate_layout,
)
from .report import FixtureLayout, LayoutReport, build_report, load_fixture

__all__ = [
    "Candidate",
    "DIAG_DUPLICATE_REMOVED",
    "DIAG_IDENTITY_UNKNOWN",
    "DIAG_NOT_UPRIGHT",
    "DIAG_ROW_BOUNDARY",
    "DIAG_SPACING_AMBIGUOUS",
    "FixtureLayout",
    "LAYOUT_CODES",
    "Layout",
    "LayoutOptions",
    "LayoutReport",
    "LayoutRow",
    "LayoutSlot",
    "READING_DIRECTIONS",
    "READING_FRAMES",
    "ReadingFrame",
    "SCHEMA_VERSION",
    "USABLE_STATUSES",
    "build_layout",
    "build_report",
    "candidates_from_document",
    "dedupe",
    "estimate_pitch",
    "layout_from_card_list",
    "layout_from_document",
    "load_fixture",
    "validate_layout",
]
