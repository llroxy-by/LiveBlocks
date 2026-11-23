"""S3 ``Layout`` → S4 ``Ast``（`docs/PROTOCOL.md` §5），P0 最小文法。

规则（完整定义见 ``docs/GRAMMAR.md``，本模块只是它的执行部分）：

- **一行 = 一条语句**：``Layout.rows`` 按 ``row_index`` 顺序逐个解析，行的第 0 个槽位必须是
  命令卡，其后的槽位必须是字面数字卡（参数按槽位顺序排列）。
- **出错整行丢弃、只报一条诊断**：一行的任何一个槽位违约，就把整行（这条语句）丢掉，诊断定位到
  **第一个**违约的槽位（``span`` + ``origin`` 都指向那张卡），同一行不再产生第二条诊断，避免
  一条错卡引发一串级联误报；其余行照常解析，因此非法输入也能得到部分 AST 而不是异常。
- **不检查参数个数**：§5 与 D-005 都没有定义每条命令的参数元数（arity），P0 不做这个判断
  （那是 LB-006 规范化时结合 IR 语义决定的事，已记进 ``docs/DECISIONS.md`` D-016）。
- **不复制上游诊断**：``Layout.diagnostics``（``LB-L0xx``）属于排列层，语法树只放语法层的
  ``LB-P0xx``；一次运行的所有层的对象都会进运行记录（§8），因此不会丢信息。

``parse_layout_document`` 先做 §4 校验：文档本身不符合 §4 时抛 ``ValueError``（输入坏了不是
语法错误，不该伪装成 ``LB-P0xx``）；只有**语法**问题才变成诊断。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..layout import Layout, validate_layout
from ..vision.protocol import diagnostic
from .ast import Ast, Command, Number, Origin, Program, Span
from .grammar import (
    DIAG_BAD_ARGUMENT,
    DIAG_CONTROL_UNSUPPORTED,
    DIAG_EMPTY_SLOT,
    DIAG_MISSING_COMMAND,
    DIAG_OPERATOR_UNSUPPORTED,
    ROLE_COMMAND,
    ROLE_CONTROL,
    ROLE_LITERAL,
    ROLE_OPERATOR,
    ROLE_UNKNOWN,
    card_role,
    literal_value,
)


@dataclass(frozen=True)
class _Slot:
    """``LayoutSlot``（或同形状的字典）在解析时的最小视图。"""

    slot_index: int
    candidate_id: str
    card_face: str


@dataclass(frozen=True)
class _Row:
    row_index: int
    slots: tuple[_Slot, ...]


def _to_slot(raw: Any) -> _Slot:
    if isinstance(raw, dict):
        return _Slot(
            slot_index=int(raw["slot_index"]),
            candidate_id=str(raw.get("candidate_id", "")),
            card_face=str(raw.get("card_face", "")),
        )
    return _Slot(
        slot_index=int(raw.slot_index),
        candidate_id=str(raw.candidate_id),
        card_face=str(raw.card_face),
    )


def _rows_from_document(document: dict[str, Any]) -> tuple[_Row, ...]:
    rows: list[_Row] = []
    for row in document.get("rows", []):
        rows.append(
            _Row(
                row_index=int(row["row_index"]),
                slots=tuple(_to_slot(slot) for slot in row.get("slots", [])),
            )
        )
    return tuple(rows)


def _rows_from_layout(layout: Layout) -> tuple[_Row, ...]:
    return tuple(
        _Row(row_index=int(row.row_index), slots=tuple(_to_slot(slot) for slot in row.slots))
        for row in layout.rows
    )


def _origin(slots: Sequence[_Slot]) -> Origin:
    entries = tuple((slot.candidate_id, slot.card_face) for slot in slots)
    if len(entries) == 1:
        return Origin.single(*entries[0])
    return Origin.many(entries)


def _diagnostic(
    code: str,
    message: str,
    row_index: int,
    slots: Sequence[_Slot],
) -> dict[str, Any]:
    """按 §9 形状造诊断，并补上本实现要求的 ``span`` 与 ``origin``。

    ``slots`` 是被点名的那一张（或几张）卡；``span`` 取第一个槽位的位置——诊断要能指回
    「第几行第几个槽位上的哪张卡」。
    """
    first = slots[0] if slots else _Slot(slot_index=0, candidate_id="", card_face="")
    last_index = slots[-1].slot_index if slots else first.slot_index
    span = Span.single(row_index, first.slot_index, last_index)
    entry = diagnostic(
        code,
        "error",
        message,
        candidate_ids=[slot.candidate_id for slot in slots if slot.candidate_id],
    )
    entry["span"] = span.to_dict()
    entry["origin"] = _origin(slots).to_dict()
    return entry


def _parse_row(row: _Row) -> tuple[Command | None, dict[str, Any] | None]:
    """解析一行；返回 ``(语句, 诊断)``，两者至多一个非 ``None``。"""
    if not row.slots:  # 理论上的空行：§4 不允许，但真出现也不能崩
        return None, _diagnostic(
            DIAG_MISSING_COMMAND,
            f"行 {row.row_index} 没有任何槽位，无法组成语句",
            row.row_index,
            (),
        )

    head = row.slots[0]
    head_role = card_role(head.card_face)

    if head_role == ROLE_UNKNOWN:
        return None, _diagnostic(
            DIAG_EMPTY_SLOT,
            f"行 {row.row_index} 槽位 {head.slot_index} 的卡面 {head.card_face!r} "
            "为空或不在卡片字典里，无法判断它是不是命令卡",
            row.row_index,
            (head,),
        )
    head_code = {
        ROLE_LITERAL: DIAG_MISSING_COMMAND,
        ROLE_CONTROL: DIAG_CONTROL_UNSUPPORTED,
        ROLE_OPERATOR: DIAG_OPERATOR_UNSUPPORTED,
    }.get(head_role)
    if head_code is not None:  # 字面卡 / 控制卡 / 运算符卡都不能当命令卡
        return None, _diagnostic(
            head_code, _head_message(row.row_index, head, head_role), row.row_index, (head,)
        )

    args: list[Number] = []
    for slot in row.slots[1:]:
        role = card_role(slot.card_face)
        if role == ROLE_LITERAL:
            value = literal_value(slot.card_face)
            if value is None:  # card_role 已经把这种卡面归成 unknown，这里只是兜底
                return None, _argument_diagnostic(row, slot, DIAG_EMPTY_SLOT)
            args.append(
                Number(
                    span=Span.single(row.row_index, slot.slot_index, slot.slot_index),
                    origin=_origin((slot,)),
                    value=value,
                )
            )
            continue
        if role == ROLE_UNKNOWN:
            return None, _argument_diagnostic(row, slot, DIAG_EMPTY_SLOT)
        if role == ROLE_OPERATOR:
            return None, _argument_diagnostic(row, slot, DIAG_OPERATOR_UNSUPPORTED)
        if role == ROLE_COMMAND:  # 命令卡 / wait 卡占了参数位
            return None, _argument_diagnostic(row, slot, DIAG_BAD_ARGUMENT)
        return None, _argument_diagnostic(row, slot, DIAG_CONTROL_UNSUPPORTED)

    return (
        Command(
            span=Span.single(row.row_index, row.slots[0].slot_index, row.slots[-1].slot_index),
            origin=_origin((head,)),
            name=head.card_face,
            args=tuple(args),
        ),
        None,
    )


def _head_message(row_index: int, head: _Slot, role: str) -> str:
    if role == ROLE_LITERAL:
        return (
            f"行 {row_index} 的第 0 个槽位是字面卡 {head.card_face!r}，"
            "语句必须先有一张命令卡；该行整条丢弃"
        )
    if role == ROLE_CONTROL:
        return (
            f"行 {row_index} 槽位 {head.slot_index} 是控制流卡 {head.card_face!r}，"
            "P0 文法只有「语句序列 + 字面参数」，不支持控制流；该行整条丢弃"
        )
    if role == ROLE_OPERATOR:
        return (
            f"行 {row_index} 槽位 {head.slot_index} 是运算符卡 {head.card_face!r}，"
            "P0 只支持字面参数，不做表达式；该行整条丢弃"
        )
    return (
        f"行 {row_index} 槽位 {head.slot_index} 的卡面 {head.card_face!r} "
        "无法作为命令卡；该行整条丢弃"
    )


def _argument_diagnostic(row: _Row, slot: _Slot, code: str) -> dict[str, Any]:
    role = card_role(slot.card_face)
    if code == DIAG_EMPTY_SLOT:
        message = (
            f"行 {row.row_index} 槽位 {slot.slot_index} 的卡面 {slot.card_face!r} 为空或未知，"
            "无法作为参数；该行整条丢弃（期望一张字面数字卡）"
        )
    elif code == DIAG_OPERATOR_UNSUPPORTED:
        message = (
            f"行 {row.row_index} 槽位 {slot.slot_index} 是运算符卡 {slot.card_face!r}，"
            "P0 的参数只取字面数字卡，不做表达式；该行整条丢弃"
        )
    elif role == ROLE_CONTROL:
        message = (
            f"行 {row.row_index} 槽位 {slot.slot_index} 是控制流卡 {slot.card_face!r}，"
            "不能作为参数；P0 文法不支持控制流，该行整条丢弃"
        )
    else:
        message = (
            f"行 {row.row_index} 槽位 {slot.slot_index} 是命令卡 {slot.card_face!r}，"
            "参数位置只接受字面数字卡（一行一条语句）；该行整条丢弃"
        )
    return _diagnostic(code, message, row.row_index, (slot,))


def _program_span(rows: Sequence[_Row]) -> Span:
    """``Program`` 覆盖整个 ``Layout``：首行到末行、槽位 0 到最宽那行的最后一个槽位。"""
    if not rows:
        return Span.single(0, 0, 0)
    slot_to = 0
    for row in rows:
        if row.slots:
            slot_to = max(slot_to, row.slots[-1].slot_index)
    return Span.across(rows[0].row_index, 0, rows[-1].row_index, slot_to)


def _program_origin(body: Sequence[Command]) -> Origin:
    entries: list[tuple[str, str]] = []
    for command in body:
        entries.extend(command.origin.entries)
        for argument in command.args:
            entries.extend(argument.origin.entries)
    return Origin.many(entries)


def parse_rows(rows: Iterable[_Row], frame_id: str) -> Ast:
    """核心：按行解析并组装 ``Ast``（``rows`` 是内部视图，入口见下面两个函数）。"""
    ordered = tuple(rows)
    body: list[Command] = []
    diagnostics: list[dict[str, Any]] = []
    for row in ordered:
        statement, problem = _parse_row(row)
        if statement is not None:
            body.append(statement)
        if problem is not None:
            diagnostics.append(problem)
    root = Program(
        span=_program_span(ordered),
        origin=_program_origin(body),
        body=tuple(body),
    )
    return Ast(root=root, frame_id=str(frame_id), diagnostics=tuple(diagnostics))


def parse_layout_document(document: dict[str, Any]) -> Ast:
    """§4 ``Layout`` 文档 → §5 ``Ast``。文档本身不合法时抛 ``ValueError``。"""
    validate_layout(document)
    return parse_rows(_rows_from_document(document), str(document.get("frame_id", "")))


def parse_layout(layout: Layout) -> Ast:
    """LB-004 的 ``Layout`` 对象 → §5 ``Ast``（少量 JSON 侧字段如 ``basis`` 与语法无关）。"""
    return parse_rows(_rows_from_layout(layout), str(layout.frame_id))


__all__ = ["parse_layout", "parse_layout_document"]
