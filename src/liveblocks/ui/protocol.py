"""调试界面（S7b ``ui.debug``）的常量、问题码与运行记录的最小校验。

``docs/PROTOCOL.md`` §8 只规定「首行 ``RunHeader``，随后按产出顺序写各层对象与事件」，
没有定义：``RunHeader`` 的必填字段、引用的帧图像怎么找到、**分层耗时**放在哪里、
坏记录怎么报（§9 只有各层的 ``LB-*`` 码，没有界面层的码）、以及「一层缺了」在界面上的表现。
本模块按 §10 规则 1 做**加法式**补充，并把缺口写进 ``docs/UI.md`` 与
``docs/DECISIONS.md``（D-020），不改 ``docs/PROTOCOL.md``（协议由仓库所有者维护）：

- 运行记录行的 ``kind`` 取值沿用 §1 的层名；``RunHeader`` 之外**允许**出现本层加法式字段
  （``frame`` / ``timings_ms`` / ``layers``），缺失时降级为可见提示而不是失败；
- 界面层的问题码用 ``LB-U0xx`` 区间（U = UI），形状沿用 §9 的诊断对象；
- 记录是**只读**输入：解析过程只产出 ``RecordIssue``，绝不抛异常打断渲染。

``RecordIssue`` 的 ``severity`` 语义：

- ``error``：这一层/这一行没法用（缺字段、不是合法 JSON、kind 不认识……），界面上显示占位；
- ``warning``：能用但不完整（耗时缺失、事件 ``seq`` 不连续、帧图像找不到……）；
- ``info``：只是说明（例如某层被正确跳过）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: 本层实现的 ``schema_version``（与 §0 的 ``major.minor`` 形状一致，示例值同其他层）。
SCHEMA_VERSION = "0.0.1"

#: 调试视图的版本（``docs/UI.md`` 引用它）。
UI_VERSION = "0.0.1"

KIND_RUN_HEADER = "RunHeader"
KIND_MARKER = "MarkerCandidateList"
KIND_CARD = "CardCandidateList"
KIND_LAYOUT = "Layout"
KIND_AST = "Ast"
KIND_IR = "Ir"
KIND_EXEC_EVENT = "ExecEvent"
KIND_EXEC_RESULT = "ExecResult"

#: §1 里 ``ui.debug`` 作为消费者要看的层，按「运行记录里的产出顺序」排列。
#: 前五项正好是一屏里的五个面板（帧叠加由 S1/S2 合成面板）。
LAYER_ORDER = ("vision", "layout", "ast", "ir", "exec")

#: 面板标题（界面上原样显示，测试与报告按它核对「五层同时呈现」）。
LAYER_TITLES = {
    "vision": "① 帧叠加（S2 识别）",
    "layout": "② 排列（S3）",
    "ast": "③ AST（S4）",
    "ir": "④ IR（S5）",
    "exec": "⑤ 事件流（S6）+ 分层耗时",
}

#: 每个 ``kind`` 属于哪个面板（``ExecEvent`` / ``ExecResult`` 同属 ``exec``）。
LAYER_OF_KIND = {
    KIND_MARKER: "vision",
    KIND_CARD: "vision",
    KIND_LAYOUT: "layout",
    KIND_AST: "ast",
    KIND_IR: "ir",
    KIND_EXEC_EVENT: "exec",
    KIND_EXEC_RESULT: "exec",
}

#: §8 要求的层（缺一层就报 ``LB-U004``）。``MarkerCandidateList`` 不在其中：它是 S2a 的
#: 中间产物，§3.2 的 ``CardCandidateList`` 才是 ``layout`` 的输入，记录里可以没有它。
REQUIRED_KINDS = (KIND_CARD, KIND_LAYOUT, KIND_AST, KIND_IR, KIND_EXEC_RESULT)

#: 五个面板对应的分层耗时键（``RunHeader.timings_ms``，本层加法式字段）。
TIMING_LAYERS = ("vision", "layout", "parser", "ir", "exec")

#: 分层耗时的中文标签。
TIMING_LABELS = {
    "vision": "识别（vision）",
    "layout": "排列（layout）",
    "parser": "语法（parser）",
    "ir": "规范化（ir）",
    "exec": "执行（exec）",
}

#: 界面层问题码（本实现新增，§9 的 ``LB-U0xx`` 区间）。
ISSUE_BAD_LINE = "LB-U001"  # 行不是合法 JSON
ISSUE_UNKNOWN_KIND = "LB-U002"  # kind 缺失/不是字符串/不是已知层
ISSUE_BAD_FIELDS = "LB-U003"  # 层对象缺少必填字段或字段类型不符
ISSUE_MISSING_LAYER = "LB-U004"  # §8 要求的层没有出现在记录里
ISSUE_ORDER = "LB-U005"  # 层顺序错乱或同一层重复出现
ISSUE_NO_HEADER = "LB-U006"  # 记录为空 / 首行不是 RunHeader
ISSUE_BAD_FRAME = "LB-U007"  # 引用的帧图像读不出来或尺寸不符
ISSUE_BAD_EVENTS = "LB-U008"  # 事件流 seq/时间不单调
ISSUE_BAD_TIMINGS = "LB-U009"  # 分层耗时缺失或非法

#: 全部界面层问题码（文档核对与「每个码都有触发路径」的测试据此遍历）。
ISSUE_CODES = (
    ISSUE_BAD_LINE,
    ISSUE_UNKNOWN_KIND,
    ISSUE_BAD_FIELDS,
    ISSUE_MISSING_LAYER,
    ISSUE_ORDER,
    ISSUE_NO_HEADER,
    ISSUE_BAD_FRAME,
    ISSUE_BAD_EVENTS,
    ISSUE_BAD_TIMINGS,
)

#: 码 → 一级标题（``docs/UI.md`` 的表格与 ``ui_document_gaps`` 按它核对）。
ISSUE_TITLES = {
    ISSUE_BAD_LINE: "记录某行不是合法 JSON",
    ISSUE_UNKNOWN_KIND: "行里的 kind 缺失或不是已知层",
    ISSUE_BAD_FIELDS: "层对象缺少必填字段或字段类型不符",
    ISSUE_MISSING_LAYER: "§8 要求的层没有出现在记录里",
    ISSUE_ORDER: "层顺序错乱或同一层重复出现",
    ISSUE_NO_HEADER: "记录为空或首行不是 RunHeader",
    ISSUE_BAD_FRAME: "引用的帧图像读不出来或尺寸不符",
    ISSUE_BAD_EVENTS: "事件流 seq / 时间不单调",
    ISSUE_BAD_TIMINGS: "分层耗时缺失或非法",
}

SEVERITIES = ("info", "warning", "error")

#: ``schema_version`` 的容忍形状（``major.minor`` 或 ``major.minor.patch``，同其他层）。
VERSION_PATTERN = re.compile(r"^\d+\.\d+(\.\d+)?$")


@dataclass(frozen=True)
class RecordIssue:
    """一条界面层问题（§9 诊断对象的形状 + 记录里的行号与所属面板）。"""

    code: str
    severity: str
    message: str
    line: int = 0
    layer: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity 必须属于 {list(SEVERITIES)}：{self.severity!r}")
        if self.code not in ISSUE_CODES:
            raise ValueError(f"未知的界面层问题码：{self.code!r}")

    def as_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.line:
            entry["line"] = self.line
        if self.layer:
            entry["layer"] = self.layer
        return entry

    def describe(self) -> str:
        where = f"第 {self.line} 行" if self.line else "记录"
        return f"{self.code}（{self.severity}，{where}）：{self.message}"


def issue(code: str, severity: str, message: str, *, line: int = 0, layer: str = "") -> RecordIssue:
    """构造一条 ``RecordIssue``（方便调用处写短一点）。"""
    return RecordIssue(code=code, severity=severity, message=message, line=line, layer=layer)


@dataclass(frozen=True)
class LayerIssue:
    """``diagnostics`` 数组里的一条（各层自己的 ``LB-*`` 码，原样展示，不改语义）。"""

    code: str = ""
    severity: str = "info"
    message: str = ""
    layer: str = ""

    def describe(self) -> str:
        return f"{self.code}（{self.severity}）：{self.message}"


def layer_issues(document: Any, layer: str) -> list[LayerIssue]:
    """把一份层对象的 ``diagnostics`` 取成 ``LayerIssue``；形状不对就返回空并让上层报码。"""
    if not isinstance(document, dict):
        return []
    entries = document.get("diagnostics")
    if not isinstance(entries, list):
        return []
    result: list[LayerIssue] = []
    for entry in entries:
        if not isinstance(entry, dict):
            result.append(LayerIssue(code="?", severity="warning", message=str(entry), layer=layer))
            continue
        severity = entry.get("severity")
        result.append(
            LayerIssue(
                code=str(entry.get("code") or "?"),
                severity=severity if severity in SEVERITIES else "info",
                message=str(entry.get("message") or ""),
                layer=layer,
            )
        )
    return result


# --- 最小校验：只看渲染要用到的字段，缺了就报 LB-U003，不抛异常 -----------------


@dataclass
class FieldReport:
    """一次最小校验的结果：问题列表 + 渲染时可以直接用的「干净」字段。"""

    issues: list[RecordIssue] = field(default_factory=list)

    def missing(self, message: str, *, line: int, layer: str, severity: str = "error") -> None:
        self.issues.append(issue(ISSUE_BAD_FIELDS, severity, message, line=line, layer=layer))


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _point(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x = _as_int(value[0])
    y = _as_int(value[1])
    if x is None or y is None:
        return None
    return (x, y)


def _quad(value: Any) -> list[tuple[int, int]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    points = []
    for item in value:
        point = _point(item)
        if point is None:
            return None
        points.append(point)
    return points


def check_marker_list(document: dict[str, Any], *, line: int) -> tuple[list[dict[str, Any]], FieldReport]:
    """``MarkerCandidateList``（§3.1）：只取界面上要用到的 ``markers[]`` 字段。"""
    report = FieldReport()
    markers_raw = document.get("markers")
    if not isinstance(markers_raw, list):
        report.missing("MarkerCandidateList 缺少 markers 数组", line=line, layer="vision")
        return [], report
    markers: list[dict[str, Any]] = []
    for index, entry in enumerate(markers_raw):
        if not isinstance(entry, dict):
            report.missing(f"markers[{index}] 不是对象", line=line, layer="vision", severity="warning")
            continue
        corners = _quad(entry.get("corners_px"))
        center = _point(entry.get("center_px"))
        marker_id = entry.get("marker_id")
        if corners is None and center is None:
            report.missing(
                f"markers[{index}] 既没有可用的 corners_px 也没有 center_px，画不出来",
                line=line,
                layer="vision",
                severity="warning",
            )
            continue
        markers.append(
            {
                "marker_id": "" if marker_id is None else str(marker_id),
                "corners": corners or [],
                "center": center,
                "confidence": _as_float(entry.get("confidence")),
            }
        )
    return markers, report


def check_card_list(document: dict[str, Any], *, line: int) -> tuple[list[dict[str, Any]], FieldReport]:
    """``CardCandidateList``（§3.2）：``cards[]`` 至少要能定位到画面上的点。"""
    report = FieldReport()
    cards_raw = document.get("cards")
    if not isinstance(cards_raw, list):
        report.missing("CardCandidateList 缺少 cards 数组", line=line, layer="vision")
        return [], report
    cards: list[dict[str, Any]] = []
    for index, entry in enumerate(cards_raw):
        if not isinstance(entry, dict):
            report.missing(f"cards[{index}] 不是对象", line=line, layer="vision", severity="warning")
            continue
        center = _point(entry.get("center_px"))
        candidate_id = entry.get("candidate_id")
        if center is None:
            report.missing(
                f"cards[{index}]（candidate_id={candidate_id!r}）缺少可用的 center_px，叠加图画不出来",
                line=line,
                layer="vision",
                severity="warning",
            )
            continue
        cards.append(
            {
                "candidate_id": "" if candidate_id is None else str(candidate_id),
                "card_id": str(entry.get("card_id") or "unknown"),
                "card_face": str(entry.get("card_face") or "unknown"),
                "status": str(entry.get("status") or "unknown"),
                "center": center,
                "confidence": _as_float(entry.get("confidence")),
            }
        )
    return cards, report


def check_layout(document: dict[str, Any], *, line: int) -> tuple[list[dict[str, Any]], FieldReport]:
    """``Layout``（§4）：``rows[]`` → ``slots[]``，槽位要有 ``card_face`` 与 ``slot_index``。"""
    report = FieldReport()
    rows_raw = document.get("rows")
    if not isinstance(rows_raw, list):
        report.missing("Layout 缺少 rows 数组", line=line, layer="layout")
        return [], report
    rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows_raw):
        if not isinstance(row, dict):
            report.missing(f"rows[{row_index}] 不是对象", line=line, layer="layout", severity="warning")
            continue
        slots_raw = row.get("slots")
        if not isinstance(slots_raw, list):
            report.missing(f"rows[{row_index}] 缺少 slots 数组", line=line, layer="layout", severity="warning")
            slots_raw = []
        slots: list[dict[str, Any]] = []
        for slot_index, slot in enumerate(slots_raw):
            if not isinstance(slot, dict):
                report.missing(
                    f"rows[{row_index}].slots[{slot_index}] 不是对象",
                    line=line,
                    layer="layout",
                    severity="warning",
                )
                continue
            slots.append(
                {
                    "slot_index": _as_int(slot.get("slot_index")) if slot.get("slot_index") is not None else slot_index,
                    "candidate_id": str(slot.get("candidate_id") or ""),
                    "card_face": str(slot.get("card_face") or "unknown"),
                    "x_px": _as_int(slot.get("x_px")),
                    "y_px": _as_int(slot.get("y_px")),
                    "confidence": _as_float(slot.get("confidence")),
                }
            )
        counts = row.get("counts")
        rows.append(
            {
                "row_index": _as_int(row.get("row_index")) if row.get("row_index") is not None else row_index,
                "slots": slots,
                "counts": counts if isinstance(counts, dict) else {},
            }
        )
    basis = document.get("basis")
    return rows, report


def check_ast(document: dict[str, Any], *, line: int) -> tuple[dict[str, Any], FieldReport]:
    """``Ast``（§5）：要有一个 ``root`` 节点，``node`` 字段用来区分节点类型。"""
    report = FieldReport()
    root = document.get("root")
    if not isinstance(root, dict):
        report.missing("Ast 缺少 root 节点", line=line, layer="ast")
        return {}, report
    if not isinstance(root.get("node"), str):
        report.missing("Ast.root 缺少 node 字段", line=line, layer="ast")
        return {}, report
    return root, report


def check_ir(document: dict[str, Any], *, line: int) -> tuple[list[dict[str, Any]], dict[str, Any], FieldReport]:
    """``Ir``（§6）：``program.steps[]`` 与 ``program.limits``。"""
    report = FieldReport()
    program = document.get("program")
    if not isinstance(program, dict):
        report.missing("Ir 缺少 program 对象", line=line, layer="ir")
        return [], {}, report
    steps_raw = program.get("steps")
    if not isinstance(steps_raw, list):
        report.missing("Ir.program 缺少 steps 数组", line=line, layer="ir")
        steps_raw = []
    steps: list[dict[str, Any]] = []
    for index, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            report.missing(f"steps[{index}] 不是对象", line=line, layer="ir", severity="warning")
            continue
        steps.append(
            {
                "step_id": str(step.get("step_id") or f"#{index}"),
                "op": str(step.get("op") or "unknown"),
                "args": step.get("args") if isinstance(step.get("args"), dict) else {},
                "source": step.get("source") if isinstance(step.get("source"), dict) else {},
            }
        )
    limits = program.get("limits")
    return steps, (limits if isinstance(limits, dict) else {}), report


def check_event(entry: Any, *, line: int) -> tuple[dict[str, Any] | None, FieldReport]:
    """``ExecEvent``（§7.1）：``seq`` / ``at_ms`` / ``phase`` / ``step_id`` 是单步要用的。"""
    report = FieldReport()
    if not isinstance(entry, dict):
        report.missing("ExecEvent 不是对象", line=line, layer="exec", severity="warning")
        return None, report
    seq = _as_int(entry.get("seq"))
    at_ms = _as_float(entry.get("at_ms"))
    phase = entry.get("phase")
    if seq is None:
        report.missing("ExecEvent 缺少整数 seq，单步定位会跳过这条", line=line, layer="exec", severity="warning")
        return None, report
    if at_ms is None:
        report.missing(f"ExecEvent seq={seq} 缺少数值 at_ms", line=line, layer="exec", severity="warning")
        at_ms = 0.0
    if not isinstance(phase, str):
        report.missing(f"ExecEvent seq={seq} 缺少 phase", line=line, layer="exec", severity="warning")
        phase = "unknown"
    detail = entry.get("detail")
    return (
        {
            "seq": seq,
            "at_ms": at_ms,
            "phase": phase,
            "step_id": str(entry.get("step_id") or ""),
            "backend": str(entry.get("backend") or "unknown"),
            "detail": detail if isinstance(detail, dict) else {},
        },
        report,
    )


def check_result(document: dict[str, Any], *, line: int) -> tuple[dict[str, Any], FieldReport]:
    """``ExecResult``（§7.2）：``status`` / ``steps_executed`` 给界面上显示收尾结论。"""
    report = FieldReport()
    status = document.get("status")
    if not isinstance(status, str):
        report.missing("ExecResult 缺少 status", line=line, layer="exec", severity="warning")
        status = "unknown"
    steps_executed = _as_int(document.get("steps_executed"))
    if steps_executed is None:
        report.missing("ExecResult 缺少整数 steps_executed", line=line, layer="exec", severity="warning")
        steps_executed = 0
    duration_ms = _as_float(document.get("duration_ms"))
    return (
        {
            "status": status,
            "steps_executed": steps_executed,
            "duration_ms": 0.0 if duration_ms is None else duration_ms,
            "backend": str(document.get("backend") or "unknown"),
        },
        report,
    )


def check_header(document: dict[str, Any], *, line: int) -> tuple[dict[str, Any], FieldReport]:
    """``RunHeader``（§8）：``run_id`` / ``backend`` 必填，``frame`` / ``timings_ms`` 可缺。"""
    report = FieldReport()
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        report.missing("RunHeader 缺少 run_id", line=line, layer="header")
        run_id = ""
    backend = document.get("backend")
    if not isinstance(backend, str) or not backend:
        report.missing("RunHeader 缺少 backend（sim / hw）", line=line, layer="header", severity="warning")
        backend = "unknown"
    frame = document.get("frame")
    frame_ref: dict[str, Any] = {}
    if isinstance(frame, dict):
        frame_ref = {
            "frame_id": str(frame.get("frame_id") or document.get("frame_id") or ""),
            "image_ref": frame.get("image_ref"),
            "width": _as_int(frame.get("width")),
            "height": _as_int(frame.get("height")),
            "source": str(frame.get("source") or "unknown"),
        }
        if not isinstance(frame_ref["image_ref"], str) or not frame_ref["image_ref"]:
            report.issues.append(
                issue(
                    ISSUE_BAD_FRAME,
                    "warning",
                    "RunHeader.frame 没有 image_ref：叠加图只能给出占位",
                    line=line,
                    layer="vision",
                )
            )
            frame_ref["image_ref"] = None
    else:
        report.issues.append(
            issue(
                ISSUE_BAD_FRAME,
                "warning",
                "RunHeader 没有 frame 对象（§8 的「输入帧引用」缺失）：叠加图只能给出占位",
                line=line,
                layer="vision",
            )
        )
    timings_raw = document.get("timings_ms")
    timings: dict[str, float] = {}
    if isinstance(timings_raw, dict):
        for key in TIMING_LAYERS:
            value = _as_float(timings_raw.get(key))
            if value is None:
                report.issues.append(
                    issue(
                        ISSUE_BAD_TIMINGS,
                        "warning",
                        f"RunHeader.timings_ms 缺少或不是数字的 {key!r}：该层耗时显示为未知",
                        line=line,
                        layer=key,
                    )
                )
            elif value < 0:
                report.issues.append(
                    issue(
                        ISSUE_BAD_TIMINGS,
                        "warning",
                        f"RunHeader.timings_ms.{key} 是负数（{value}）：按未知处理",
                        line=line,
                        layer=key,
                    )
                )
            else:
                timings[key] = value
    else:
        report.issues.append(
            issue(
                ISSUE_BAD_TIMINGS,
                "warning",
                "RunHeader 没有 timings_ms（§8 未定义分层耗时字段，本实现按加法式可选字段处理）："
                "五层里「耗时」一栏显示为未知",
                line=line,
                layer="header",
            )
        )
    return (
        {
            "run_id": run_id,
            "backend": backend,
            "frame": frame_ref,
            "timings_ms": timings,
            "scene": str(document.get("scene") or ""),
            "created_with": document.get("created_with") if isinstance(document.get("created_with"), dict) else {},
        },
        report,
    )


def format_ms(value: float) -> str:
    """耗时的显示口径：小于 1 ms 保留 3 位，其余保留 2 位（固定，便于字节比对）。"""
    if value < 1.0:
        return f"{value:.3f}"
    return f"{value:.2f}"


__all__ = [
    "SCHEMA_VERSION",
    "UI_VERSION",
    "KIND_RUN_HEADER",
    "KIND_MARKER",
    "KIND_CARD",
    "KIND_LAYOUT",
    "KIND_AST",
    "KIND_IR",
    "KIND_EXEC_EVENT",
    "KIND_EXEC_RESULT",
    "LAYER_ORDER",
    "LAYER_TITLES",
    "LAYER_OF_KIND",
    "REQUIRED_KINDS",
    "TIMING_LAYERS",
    "TIMING_LABELS",
    "ISSUE_CODES",
    "ISSUE_TITLES",
    "ISSUE_BAD_LINE",
    "ISSUE_UNKNOWN_KIND",
    "ISSUE_BAD_FIELDS",
    "ISSUE_MISSING_LAYER",
    "ISSUE_ORDER",
    "ISSUE_NO_HEADER",
    "ISSUE_BAD_FRAME",
    "ISSUE_BAD_EVENTS",
    "ISSUE_BAD_TIMINGS",
    "SEVERITIES",
    "VERSION_PATTERN",
    "RecordIssue",
    "LayerIssue",
    "issue",
    "layer_issues",
    "check_header",
    "check_marker_list",
    "check_card_list",
    "check_layout",
    "check_ast",
    "check_ir",
    "check_event",
    "check_result",
    "format_ms",
]
