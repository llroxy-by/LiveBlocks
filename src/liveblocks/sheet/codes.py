"""LB-032 的诊断码表（``LB-S0xx``）与文档核对。

码表与 ``docs/CARD-SPEC.md`` §10 一一对应；:func:`document_gaps` 逐条核对文档里是否真的写了
这些码与标题（``tests/test_sheet_docs.py`` 做正反两向验证，避免「文档写了、实现没做」或
「实现有码、文档没写」）。
"""

from __future__ import annotations

from typing import Any

#: 诊断码 → 一行标题（与 ``docs/CARD-SPEC.md`` §10 的表格逐字对应）。
CODES: dict[str, str] = {
    "LB-S001": "版面参数非法",
    "LB-S002": "标记与静区放不进卡面",
    "LB-S003": "卡片放不进一页",
    "LB-S004": "重识别卡片 id 与清单不符",
    "LB-S005": "朝向读数与清单不符",
    "LB-S006": "同参数两次输出字节不一致",
    "LB-S007": "规范数字缺少溯源",
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


__all__ = ["CODES", "diagnostic", "document_gaps"]
