"""规范化自检报告：把 ``fixtures/ir/`` 的 ``Ast`` 夹具全部规范化一遍，输出可读摘要与验收对照。

与 LB-003 / LB-004 / LB-005 的报告同一思路：**证据来自真跑一遍**。每个夹具在 ``index.json``
（清单）里声明「期望几步、哪些 ``op``、哪些诊断码、``limits`` 是多少」，报告逐条核对，并把
Issue #16 的四条可观察验收（字节一致、未知 ``op`` 报 ``LB-I001``、通过 schema 校验、golden
快照一致）当成硬条件；``limits`` 显式化的范围项作为第五条。

清单格式见 ``fixtures/README.md`` §6。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..parser.ast import validate_ast
from .ops import DOC_PATH, IR_CODES, ir_document_gaps, op_table_gaps
from .protocol import Ir, validate_ir
from .normalize import normalize_ast_document
from .schema import SchemaError, load_schema, schema_unsupported_keywords, validate_document

#: 仓库根（源码布局 ``src/liveblocks/ir/report.py`` 往上四级）；安装成 wheel 时可能不成立，
#: 因此 ``build_report`` 允许用 ``doc_path`` 显式指定 IR 文档。
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent


@dataclass(frozen=True)
class FixtureIr:
    """一个 IR 夹具的规范化结果 + 各项结论。"""

    name: str
    ast_path: Path
    golden_path: Path
    expected_steps: int
    expected_ops: tuple[str, ...]
    expected_codes: tuple[str, ...]
    expected_limits: dict[str, int]
    source: str = ""
    note: str = ""
    ir: Ir | None = None
    canonical: str = ""
    golden_text: str = ""
    stable: bool = False
    schema_errors: tuple[str, ...] = ()
    normalize_error: str = ""
    ast_error: str = ""

    @property
    def steps(self) -> int:
        return 0 if self.ir is None else len(self.ir.steps)

    @property
    def ops(self) -> tuple[str, ...]:
        return () if self.ir is None else self.ir.ops

    @property
    def codes(self) -> tuple[str, ...]:
        return () if self.ir is None else self.ir.codes()

    @property
    def limits(self) -> dict[str, int]:
        return {} if self.ir is None else self.ir.limits.to_dict()

    @property
    def golden_match(self) -> bool:
        return bool(self.canonical) and self.canonical == self.golden_text

    @property
    def outside_ir_codes(self) -> tuple[str, ...]:
        return tuple(code for code in self.codes if code not in IR_CODES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ast": str(self.ast_path),
            "golden": str(self.golden_path),
            "expected": {
                "steps": self.expected_steps,
                "ops": list(self.expected_ops),
                "codes": list(self.expected_codes),
                "limits": dict(self.expected_limits),
            },
            "actual": {
                "steps": self.steps,
                "ops": list(self.ops),
                "codes": list(self.codes),
                "limits": self.limits,
                "sha256": "" if self.ir is None else self.ir.sha256(),
                "stable": self.stable,
                "golden_match": self.golden_match,
            },
            "schema_errors": list(self.schema_errors),
            "normalize_error": self.normalize_error,
            "ast_error": self.ast_error,
            "source": self.source,
            "note": self.note,
            "ir": None if self.ir is None else self.ir.to_dict(),
        }


@dataclass(frozen=True)
class IrReport:
    """一组夹具的报告 + Issue #16 的验收对照。"""

    directory: Path
    fixtures: tuple[FixtureIr, ...]
    schema_unsupported: tuple[str, ...] = ()
    op_gaps: tuple[str, ...] = ()
    doc_gaps: tuple[str, ...] = ()
    manifest_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "manifest_error": self.manifest_error,
            "schema_unsupported": list(self.schema_unsupported),
            "op_gaps": list(self.op_gaps),
            "doc_gaps": list(self.doc_gaps),
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
        }

    # -- 验收对照 -----------------------------------------------------------
    def acceptance(self) -> list[tuple[bool, str, str]]:
        results: list[tuple[bool, str, str]] = []
        usable = [fixture for fixture in self.fixtures if fixture.ir is not None]

        # 1. 同一 AST 规范化两次输出字节一致
        problems = [fixture.name for fixture in usable if not fixture.stable]
        problems += [fixture.name for fixture in self.fixtures if fixture.ir is None]
        ok = bool(usable) and not problems
        detail = (
            f"{len(usable)} 个夹具各自规范化两次，字节全等（--json 报告里逐夹具给出 sha256，可逐字节复核）"
        )
        if problems:
            detail = "这些夹具两次规范化结果不一致或没跑出 Ir：" + "、".join(problems)
        results.append((ok, "同一 AST 规范化两次输出字节一致", detail))

        # 2. 未知 op 报 LB-I001
        named = [fixture for fixture in self.fixtures if "LB-I001" in fixture.expected_codes]
        problems = []
        for fixture in named:
            if "LB-I001" not in fixture.codes:
                problems.append(f"{fixture.name}: 期望 LB-I001，实际 {list(fixture.codes)}")
        for fixture in self.fixtures:
            if tuple(fixture.codes) != fixture.expected_codes:
                problems.append(
                    f"{fixture.name}: 诊断码 {list(fixture.codes)} 与清单 {list(fixture.expected_codes)} 不符"
                )
            if fixture.outside_ir_codes:
                problems.append(f"{fixture.name}: 出现 LB-I0xx 之外的诊断码 {list(fixture.outside_ir_codes)}")
        ok = bool(named) and not problems
        detail = (
            f"{len(named)} 个夹具含未知 op，都产出 LB-I001（并对该语句不进 IR 留了诊断）"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "未知 op 报 LB-I001", detail))

        # 3. IR 通过 schema 校验
        problems = [f"{fixture.name}: {error}" for fixture in usable for error in fixture.schema_errors]
        ok = bool(usable) and not problems and not self.schema_unsupported
        detail = (
            f"{len(usable)} 份 Ir 全部通过入库 schema 与语义校验"
            "（src/liveblocks/data/ir_schema.json + validate_ir）"
        )
        if problems:
            detail = "；".join(problems)
        elif self.schema_unsupported:
            detail = "schema 用了本校验器不支持的关键字：" + "、".join(self.schema_unsupported)
        results.append((ok, "IR 通过 schema 校验", detail))

        # 4. golden 文件快照一致
        problems = [fixture.name for fixture in self.fixtures if not fixture.golden_match]
        ok = bool(self.fixtures) and not problems
        detail = f"{len(self.fixtures)} 个夹具的输出与入库 golden 逐字节一致"
        if problems:
            detail = "这些夹具与 golden 不一致：" + "、".join(problems)
        elif not self.fixtures:
            detail = "没有夹具（清单缺失或为空）"
        results.append((ok, "golden 文件快照测试通过", detail))

        # 5. limits 显式出现（Issue #16 范围项「limits 显式化」）
        problems = []
        for fixture in self.fixtures:
            if fixture.ir is None:
                problems.append(f"{fixture.name}: 没跑出 Ir，无法核对 limits")
            elif fixture.limits != fixture.expected_limits:
                problems.append(
                    f"{fixture.name}: limits {fixture.limits} 与清单 {fixture.expected_limits} 不符"
                )
        ok = bool(self.fixtures) and not problems
        defaults = self.fixtures[0].expected_limits if self.fixtures else {}
        detail = (
            f"{len(self.fixtures)} 个夹具的 IR 都带显式 limits，缺省 {defaults}"
            if not problems
            else "；".join(problems)
        )
        results.append((ok, "limits 显式化（缺省值由规范化器填充）", detail))

        return results


def load_manifest(directory: Path) -> dict[str, Any]:
    """读取夹具清单（``index.json``）；结构不对时抛 ``ValueError``。"""
    path = directory / "index.json"
    if not path.is_file():
        raise ValueError(f"缺少 IR 夹具清单：{path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("fixtures"), list):
        raise ValueError(f"{path} 必须是含 fixtures 数组的对象")
    return document


def _fixture_from_entry(directory: Path, entry: dict[str, Any]) -> FixtureIr:
    name = str(entry["name"])
    ast_path = directory / str(entry["ast"])
    golden_path = directory / str(entry["golden"])
    expected_limits = {str(key): int(value) for key, value in dict(entry.get("limits", {})).items()}
    fixture = FixtureIr(
        name=name,
        ast_path=ast_path,
        golden_path=golden_path,
        expected_steps=int(entry.get("steps", 0)),
        expected_ops=tuple(str(op) for op in entry.get("ops", [])),
        expected_codes=tuple(str(code) for code in entry.get("codes", [])),
        expected_limits=expected_limits,
        source=str(entry.get("source", "")),
        note=str(entry.get("note", "")),
    )

    golden_text = ""
    if golden_path.is_file():
        golden_text = golden_path.read_text(encoding="utf-8")
    else:
        return _replace(fixture, ast_error=f"找不到 golden 文件 {golden_path}")

    if not ast_path.is_file():
        return _replace(fixture, ast_error=f"找不到 Ast 夹具 {ast_path}")

    try:
        document = json.loads(ast_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return _replace(fixture, ast_error=f"{ast_path} 不是合法 JSON：{error}")
    try:
        validate_ast(document)
    except ValueError as error:
        return _replace(fixture, ast_error=f"输入不是合法 Ast：{error}")

    try:
        first = normalize_ast_document(document)
        second = normalize_ast_document(document)
    except Exception as error:  # noqa: BLE001 —— 报告要如实记录崩溃，而不是让报告本身崩掉
        return _replace(
            fixture,
            golden_text=golden_text,
            normalize_error=f"{type(error).__name__}: {error}",
        )

    canonical = first.canonical_text()
    errors: list[str] = []
    try:
        validate_document(first.to_dict(), load_schema())
    except SchemaError as error:
        errors.append(str(error))
    try:
        validate_ir(first.to_dict())
    except ValueError as error:
        errors.append(str(error))
    return _replace(
        fixture,
        ir=first,
        canonical=canonical,
        golden_text=golden_text,
        stable=canonical == second.canonical_text(),
        schema_errors=tuple(errors),
    )


def _replace(fixture: FixtureIr, **changes: Any) -> FixtureIr:
    from dataclasses import replace

    return replace(fixture, **changes)


def build_report(directory: Path, *, doc_path: Path | None = None) -> IrReport:
    """对夹具目录跑一遍规范化，并按 Issue #16 的验收给出结论。"""
    manifest_error = ""
    fixtures: list[FixtureIr] = []
    try:
        manifest = load_manifest(directory)
    except ValueError as error:
        manifest_error = str(error)
        manifest = {"fixtures": []}
    for entry in manifest["fixtures"]:
        try:
            fixtures.append(_fixture_from_entry(directory, entry))
        except (ValueError, KeyError, TypeError) as error:
            manifest_error = f"清单条目有问题：{error}"
            break

    try:
        schema = load_schema()
        unsupported = tuple(schema_unsupported_keywords(schema))
    except Exception as error:  # noqa: BLE001
        schema = {}
        unsupported = (f"schema 读不出来：{error}",)
    doc = doc_path if doc_path is not None else REPO_ROOT / DOC_PATH
    if not doc.is_file():
        doc_gaps: tuple[str, ...] = (f"缺少 IR 文档 {doc}",)
    else:
        doc_gaps = tuple(ir_document_gaps(doc.read_text(encoding="utf-8")))
    return IrReport(
        directory=directory,
        fixtures=tuple(fixtures),
        schema_unsupported=unsupported,
        op_gaps=tuple(op_table_gaps()),
        doc_gaps=doc_gaps,
        manifest_error=manifest_error,
    )


def format_report(report: IrReport) -> str:
    """可读报告：每个夹具一行 + 验收对照 + 合计。"""
    lines: list[str] = [f"IR 夹具目录 {report.directory}"]
    if report.manifest_error:
        lines.append(f"清单问题：{report.manifest_error}")
    for fixture in report.fixtures:
        codes = ",".join(fixture.codes) or "-"
        ops = ",".join(fixture.ops) or "-"
        status = "Ok" if _fixture_ok(fixture) else "FAIL"
        detail = fixture.ast_error or fixture.normalize_error
        if not detail:
            detail = "；".join(fixture.schema_errors) or (
                "golden 一致" if fixture.golden_match else "golden 不一致"
            )
        lines.append(
            f"{status:<4} {fixture.name:<28} 步骤 {fixture.steps}"
            f"  诊断 {len(fixture.codes)} [{codes}]  op [{ops}]  {detail}"
        )
    lines.append("")
    lines.append("验收对照：")
    for ok, criterion, evidence in report.acceptance():
        lines.append(f"  [{'通过' if ok else '未通过'}] {criterion}")
        lines.append(f"          依据：{evidence}")
    lines.append("")
    total_steps = sum(fixture.steps for fixture in report.fixtures)
    total_codes = sum(len(fixture.codes) for fixture in report.fixtures)
    lines.append(
        f"合计 {len(report.fixtures)} 个夹具 / IR 步骤 {total_steps} 个 / 诊断 {total_codes} 条 / "
        f"op 表缺口 {len(report.op_gaps)} 项 / 文档缺口 {len(report.doc_gaps)} 项"
    )
    return "\n".join(lines) + "\n"


def _fixture_ok(fixture: FixtureIr) -> bool:
    if fixture.ast_error or fixture.normalize_error or fixture.schema_errors:
        return False
    if not fixture.stable or not fixture.golden_match:
        return False
    if tuple(fixture.codes) != fixture.expected_codes:
        return False
    if fixture.ops != fixture.expected_ops or fixture.steps != fixture.expected_steps:
        return False
    return fixture.limits == fixture.expected_limits


__all__ = [
    "FixtureIr",
    "IrReport",
    "build_report",
    "format_report",
    "load_manifest",
]
