"""S7b ``ui.debug``：把一份 §8 运行记录渲染成一屏分层调试视图（只读）。

模块划分：

- :mod:`liveblocks.ui.protocol` —— 层名、面板标题、``LB-U0xx`` 问题码、最小字段校验；
- :mod:`liveblocks.ui.record` —— §8 JSONL 解析（容错、可定位、不改语义）；
- :mod:`liveblocks.ui.replay` —— 回放时间线与单步状态（从记录重算，确定）；
- :mod:`liveblocks.ui.raster` —— 叠加面板的确定性光栅（自带点阵字体与 PNG 编码器）；
- :mod:`liveblocks.ui.render` —— 一屏渲染（HTML / 文本 / 叠加 PNG）；
- :mod:`liveblocks.ui.evidence` —— 截图证据的最小 PNG 统计（证明「不是空白图」，不替代人眼）；
- :mod:`liveblocks.ui.docs` —— ``docs/UI.md`` 与实现的核对（文档缺口）；
- :mod:`liveblocks.ui.report` —— 对 ``fixtures/runs`` 跑一遍并打印验收对照。

界面**只读**：不写回记录、不改业务数据，也不对外发布（LB-008 的「不做」）。
"""

from __future__ import annotations

from .protocol import UI_VERSION, RecordIssue
from .record import RunRecord, parse_record_file, parse_record_text
from .render import View, build_view, build_view_from_file, render_overlay
from .replay import Replay, build_replay

__all__ = [
    "UI_VERSION",
    "RecordIssue",
    "RunRecord",
    "Replay",
    "View",
    "build_view",
    "build_view_from_file",
    "build_replay",
    "parse_record_file",
    "parse_record_text",
    "render_overlay",
]
