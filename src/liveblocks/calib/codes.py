"""LB-031 的诊断码表（``LB-B0xx``）与文档核对。

码表与 ``docs/CALIB.md`` §6 一一对应；:func:`document_gaps` 会逐条核对文档里是否真的写了
这些码与标题（``tests/test_calib_docs.py`` 用它做正反两向验证，避免「文档写了、实现没做」
或「实现有码、文档没写」）。
"""

from __future__ import annotations

from typing import Any

#: 诊断码 → 一行标题（与 ``docs/CALIB.md`` §6 的表格逐字对应）。
CODES: dict[str, str] = {
    "LB-B001": "标定板方块检出不足",
    "LB-B002": "格点编号（网格自洽）失败",
    "LB-B003": "内角点缺失",
    "LB-B004": "ChArUco 标记不足",
    "LB-B005": "标定视图不足",
    "LB-B006": "重投影误差超过阈值",
    "LB-B007": "校正前后角点重复性超过阈值",
    "LB-B008": "标定文件字段缺失或类型不符",
}


def diagnostic(code: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    """构造一条诊断（形状与 ``docs/PROTOCOL.md`` §9 的其他层一致）。"""
    if code not in CODES:
        raise KeyError(f"未登记的诊断码：{code}")
    if severity not in ("error", "warning", "info"):
        raise ValueError(f"severity 只能是 error/warning/info：{severity}")
    entry: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    entry.update(extra)
    return entry


def document_gaps(markdown: str) -> list[str]:
    """核对文档里每个码与标题都出现了；返回缺口列表（空 = 齐全）。"""
    gaps: list[str] = []
    for code, title in CODES.items():
        if code not in markdown:
            gaps.append(f"{code} 没有出现在文档里")
        elif title not in markdown:
            gaps.append(f"{code} 的标题「{title}」没有出现在文档里")
    return gaps
