"""规范化层（LB-006）：``Ast`` → 规范化 ``Ir``（`docs/PROTOCOL.md` §6）。

这一层只做「把语法树折成确定性、可回归、可跨语言消费的执行计划」，**不执行**（那是 LB-007 起）：

- :mod:`liveblocks.ir.ops` —— ``ir_version`` 0.0.1 的 **op 表**（卡面 → ``op`` + 参数名 + 元数 +
  单位）、``limits`` 缺省值、``LB-I0xx`` 诊断码，以及「``docs/IR.md`` 是否写全了这些」的核对函数；
- :mod:`liveblocks.ir.schema` —— 入库的 JSON Schema（``src/liveblocks/data/ir_schema.json``）与
  零依赖的最小 schema 校验器；
- :mod:`liveblocks.ir.protocol` —— ``Ir`` / ``IrStep`` / ``IrLimits`` 的数据模型、确定性序列化
  （``canonical_text``）与校验（``validate_ir``：schema + 语义）；
- :func:`liveblocks.ir.normalize_ast_document` / :func:`liveblocks.ir.normalize` —— 两个现成入口
  （§5 JSON 文档 / LB-005 的 ``Ast`` 对象）；
- :func:`liveblocks.ir.build_report` —— 对 ``fixtures/ir`` 跑一遍并给出验收对照。

人类可读的规范在 ``docs/IR.md``（§6 没写死的取舍见 ``docs/DECISIONS.md`` D-017），
协议文件本身未被改动（由仓库所有者维护）。
"""

from __future__ import annotations

from .normalize import normalize, normalize_ast_document, normalize_document, resolve_limits
from .ops import (
    DIAGNOSTICS_BY_CODE,
    DIAG_ARITY,
    DIAG_NON_LITERAL_ARG,
    DIAG_UNKNOWN_OP,
    DIAG_UNSUPPORTED_STATEMENT,
    DOC_PATH,
    IR_CODES,
    IR_VERSION,
    LIMIT_DEFAULTS,
    LIMIT_KEYS,
    LIMIT_MINIMUM,
    OP_BY_FACE,
    OP_BY_NAME,
    OP_SPECS,
    OpSpec,
    Param,
    SCHEMA_FILENAME,
    STEP_ID_PATTERN,
    ir_document_gaps,
    op_spec,
    op_table_gaps,
)
from .protocol import Ir, IrLimits, IrStep, canonical_text, document_sha256, validate_ir
from .report import FixtureIr, IrReport, build_report, format_report, load_manifest
from .schema import (
    SUPPORTED_KEYWORDS,
    SchemaError,
    load_schema,
    schema_unsupported_keywords,
    validate_document,
)

__all__ = [
    "DIAGNOSTICS_BY_CODE",
    "DIAG_ARITY",
    "DIAG_NON_LITERAL_ARG",
    "DIAG_UNKNOWN_OP",
    "DIAG_UNSUPPORTED_STATEMENT",
    "DOC_PATH",
    "FixtureIr",
    "IR_CODES",
    "IR_VERSION",
    "Ir",
    "IrLimits",
    "IrReport",
    "IrStep",
    "LIMIT_DEFAULTS",
    "LIMIT_KEYS",
    "LIMIT_MINIMUM",
    "OP_BY_FACE",
    "OP_BY_NAME",
    "OP_SPECS",
    "OpSpec",
    "Param",
    "SCHEMA_FILENAME",
    "STEP_ID_PATTERN",
    "SUPPORTED_KEYWORDS",
    "SchemaError",
    "build_report",
    "canonical_text",
    "document_sha256",
    "format_report",
    "ir_document_gaps",
    "load_manifest",
    "load_schema",
    "normalize",
    "normalize_ast_document",
    "normalize_document",
    "op_spec",
    "op_table_gaps",
    "resolve_limits",
    "schema_unsupported_keywords",
    "validate_document",
    "validate_ir",
]
