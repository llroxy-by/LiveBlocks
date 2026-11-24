"""运行记录（§8 JSONL）的解析：坏字段只报问题码，不抛异常。

设计要点（``docs/UI.md`` §2）：

1. **只读**：``parse_record`` 不改输入，也不落盘；
2. **容错**：任何一行坏掉都只留下一条 ``RecordIssue``，其余行照常解析，渲染继续；
3. **可定位**：每条 ``RecordIssue`` 带 ``line``（记录里的 1 基行号）与 ``layer``（哪个面板）；
4. **确定性**：解析结果只依赖输入字节（不含时间、不含哈希随机序），同一个文件解析两次
   ``RecordIssue`` 列表与所有字段逐字段相等。

解析结果里的字段都是渲染直接要用的「干净值」——原始文档另存在 ``documents`` 里，
供 AST 树等需要看全貌的面板使用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import (
    ISSUE_BAD_EVENTS,
    ISSUE_BAD_FRAME,
    ISSUE_BAD_LINE,
    ISSUE_MISSING_LAYER,
    ISSUE_NO_HEADER,
    ISSUE_ORDER,
    ISSUE_UNKNOWN_KIND,
    KIND_AST,
    KIND_CARD,
    KIND_EXEC_EVENT,
    KIND_EXEC_RESULT,
    KIND_IR,
    KIND_LAYOUT,
    KIND_MARKER,
    KIND_RUN_HEADER,
    LAYER_OF_KIND,
    REQUIRED_KINDS,
    LayerIssue,
    RecordIssue,
    check_ast,
    check_card_list,
    check_event,
    check_header,
    check_ir,
    check_layout,
    check_marker_list,
    check_result,
    issue,
    layer_issues,
)

#: 运行记录里各 ``kind`` 的期望顺序（用于 ``LB-U005`` 的乱序判定）。
_KIND_RANK = {
    KIND_MARKER: 0,
    KIND_CARD: 1,
    KIND_LAYOUT: 2,
    KIND_AST: 3,
    KIND_IR: 4,
    KIND_EXEC_EVENT: 5,
    KIND_EXEC_RESULT: 6,
}


@dataclass
class RunRecord:
    """一份运行记录解析后的样子。缺的层就是空的，不补假数据。"""

    source: str = ""
    lines: int = 0
    header: dict[str, Any] | None = None
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    markers: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    ast_root: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    limits: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    issues: list[RecordIssue] = field(default_factory=list)

    # --- 便捷视图 ---------------------------------------------------------

    @property
    def run_id(self) -> str:
        return "" if self.header is None else str(self.header.get("run_id") or "")

    @property
    def backend(self) -> str:
        return "" if self.header is None else str(self.header.get("backend") or "")

    @property
    def frame(self) -> dict[str, Any]:
        return {} if self.header is None else dict(self.header.get("frame") or {})

    @property
    def frame_id(self) -> str:
        return str(self.frame.get("frame_id") or "")

    @property
    def timings(self) -> dict[str, float]:
        return {} if self.header is None else dict(self.header.get("timings_ms") or {})

    @property
    def present_kinds(self) -> tuple[str, ...]:
        return tuple(
            kind
            for kind in _KIND_RANK
            if kind in self.documents or (kind == KIND_EXEC_EVENT and self.events)
        )

    @property
    def missing_layers(self) -> tuple[str, ...]:
        layers = []
        for kind in REQUIRED_KINDS:
            if kind not in self.documents:
                layer = LAYER_OF_KIND[kind]
                if layer not in layers:
                    layers.append(layer)
        return tuple(layers)

    def issues_for(self, layer: str) -> tuple[RecordIssue, ...]:
        return tuple(entry for entry in self.issues if entry.layer == layer)

    def layer_diagnostics(self, layer: str) -> tuple[LayerIssue, ...]:
        """某面板涉及的各层文档里的 ``diagnostics``（原样展示，按 (code, message) 去重）。

        去重是必要的：LB-003 会把标记层的问题（例如 ``LB-V002`` 标记被裁切）同时写进
        ``MarkerCandidateList`` 与 ``CardCandidateList``，同一句在面板上出现两次纯属噪声。
        """
        result: list[LayerIssue] = []
        seen: set[tuple[str, str]] = set()
        for kind in _KIND_RANK:
            if LAYER_OF_KIND[kind] != layer:
                continue
            document = self.documents.get(kind)
            if document is None:
                continue
            for entry in layer_issues(document, layer):
                key = (entry.code, entry.message)
                if key in seen:
                    continue
                seen.add(key)
                result.append(entry)
        return tuple(result)

    @property
    def severity_counts(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for entry in self.issues:
            counts[entry.severity] = counts.get(entry.severity, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        return not self.issues and self.missing_layers == ()

    @property
    def has_error(self) -> bool:
        return any(entry.severity == "error" for entry in self.issues)

    def summary(self) -> str:
        counts = self.severity_counts
        return (
            f"run_id={self.run_id or '?'} backend={self.backend or '?'} "
            f"层={len(self.present_kinds)}/7 事件={len(self.events)} "
            f"问题={len(self.issues)}（error {counts['error']} / warning {counts['warning']}）"
        )


def _json_object(text: str) -> tuple[dict[str, Any] | None, str]:
    """解析一行；返回 ``(对象, 错误说明)``。"""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：{exc.msg}（第 {exc.colno} 列）"
    if not isinstance(value, dict):
        return None, f"这一行是 {type(value).__name__}，不是对象"
    return value, ""


def parse_record_text(text: str, *, source: str = "") -> RunRecord:
    """把 §8 JSONL 文本解析成 ``RunRecord``。**不抛异常**。"""
    record = RunRecord(source=source)
    raw_lines = text.splitlines()
    record.lines = len(raw_lines)

    seen_header = False
    last_rank = -1
    last_seq = 0
    last_at_ms = 0.0
    for number, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        document, error = _json_object(raw)
        if document is None:
            record.issues.append(issue(ISSUE_BAD_LINE, "error", error, line=number))
            continue
        kind = document.get("kind")
        if not isinstance(kind, str) or (kind not in LAYER_OF_KIND and kind != KIND_RUN_HEADER):
            record.issues.append(
                issue(
                    ISSUE_UNKNOWN_KIND,
                    "error",
                    f"kind={kind!r} 不是 §8 / §1 里的层名，这一行被忽略",
                    line=number,
                )
            )
            continue

        if kind == KIND_RUN_HEADER:
            if seen_header:
                record.issues.append(
                    issue(ISSUE_ORDER, "error", "重复的 RunHeader（§8 要求首行一份），这一行被忽略", line=number)
                )
                continue
            if any(raw_previous.strip() for raw_previous in raw_lines[: number - 1]):
                record.issues.append(
                    issue(
                        ISSUE_ORDER,
                        "warning",
                        "RunHeader 不是首行（§8 要求首行），仍按 header 使用",
                        line=number,
                    )
                )
            header, report = check_header(document, line=number)
            record.header = header
            record.documents[KIND_RUN_HEADER] = document
            record.issues.extend(report.issues)
            seen_header = True
            continue

        if not seen_header:
            record.issues.append(
                issue(
                    ISSUE_NO_HEADER,
                    "error",
                    f"首行不是 RunHeader（这一行是 {kind}，§8 要求首行是 RunHeader），仍继续解析",
                    line=number,
                )
            )

        rank = _KIND_RANK[kind]
        if rank < last_rank:
            record.issues.append(
                issue(
                    ISSUE_ORDER,
                    "warning",
                    f"{kind} 出现在 {_kind_at(last_rank)!s} 之后（§8 要求按产出顺序），按出现顺序展示",
                    line=number,
                )
            )
        last_rank = max(last_rank, rank)

        if kind != KIND_EXEC_EVENT and kind in record.documents:
            record.issues.append(
                issue(
                    ISSUE_ORDER,
                    "warning",
                    f"{kind} 重复出现（只保留第一份用于渲染）",
                    line=number,
                )
            )
            continue
        if kind != KIND_EXEC_EVENT:
            record.documents[kind] = document

        if kind == KIND_MARKER:
            markers, report = check_marker_list(document, line=number)
            record.markers = markers
            record.issues.extend(report.issues)
        elif kind == KIND_CARD:
            cards, report = check_card_list(document, line=number)
            record.cards = cards
            record.issues.extend(report.issues)
        elif kind == KIND_LAYOUT:
            rows, report = check_layout(document, line=number)
            record.rows = rows
            record.issues.extend(report.issues)
        elif kind == KIND_AST:
            root, report = check_ast(document, line=number)
            record.ast_root = root
            record.issues.extend(report.issues)
        elif kind == KIND_IR:
            steps, limits, report = check_ir(document, line=number)
            record.steps = steps
            record.limits = limits
            record.issues.extend(report.issues)
        elif kind == KIND_EXEC_RESULT:
            result, report = check_result(document, line=number)
            record.result = result
            record.issues.extend(report.issues)
        elif kind == KIND_EXEC_EVENT:
            event, report = check_event(document, line=number)
            record.issues.extend(report.issues)
            if event is None:
                continue
            if event["seq"] <= last_seq and record.events:
                record.issues.append(
                    issue(
                        ISSUE_BAD_EVENTS,
                        "warning",
                        f"seq={event['seq']} 没有比上一条（{last_seq}）大（§7.1 要求严格递增），"
                        "事件仍按出现顺序展示",
                        line=number,
                        layer="exec",
                    )
                )
            if event["at_ms"] < last_at_ms:
                record.issues.append(
                    issue(
                        ISSUE_BAD_EVENTS,
                        "warning",
                        f"seq={event['seq']} 的 at_ms={event['at_ms']} 比上一条（{last_at_ms}）小，"
                        "时间轴按原值展示",
                        line=number,
                        layer="exec",
                    )
                )
            record.events.append(event)
            last_seq = max(last_seq, event["seq"])
            last_at_ms = max(last_at_ms, event["at_ms"])

    if not seen_header:
        record.issues.append(
            issue(ISSUE_NO_HEADER, "error", "记录里没有 RunHeader（§8 要求首行是 RunHeader）", line=0)
        )

    for kind in REQUIRED_KINDS:
        if kind not in record.documents:
            layer = LAYER_OF_KIND[kind]
            record.issues.append(
                issue(
                    ISSUE_MISSING_LAYER,
                    "error",
                    f"§8 要求的 {kind} 没有出现在记录里：{layer} 面板显示为缺失占位",
                    line=0,
                    layer=layer,
                )
            )
    if KIND_MARKER not in record.documents and KIND_CARD in record.documents:
        record.issues.append(
            issue(
                ISSUE_MISSING_LAYER,
                "info",
                "§8 的 S2a 中间产物 MarkerCandidateList 不在记录里：叠加图只画卡片候选，不画标记框",
                line=0,
                layer="vision",
            )
        )
    if not record.events and KIND_EXEC_RESULT in record.documents:
        record.issues.append(
            issue(
                ISSUE_MISSING_LAYER,
                "warning",
                "记录里有 ExecResult 却没有 ExecEvent：事件流面板只有收尾结论，单步没有内容",
                line=0,
                layer="exec",
            )
        )
    if record.header is not None and str(record.header.get("backend")) == "hw":
        record.issues.append(
            issue(
                ISSUE_BAD_FRAME,
                "info",
                "这份记录的 backend 是 hw：界面只读回放，不代表硬件验证结论",
                line=1,
                layer="exec",
            )
        )
    return record


def _kind_at(rank: int) -> str:
    for kind, value in _KIND_RANK.items():
        if value == rank:
            return kind
    return "?"


def parse_record_file(path: str | Path) -> RunRecord:
    """读文件并解析；文件读不出来也**不抛异常**，只留一条 ``LB-U001``。"""
    record_path = Path(path)
    try:
        text = record_path.read_text(encoding="utf-8")
    except OSError as exc:
        record = RunRecord(source=str(record_path))
        record.issues.append(issue(ISSUE_BAD_LINE, "error", f"记录文件读不出来：{exc}"))
        return record
    except UnicodeDecodeError as exc:
        record = RunRecord(source=str(record_path))
        record.issues.append(issue(ISSUE_BAD_LINE, "error", f"记录文件不是 UTF-8：{exc}"))
        return record
    return parse_record_text(text, source=str(record_path))


def resolve_frame_path(image_ref: str | None, *, record_path: str | Path | None) -> Path | None:
    """把 ``image_ref`` 解析成存在的文件路径（相对记录目录、相对仓库根都试一次）。

    找不到就返回 ``None``（调用方报 ``LB-U007`` 并画占位），不抛异常、不联网。
    """
    if not image_ref:
        return None
    candidate = Path(image_ref)
    attempts: list[Path] = []
    if candidate.is_absolute():
        attempts.append(candidate)
    else:
        if record_path is not None:
            attempts.append(Path(record_path).resolve().parent / candidate)
        attempts.append(Path.cwd() / candidate)
        repo_root = Path(__file__).resolve().parents[3]
        attempts.append(repo_root / candidate)
    for attempt in attempts:
        if attempt.is_file():
            return attempt
    return None


__all__ = ["RunRecord", "parse_record_text", "parse_record_file", "resolve_frame_path"]
