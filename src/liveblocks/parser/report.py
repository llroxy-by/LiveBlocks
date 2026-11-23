"""语法层自检报告：把 ``fixtures/ast/`` 的 Layout 夹具全部解析一遍，输出可读摘要与验收对照。

与 LB-003 / LB-004 的报告同一思路：**证据来自真跑一遍**。每个夹具在 ``index.json``（清单）里
声明「期望合法还是非法、期望几条语句、期望哪些诊断码」，报告逐条核对，并把 §5 的结构校验
（``validate_ast``）与「每个节点都有 ``span`` + ``origin``」也当成硬条件。

清单格式见 ``fixtures/README.md`` §5；``expect`` 只允许 ``valid`` / ``invalid``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..layout import validate_layout
from .ast import Ast, node_count, validate_ast
from .grammar import DOC_PATH, PARSER_CODES, grammar_document_gaps
from .parse import parse_layout_document

#: 清单里允许的期望值。
EXPECT_VALID = "valid"
EXPECT_INVALID = "invalid"

#: 仓库根（源码布局 ``src/liveblocks/parser/report.py`` 往上四级）；安装成 wheel 时可能不成立，
#: 因此 ``build_report`` 允许用 ``doc_path`` 显式指定文法文档。
_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent


@dataclass(frozen=True)
class FixtureAst:
    """一个语法夹具的解析结果 + 校验结论。"""

    name: str
    path: Path
    expect: str
    expected_codes: tuple[str, ...]
    expected_statements: int
    ast: Ast | None = None
    validation_errors: tuple[str, ...] = ()
    parse_error: str = ""
    note: str = ""

    @property
    def codes(self) -> tuple[str, ...]:
        return () if self.ast is None else self.ast.codes()

    @property
    def statements(self) -> int:
        return 0 if self.ast is None else len(self.ast.root.body)

    @property
    def nodes(self) -> int:
        return 0 if self.ast is None else node_count(self.ast)

    @property
    def untraced_nodes(self) -> tuple[str, ...]:
        """缺少 ``span`` 或 ``origin`` 的节点（§5 要求每个节点都带）。"""
        if self.ast is None:
            return ()
        missing: list[str] = []
        for node in self.ast.nodes():
            kind = str(node.get("node", "?"))
            if "span" not in node:
                missing.append(f"{kind}:缺 span")
            if "origin" not in node:
                missing.append(f"{kind}:缺 origin")
        return tuple(missing)

    @property
    def located_diagnostics(self) -> tuple[str, ...]:
        """没有 ``span`` 或 ``origin`` 的诊断（本任务要求诊断定位到行/槽位/卡片）。"""
        if self.ast is None:
            return ()
        return tuple(
            str(entry.get("code", "?"))
            for entry in self.ast.diagnostics
            if "span" not in entry or "origin" not in entry
        )

    @property
    def outside_grammar_codes(self) -> tuple[str, ...]:
        """落在 ``LB-P0xx`` 之外的诊断码（语法层不该产出别的区间）。"""
        return tuple(code for code in self.codes if code not in PARSER_CODES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "expect": self.expect,
            "expected_codes": list(self.expected_codes),
            "expected_statements": self.expected_statements,
            "codes": list(self.codes),
            "statements": self.statements,
            "nodes": self.nodes,
            "validation_errors": list(self.validation_errors),
            "parse_error": self.parse_error,
            "note": self.note,
            "ast": None if self.ast is None else self.ast.to_dict(),
        }


@dataclass(frozen=True)
class ParserReport:
    """一组夹具的报告 + Issue #14 的验收对照。"""

    directory: Path
    fixtures: tuple[FixtureAst, ...]
    grammar_gaps: tuple[str, ...] = ()
    manifest_error: str = ""

    # -- 汇总 ---------------------------------------------------------------
    @property
    def valid_fixtures(self) -> list[FixtureAst]:
        return [fixture for fixture in self.fixtures if fixture.expect == EXPECT_VALID]

    @property
    def invalid_fixtures(self) -> list[FixtureAst]:
        return [fixture for fixture in self.fixtures if fixture.expect == EXPECT_INVALID]

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "manifest_error": self.manifest_error,
            "grammar_gaps": list(self.grammar_gaps),
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
            "acceptance": [
                {"ok": ok, "criterion": criterion, "evidence": evidence}
                for ok, criterion, evidence in self.acceptance()
            ],
        }

    # -- 验收对照 -----------------------------------------------------------
    def acceptance(self) -> list[tuple[bool, str, str]]:
        results: list[tuple[bool, str, str]] = []

        # 1. 合法夹具得到符合 §5 的 AST
        problems: list[str] = []
        for fixture in self.valid_fixtures:
            if fixture.parse_error:
                problems.append(f"{fixture.name}: 解析抛异常 {fixture.parse_error}")
                continue
            if fixture.validation_errors:
                problems.append(f"{fixture.name}: §5 校验失败 " + "；".join(fixture.validation_errors))
            if fixture.codes:
                problems.append(f"{fixture.name}: 合法夹具却产出诊断 {list(fixture.codes)}")
            if fixture.untraced_nodes:
                problems.append(f"{fixture.name}: 节点缺 span/origin {list(fixture.untraced_nodes)}")
            if fixture.statements != fixture.expected_statements:
                problems.append(
                    f"{fixture.name}: 语句数 {fixture.statements} 与清单 {fixture.expected_statements} 不符"
                )
        ok = bool(self.valid_fixtures) and not problems and not self.manifest_error
        detail = f"{len(self.valid_fixtures)} 个合法夹具的 Ast 均通过 §5 校验、节点都带 span+origin、无语法诊断"
        if problems:
            detail = "；".join(problems)
        elif not self.valid_fixtures:
            detail = "没有合法夹具（清单缺失或为空）"
        elif self.manifest_error:
            detail = self.manifest_error
        results.append((bool(ok), "合法样例得到符合 §5 的 Ast（每个节点带 span 与 origin）", detail))

        # 2. 非法夹具产出带 span / origin 的 LB-P0xx 诊断且不崩溃
        problems = []
        for fixture in self.invalid_fixtures:
            if fixture.parse_error:
                problems.append(f"{fixture.name}: 解析崩溃 {fixture.parse_error}")
                continue
            if fixture.validation_errors:
                problems.append(f"{fixture.name}: 输出的 Ast 未通过 §5 校验 " + "；".join(fixture.validation_errors))
            if fixture.outside_grammar_codes:
                problems.append(f"{fixture.name}: 出现 LB-P0xx 之外的诊断码 {list(fixture.outside_grammar_codes)}")
            if not fixture.codes:
                problems.append(f"{fixture.name}: 非法夹具没有任何诊断")
            if tuple(fixture.codes) != fixture.expected_codes:
                problems.append(
                    f"{fixture.name}: 诊断码 {list(fixture.codes)} 与清单 {list(fixture.expected_codes)} 不符"
                )
            if fixture.located_diagnostics:
                problems.append(
                    f"{fixture.name}: 诊断缺 span/origin {list(fixture.located_diagnostics)}"
                )
        ok = bool(self.invalid_fixtures) and not problems and not self.manifest_error
        detail = (
            f"{len(self.invalid_fixtures)} 个非法夹具都产出 LB-P0xx 诊断，"
            "每条诊断带 span（行+槽位）与 origin（来源卡片），解析不抛异常"
        )
        if problems:
            detail = "；".join(problems)
        elif not self.invalid_fixtures:
            detail = "没有非法夹具（清单缺失或为空）"
        elif self.manifest_error:
            detail = self.manifest_error
        results.append((bool(ok), "非法样例产出带 span 与 origin 的 LB-P0xx 诊断且不崩溃", detail))

        # 3. 文法定义有独立文档并可被测试引用
        doc_ok = not self.grammar_gaps
        detail = f"{DOC_PATH} 与 grammar.py 逐项一致（产生式 / 节点名 / {len(PARSER_CODES)} 个诊断码 / 全部卡面角色）"
        if self.grammar_gaps:
            detail = f"{DOC_PATH} 与 grammar.py 不一致：" + "；".join(self.grammar_gaps)
        results.append((bool(doc_ok), "文法定义有独立文档（docs/GRAMMAR.md）并可被测试引用", detail))

        return results


def load_manifest(directory: Path) -> dict[str, Any]:
    """读取夹具清单（``index.json``）；结构不对时抛 ``ValueError``。"""
    path = directory / "index.json"
    if not path.is_file():
        raise ValueError(f"缺少语法夹具清单：{path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("fixtures"), list):
        raise ValueError(f"{path} 必须是含 fixtures 数组的对象")
    return document


def _fixture_from_entry(directory: Path, entry: dict[str, Any]) -> FixtureAst:
    name = str(entry["name"])
    path = directory / name
    expect = str(entry.get("expect", ""))
    if expect not in (EXPECT_VALID, EXPECT_INVALID):
        raise ValueError(f"{name}: expect 必须是 {EXPECT_VALID!r} 或 {EXPECT_INVALID!r}，实际 {expect!r}")
    expected_codes = tuple(str(code) for code in entry.get("codes", []))
    expected_statements = int(entry.get("statements", 0))
    note = str(entry.get("note", ""))
    if not path.is_file():
        return FixtureAst(
            name=name,
            path=path,
            expect=expect,
            expected_codes=expected_codes,
            expected_statements=expected_statements,
            parse_error=f"找不到夹具文件 {path}",
            note=note,
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    try:
        validate_layout(document)
    except ValueError as error:  # 输入就不是合法 §4 文档：算夹具问题，不算语法诊断
        errors.append(f"输入不是合法 Layout：{error}")
        return FixtureAst(
            name=name,
            path=path,
            expect=expect,
            expected_codes=expected_codes,
            expected_statements=expected_statements,
            validation_errors=tuple(errors),
            parse_error="输入 Layout 不合法",
            note=note,
        )
    ast = None
    parse_error = ""
    try:
        ast = parse_layout_document(document)
    except Exception as error:  # noqa: BLE001 —— 报告要如实记录崩溃，而不是让报告本身崩掉
        parse_error = f"{type(error).__name__}: {error}"
    if ast is not None:
        try:
            validate_ast(ast.to_dict())
        except ValueError as error:
            errors.append(str(error))
    return FixtureAst(
        name=name,
        path=path,
        expect=expect,
        expected_codes=expected_codes,
        expected_statements=expected_statements,
        ast=ast,
        validation_errors=tuple(errors),
        parse_error=parse_error,
        note=note,
    )


def build_report(directory: Path, *, doc_path: Path | None = None) -> ParserReport:
    """对夹具目录跑一遍解析，并按 Issue #14 的三条验收给出结论。"""
    manifest_error = ""
    fixtures: list[FixtureAst] = []
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
    doc = doc_path if doc_path is not None else REPO_ROOT / DOC_PATH
    gaps: tuple[str, ...] = ()
    if not doc.is_file():
        gaps = (f"缺少文法文档 {doc}",)
    else:
        gaps = tuple(grammar_document_gaps(doc.read_text(encoding="utf-8")))
    return ParserReport(
        directory=directory,
        fixtures=tuple(fixtures),
        grammar_gaps=gaps,
        manifest_error=manifest_error,
    )


def format_report(report: ParserReport) -> str:
    """可读报告：每个夹具一行 + 验收对照 + 合计。"""
    lines: list[str] = [f"语法夹具目录 {report.directory}"]
    if report.manifest_error:
        lines.append(f"清单问题：{report.manifest_error}")
    for fixture in report.fixtures:
        expect = "合法" if fixture.expect == EXPECT_VALID else "非法"
        codes = ",".join(fixture.codes) or "-"
        status = "Ok" if _fixture_ok(fixture) else "FAIL"
        detail = fixture.parse_error or "；".join(fixture.validation_errors) or "§5 校验通过"
        lines.append(
            f"{status:<4} {fixture.name:<30} {expect}  语句 {fixture.statements}"
            f"  节点 {fixture.nodes}  诊断 {len(fixture.codes)} [{codes}]  {detail}"
        )
    lines.append("")
    lines.append("验收对照：")
    for ok, criterion, evidence in report.acceptance():
        lines.append(f"  [{'通过' if ok else '未通过'}] {criterion}")
        lines.append(f"          依据：{evidence}")
    total_nodes = sum(fixture.nodes for fixture in report.fixtures)
    lines.append("")
    lines.append(
        f"合计 {len(report.fixtures)} 个夹具（合法 {len(report.valid_fixtures)} / "
        f"非法 {len(report.invalid_fixtures)}）/ 语法树节点 {total_nodes} 个 / "
        f"文法文档缺口 {len(report.grammar_gaps)} 项"
    )
    return "\n".join(lines) + "\n"


def _fixture_ok(fixture: FixtureAst) -> bool:
    if fixture.parse_error or fixture.validation_errors:
        return False
    if fixture.untraced_nodes or fixture.located_diagnostics or fixture.outside_grammar_codes:
        return False
    if tuple(fixture.codes) != fixture.expected_codes:
        return False
    return fixture.statements == fixture.expected_statements


__all__ = [
    "EXPECT_INVALID",
    "EXPECT_VALID",
    "FixtureAst",
    "ParserReport",
    "build_report",
    "format_report",
    "load_manifest",
]
