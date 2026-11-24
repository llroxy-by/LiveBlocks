"""语法层（LB-005）：卡片 ``Layout`` → 语法树 ``Ast``（`docs/PROTOCOL.md` §5）。

这一层只做「把排列好的卡片读成语句序列」，**不求值、不执行**（LB-006 起的活）：

- :mod:`liveblocks.parser.grammar` —— P0 文法（``grammar_version`` 0.0.1）的机器可读定义：
  产生式、卡面角色、诊断码，以及「``docs/GRAMMAR.md`` 是否写全了这些」的核对函数；
- :mod:`liveblocks.parser.ast` —— ``Ast`` / ``Program`` / ``Command`` / ``Number`` 的数据模型、
  ``span`` + ``origin`` 约定与 §5 的最小校验器；
- :func:`liveblocks.parser.parse_layout_document` / :func:`liveblocks.parser.parse_layout` ——
  两个现成入口（§4 JSON 文档 / LB-004 的 ``Layout`` 对象）；
- :func:`liveblocks.parser.build_report` —— 对 ``fixtures/ast`` 跑一遍并给出验收对照。

人类可读的文法定义在 ``docs/GRAMMAR.md``（§5 没写死的取舍见 ``docs/DECISIONS.md`` D-016），
协议文件本身未被改动（由仓库所有者维护）。
"""

from __future__ import annotations

from .ast import Ast, Command, Number, Origin, Program, Span, node_count, validate_ast
from .grammar import (
    BNF,
    CONTROL_FACES_AS_STATEMENT,
    DIAGNOSTICS_BY_CODE,
    DIAG_BAD_ARGUMENT,
    DIAG_CONTROL_UNSUPPORTED,
    DIAG_EMPTY_SLOT,
    DIAG_MISSING_COMMAND,
    DIAG_OPERATOR_UNSUPPORTED,
    DOC_PATH,
    GRAMMAR_VERSION,
    NODE_COMMAND,
    NODE_KINDS,
    NODE_NUMBER,
    NODE_PROGRAM,
    PARSER_CODES,
    ROLES,
    ROLE_BY_CARD_TYPE,
    ROLE_COMMAND,
    ROLE_CONTROL,
    ROLE_LITERAL,
    ROLE_OPERATOR,
    ROLE_UNKNOWN,
    card_role,
    card_types,
    faces_with_role,
    grammar_document_gaps,
    literal_value,
)
from .parse import parse_layout, parse_layout_document
from .report import (
    EXPECT_INVALID,
    EXPECT_VALID,
    FixtureAst,
    ParserReport,
    build_report,
    format_report,
    load_manifest,
)

__all__ = [
    "Ast",
    "BNF",
    "CONTROL_FACES_AS_STATEMENT",
    "Command",
    "DIAGNOSTICS_BY_CODE",
    "DIAG_BAD_ARGUMENT",
    "DIAG_CONTROL_UNSUPPORTED",
    "DIAG_EMPTY_SLOT",
    "DIAG_MISSING_COMMAND",
    "DIAG_OPERATOR_UNSUPPORTED",
    "DOC_PATH",
    "EXPECT_INVALID",
    "EXPECT_VALID",
    "FixtureAst",
    "GRAMMAR_VERSION",
    "NODE_COMMAND",
    "NODE_KINDS",
    "NODE_NUMBER",
    "NODE_PROGRAM",
    "Number",
    "Origin",
    "PARSER_CODES",
    "ParserReport",
    "Program",
    "ROLES",
    "ROLE_BY_CARD_TYPE",
    "ROLE_COMMAND",
    "ROLE_CONTROL",
    "ROLE_LITERAL",
    "ROLE_OPERATOR",
    "ROLE_UNKNOWN",
    "Span",
    "build_report",
    "card_role",
    "card_types",
    "faces_with_role",
    "format_report",
    "grammar_document_gaps",
    "literal_value",
    "load_manifest",
    "node_count",
    "parse_layout",
    "parse_layout_document",
    "validate_ast",
]
