"""S4 ``Ast`` → S5 ``Ir``（`docs/PROTOCOL.md` §6）的**确定性规范化**。

规则（完整定义见 ``docs/IR.md``，本模块只是它的执行部分）：

- **一条 ``Command`` = 一步 ``step``**：``op`` 取卡面（``Command.name``），参数按 :mod:`liveblocks.ir.ops`
  的登记折成 ``{"<参数名>": <整数>, "unit": "<单位>"}``，来源写成 ``{"row", "slot"}``（取 ``span`` 的
  起始行与起始槽位，与 §6 示例一致）。
- **``step_id`` 密集编号**：``s0`` / ``s1`` / …，按**产出的**步骤顺序，与其它层的 ``row_index`` /
  ``slot_index`` 一致（D-015 起就强制连续）。
- **``limits`` 必须显式**：``Command`` 里没有 ``limits`` 概念，因此由规范化器填
  :data:`liveblocks.ir.ops.LIMIT_DEFAULTS`；调用方可以显式覆盖（CLI 的 ``--max-steps`` /
  ``--timeout-ms``），覆盖值也写进结果，绝不留空。
- **一条语句出问题 = 一条诊断 + 该语句不进 IR**（沿用语法层 D-016 第 7 条「整行丢弃、只报一条」的
  保守策略）：未知 ``op`` 报 ``LB-I001``，参数个数不符报 ``LB-I002``，语句节点不是 ``Command``
  报 ``LB-I003``，参数不是字面量报 ``LB-I004``。**不做截断、不补零、不猜默认参数**——猜出来的
  语义下游没法追溯。
- **不复制上游诊断**：``Ast.diagnostics``（``LB-P0xx``）属于语法层，``Ir.diagnostics`` 只放
  ``LB-I0xx``；一次运行的各层对象都会进运行记录（§8），信息不丢。

``normalize_ast_document`` 先做 §5 校验：输入不是合法 ``Ast`` 时抛 ``ValueError``（输入坏了不是
规范化错误，不该伪装成 ``LB-I0xx``），与 ``parse_layout_document`` 的口径一致。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..parser.ast import Ast
from ..parser.grammar import NODE_COMMAND, NODE_NUMBER
from .ops import (
    DIAG_ARITY,
    DIAG_NON_LITERAL_ARG,
    DIAG_UNKNOWN_OP,
    DIAG_UNSUPPORTED_STATEMENT,
    LIMIT_DEFAULTS,
    LIMIT_KEYS,
    LIMIT_MINIMUM,
    op_spec,
)
from .protocol import Ir, IrLimits, IrStep


def resolve_limits(
    max_steps: int | None = None,
    timeout_ms: int | None = None,
) -> IrLimits:
    """把 ``limits`` 定下来：没给的取 §6 示例里的缺省值，给了就必须是正整数。"""
    values = {"max_steps": max_steps, "timeout_ms": timeout_ms}
    resolved: dict[str, int] = {}
    for key in LIMIT_KEYS:
        value = values.get(key)
        if value is None:
            resolved[key] = int(LIMIT_DEFAULTS[key])
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"limits.{key} 必须是整数，实际 {value!r}")
        if value < LIMIT_MINIMUM:
            raise ValueError(f"limits.{key} 必须 ≥ {LIMIT_MINIMUM}，实际 {value!r}")
        resolved[key] = int(value)
    return IrLimits(max_steps=resolved["max_steps"], timeout_ms=resolved["timeout_ms"])


def _location(node: Mapping[str, Any]) -> tuple[int, int]:
    """语句节点在 ``Layout`` 里的位置：``span`` 的起始行 + 起始槽位。"""
    span = node.get("span")
    if not isinstance(span, Mapping):
        return 0, 0
    row = span.get("row", span.get("row_from", 0))
    slot = span.get("slot_from", 0)
    if not isinstance(row, int) or isinstance(row, bool):
        row = 0
    if not isinstance(slot, int) or isinstance(slot, bool):
        slot = 0
    return int(row), int(slot)


def _candidate_ids(node: Mapping[str, Any]) -> list[str]:
    origin = node.get("origin")
    if not isinstance(origin, Mapping):
        return []
    if isinstance(origin.get("candidate_id"), str):
        return [str(origin["candidate_id"])]
    ids = origin.get("candidate_ids")
    if isinstance(ids, list):
        return [str(item) for item in ids]
    return []


def _diagnostic(
    code: str,
    message: str,
    row: int,
    slot: int,
    candidate_ids: Iterable[str] = (),
) -> dict[str, Any]:
    """按 §9 的形状造诊断，并补上 ``span``（行 + 槽位，指回 AST 节点）。"""
    entry: dict[str, Any] = {"code": code, "severity": "error", "message": message}
    ids = [str(item) for item in candidate_ids]
    if ids:
        entry["candidate_ids"] = ids
    entry["span"] = {"row": row, "slot_from": slot, "slot_to": slot}
    return entry


def _literal_value(node: Any) -> int | None:
    """参数节点能取到的整数值；不是 ``Number`` 或值不是整数时返回 ``None``。"""
    if not isinstance(node, Mapping) or node.get("node") != NODE_NUMBER:
        return None
    value = node.get("value")
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return int(value)


def _statement_at(node: Any) -> tuple[str | None, int, int, list[str]]:
    """返回 ``(node 名, 行, 槽位, 候选 id)``，供诊断定位用。"""
    if not isinstance(node, Mapping):
        return None, 0, 0, []
    name = node.get("node")
    row, slot = _location(node)
    return (str(name) if isinstance(name, str) else None), row, slot, _candidate_ids(node)


def _step_from_statement(
    node: Mapping[str, Any],
) -> tuple[IrStep | None, dict[str, Any] | None, str]:
    """一条语句 → ``(step, 诊断, op)``。``op`` 在成功时用来拼 ``step`` 之外的说明。"""
    name, row, slot, ids = _statement_at(node)
    where = f"行 {row} 槽位 {slot}"

    if name != NODE_COMMAND:
        return (
            None,
            _diagnostic(
                DIAG_UNSUPPORTED_STATEMENT,
                f"{where} 的语句节点是 {name!r}，本实现只把 Command 规范化成 step"
                "（§5 的 node 是开放枚举）；该语句不进 IR",
                row,
                slot,
                ids,
            ),
            "",
        )

    command_name = node.get("name")
    spec = op_spec(command_name) if isinstance(command_name, str) else None
    if spec is None:
        return (
            None,
            _diagnostic(
                DIAG_UNKNOWN_OP,
                f"{where} 的命令卡 {command_name!r} 不在 P0 的 op 表里"
                "（docs/IR.md §2：op 只能取文法定义里的命令卡面，控制流卡不在 P0）；该语句不进 IR",
                row,
                slot,
                ids,
            ),
            "",
        )

    arguments = node.get("args")
    if not isinstance(arguments, list):
        arguments = []
    for position, argument in enumerate(arguments):
        if _literal_value(argument) is None:
            return (
                None,
                _diagnostic(
                    DIAG_NON_LITERAL_ARG,
                    f"{where} 的命令卡 {spec.op!r} 第 {position} 个参数不是字面数字卡"
                    "（P0 只把 Number 折成整数参数）；该语句不进 IR",
                    row,
                    slot,
                    ids,
                ),
                "",
            )
    if len(arguments) != spec.arity:
        return (
            None,
            _diagnostic(
                DIAG_ARITY,
                f"{where} 的命令卡 {spec.op!r} 期望 {spec.arity} 个参数，实际 {len(arguments)} 个"
                "（docs/IR.md §2 的元数表）；该语句不进 IR，不做截断或补零",
                row,
                slot,
                ids,
            ),
            "",
        )

    args: dict[str, Any] = {}
    for param, argument in zip(spec.params, arguments):
        args[param.name] = _literal_value(argument)
    if spec.params:
        args["unit"] = spec.params[0].unit
    step = IrStep(step_id="s0", op=spec.op, args=args, source={"row": row, "slot": slot})
    return step, None, spec.op


def normalize_document(
    document: Mapping[str, Any],
    *,
    max_steps: int | None = None,
    timeout_ms: int | None = None,
) -> Ir:
    """把一份**已通过 §5 校验**的 ``Ast`` 文档规范化成 ``Ir``。"""
    limits = resolve_limits(max_steps=max_steps, timeout_ms=timeout_ms)
    root = document.get("root")
    body = root.get("body") if isinstance(root, Mapping) else None
    statements = body if isinstance(body, list) else []

    steps: list[IrStep] = []
    diagnostics: list[dict[str, Any]] = []
    for statement in statements:
        step, problem, _op = _step_from_statement(statement) if isinstance(statement, Mapping) else (
            None,
            _diagnostic(
                DIAG_UNSUPPORTED_STATEMENT,
                "Program.body 里有不是对象的语句节点；该语句不进 IR",
                0,
                0,
            ),
            "",
        )
        if problem is not None:
            diagnostics.append(problem)
            continue
        assert step is not None  # 上一步已经把 problem 与 step 二选一
        steps.append(
            IrStep(
                step_id=f"s{len(steps)}",
                op=step.op,
                args=dict(step.args),
                source=dict(step.source),
            )
        )
    return Ir(steps=tuple(steps), limits=limits, diagnostics=tuple(diagnostics))


def normalize_ast_document(
    document: dict[str, Any],
    *,
    max_steps: int | None = None,
    timeout_ms: int | None = None,
) -> Ir:
    """§5 ``Ast`` 文档 → §6 ``Ir``。文档本身不合法时抛 ``ValueError``。"""
    from ..parser.ast import validate_ast

    validate_ast(document)
    return normalize_document(document, max_steps=max_steps, timeout_ms=timeout_ms)


def normalize(
    ast: Ast,
    *,
    max_steps: int | None = None,
    timeout_ms: int | None = None,
) -> Ir:
    """LB-005 的 ``Ast`` 对象 → ``Ir``（等价于 ``normalize_ast_document(ast.to_dict())``）。"""
    return normalize_document(ast.to_dict(), max_steps=max_steps, timeout_ms=timeout_ms)


__all__ = ["normalize", "normalize_ast_document", "normalize_document", "resolve_limits"]
