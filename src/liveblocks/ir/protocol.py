"""S5 ``Ir``（`docs/PROTOCOL.md` §6）的数据模型、确定性序列化与校验。

§6 的四句约定在这里落地：

- **确定性**：所有字典按固定顺序构造，数字只有整数，没有时间戳与随机量；:meth:`Ir.canonical_text`
  是唯一的落盘/比对形式（``ensure_ascii=False``、``indent=2``、不排序键、结尾一个换行），
  因此「同一 AST 规范化两次 → 字节一致」可以逐字节断言。
- **``limits`` 必须显式**：``program.limits`` 永远存在，缺省值取 :data:`LIMIT_DEFAULTS`（§6 示例那
  两个数），由规范化器填充，见 :mod:`liveblocks.ir.normalize`。
- **``op`` 只能来自文法定义**：校验时会查 :mod:`liveblocks.ir.ops` 的 op 表，未知 ``op`` 直接判为
  非法文档（它本该在规范化阶段变成 ``LB-I001``）。
- **``step_id``**：密集、按产出顺序，``s0`` / ``s1`` / …

``validate_ir`` 分两层：先按入库的 JSON Schema（:mod:`liveblocks.ir.schema`）核形状，再做 schema
表达不了的语义检查（op 与 args 的对应、``step_id`` 是否连续、诊断码是否落在 ``LB-I0xx``）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..vision.protocol import SCHEMA_VERSION, VERSION_PATTERN, validate_diagnostics
from .ops import (
    IR_CODES,
    IR_VERSION,
    LIMIT_KEYS,
    LIMIT_MINIMUM,
    STEP_ID_PATTERN,
    op_spec,
)
from .schema import SchemaError, load_schema, validate_document

#: ``Ir.kind``（§6）。
KIND_IR = "Ir"


@dataclass(frozen=True)
class IrLimits:
    """§6 ``program.limits``：执行器的硬约束，规范化后必须显式出现。"""

    max_steps: int
    timeout_ms: int

    def to_dict(self) -> dict[str, int]:
        return {"max_steps": int(self.max_steps), "timeout_ms": int(self.timeout_ms)}


@dataclass(frozen=True)
class IrStep:
    """§6 ``program.steps[]``：一步 = 一条语句的规范化结果。"""

    step_id: str
    op: str
    args: dict[str, Any]
    source: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "op": self.op,
            "args": dict(self.args),
            "source": {"row": int(self.source["row"]), "slot": int(self.source["slot"])},
        }


@dataclass(frozen=True)
class Ir:
    """§6 ``Ir``：规范化后的程序 + 显式 ``limits`` + 诊断。"""

    steps: tuple[IrStep, ...]
    limits: IrLimits
    diagnostics: tuple[dict[str, Any], ...] = ()
    ir_version: str = IR_VERSION
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": KIND_IR,
            "ir_version": self.ir_version,
            "program": {
                "steps": [step.to_dict() for step in self.steps],
                "limits": self.limits.to_dict(),
            },
            "diagnostics": [dict(entry) for entry in self.diagnostics],
        }

    def canonical_text(self) -> str:
        """唯一的落盘/比对形式：固定缩进、不排序键、非 ASCII 原样、结尾一个换行。"""
        return canonical_text(self.to_dict())

    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_text().encode("utf-8")).hexdigest()

    def codes(self) -> tuple[str, ...]:
        return tuple(str(entry.get("code", "")) for entry in self.diagnostics)

    def diagnostics_with(self, code: str) -> tuple[dict[str, Any], ...]:
        return tuple(entry for entry in self.diagnostics if entry.get("code") == code)

    @property
    def ops(self) -> tuple[str, ...]:
        return tuple(step.op for step in self.steps)


def canonical_text(document: dict[str, Any]) -> str:
    """把文档序列化成**可逐字节比对**的文本（§6 的确定性要求）。"""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def document_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_step(step: Any, where: str) -> None:
    """语义检查：``op`` 必须在 op 表里，``args`` 必须与登记的元数/参数名/单位一致。"""
    if not isinstance(step, dict):
        raise ValueError(f"{where}: step 必须是对象")
    step_id = step.get("step_id")
    if not isinstance(step_id, str) or STEP_ID_PATTERN.fullmatch(step_id) is None:
        raise ValueError(f"{where}: step_id 必须形如 s0 / s1 / …，实际 {step_id!r}")
    op = step.get("op")
    spec = op_spec(op) if isinstance(op, str) else None
    if spec is None:
        raise ValueError(f"{where}: op {op!r} 不在 P0 的 op 表里（见 docs/IR.md §2）")
    args = step.get("args")
    if not isinstance(args, dict):
        raise ValueError(f"{where}: args 必须是对象")
    if spec.arity == 0:
        if args:
            raise ValueError(f"{where}: op {spec.op} 不收参数，实际 args={args!r}")
    else:
        expected = {param.name for param in spec.params} | {"unit"}
        if set(args) != expected:
            raise ValueError(
                f"{where}: op {spec.op} 的 args 字段必须是 {sorted(expected)}，实际 {sorted(args)}"
            )
        for param in spec.params:
            value = args[param.name]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{where}: args.{param.name} 必须是整数，实际 {value!r}")
            if value < 0:
                raise ValueError(f"{where}: args.{param.name} 不能为负，实际 {value!r}")
        if args["unit"] != spec.params[0].unit:
            raise ValueError(
                f"{where}: op {spec.op} 的 unit 必须是 {spec.params[0].unit!r}，实际 {args['unit']!r}"
            )
    source = step.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"{where}: source 必须是对象")
    for key in ("row", "slot"):
        value = source.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{where}: source.{key} 必须是整数，实际 {value!r}")
        if value < 0:
            raise ValueError(f"{where}: source.{key} 不能为负，实际 {value!r}")


def validate_ir(document: dict[str, Any]) -> None:
    """校验 ``Ir``：先按入库 schema 核形状，再做 schema 表达不了的语义检查。"""
    if not isinstance(document, dict):
        raise ValueError("Ir 必须是对象")
    try:
        validate_document(document, load_schema())
    except SchemaError as error:
        raise ValueError(f"IR 不符合 schema：{error}") from error

    if document.get("kind") != KIND_IR:
        raise ValueError(f"kind 必须是 {KIND_IR!r}，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]' 字符串，实际 {version!r}")
    if document.get("ir_version") != IR_VERSION:
        raise ValueError(f"ir_version 必须是 {IR_VERSION!r}，实际 {document.get('ir_version')!r}")

    program = document.get("program")
    if not isinstance(program, dict):
        raise ValueError("program 必须是对象")
    steps = program.get("steps")
    if not isinstance(steps, list):
        raise ValueError("program.steps 必须是数组")
    for position, step in enumerate(steps):
        where = f"program.steps[{position}]"
        _check_step(step, where)
        expected_id = f"s{position}"
        if step["step_id"] != expected_id:
            raise ValueError(
                f"{where}: step_id 必须是 {expected_id!r}（按产出顺序密集编号），实际 {step['step_id']!r}"
            )

    limits = program.get("limits")
    if not isinstance(limits, dict):
        raise ValueError("program.limits 必须是对象（§6：规范化后必须显式出现）")
    for key in LIMIT_KEYS:
        if key not in limits:
            raise ValueError(f"program.limits 缺少字段 {key!r}")
        value = limits[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"program.limits.{key} 必须是整数，实际 {value!r}")
        if value < LIMIT_MINIMUM:
            raise ValueError(f"program.limits.{key} 必须 ≥ {LIMIT_MINIMUM}，实际 {value!r}")

    diagnostics = document.get("diagnostics")
    validate_diagnostics(diagnostics)
    for position, entry in enumerate(diagnostics):
        code = str(entry.get("code", ""))
        if code not in IR_CODES:
            raise ValueError(
                f"diagnostics[{position}]: 诊断码 {code!r} 不在规范化区间 {list(IR_CODES)} 里"
            )
        if "span" in entry and not isinstance(entry["span"], dict):
            raise ValueError(f"diagnostics[{position}]: span 必须是对象")


__all__ = [
    "Ir",
    "IrLimits",
    "IrStep",
    "KIND_IR",
    "canonical_text",
    "document_sha256",
    "validate_ir",
]
