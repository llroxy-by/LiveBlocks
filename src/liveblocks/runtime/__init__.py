"""执行层（LB-007）：``Ir`` → ``ExecEvent[]`` + ``ExecResult``，虚拟时钟 + ``limits`` + 中断。

这一层**只做模拟**（``backend: "sim"``），不接硬件、不做界面：

- :mod:`liveblocks.runtime.semantics` —— ``exec_version`` 0.0.1 的**执行语义表**（每个 ``op`` 的虚拟
  耗时与状态影响）、虚拟时钟换算（1 tick = 10 ms）、``LB-R0xx`` 诊断码，以及「``docs/EXEC.md`` 是否
  写全了这些」的核对函数；
- :mod:`liveblocks.runtime.protocol` —— ``ExecEvent``（§7.1）/ ``ExecResult``（§7.2）的数据模型、加法式
  的 ``ExecState``（最终状态）与校验/序列化；
- :class:`liveblocks.runtime.SimExecutor` —— 模拟后端：虚拟时钟、来自 ``Ir.limits`` 的步数/超时限制、
  步骤边界生效的中断；
- :func:`liveblocks.runtime.build_report` —— 对 ``fixtures/exec`` 真跑一遍并给出验收对照。

人类可读的规范在 ``docs/EXEC.md``（§7 没写死的取舍见 ``docs/DECISIONS.md`` D-018），协议文件本身
未被改动（由仓库所有者维护）。**模拟事件永远标 ``backend: "sim"``，不得当作硬件验证证据**。
"""

from __future__ import annotations

from .protocol import (
    KIND_EXEC_EVENT,
    KIND_EXEC_RESULT,
    KIND_EXEC_STATE,
    canonical_text,
    events_jsonl,
    exec_event,
    exec_result,
    exec_state,
    validate_event_stream,
    validate_exec_event,
    validate_exec_result,
    validate_exec_state,
)
from .report import (
    NON_TERMINATING_STATUSES,
    PER_RUN_WALL_MS_BOUND,
    REPORT_WALL_MS_BOUND,
    ExecReport,
    FixtureRun,
    build_report,
    format_report,
    load_manifest,
    run_fixture,
)
from .semantics import (
    BACKENDS,
    BACKEND_SIM,
    CLOCK_MS_PER_TICK,
    DIAGNOSTICS_BY_CODE,
    DIAG_ABORTED,
    DIAG_EMPTY_HANDS,
    DIAG_RUNTIME_FAULT,
    DIAG_STEP_LIMIT,
    DIAG_TIMEOUT,
    DOC_PATH,
    EXEC_CODES,
    EXEC_VERSION,
    FACINGS,
    FACING_VECTORS,
    OP_SEMANTICS,
    PHASES,
    RESULT_STATUSES,
    SEMANTICS_BY_OP,
    STATUS_CODES,
    OpSemantic,
    runtime_document_gaps,
    semantic_for,
    step_ticks,
    ticks_to_ms,
)
from .sim import (
    DEFAULT_RUN_ID,
    AbortHook,
    SimExecutor,
    SimRun,
    WorldFault,
    WorldState,
    apply_step,
    ir_from_document,
    run_ir,
    validate_run,
)

__all__ = [
    "BACKENDS",
    "BACKEND_SIM",
    "CLOCK_MS_PER_TICK",
    "DEFAULT_RUN_ID",
    "DIAGNOSTICS_BY_CODE",
    "DIAG_ABORTED",
    "DIAG_EMPTY_HANDS",
    "DIAG_RUNTIME_FAULT",
    "DIAG_STEP_LIMIT",
    "DIAG_TIMEOUT",
    "DOC_PATH",
    "EXEC_CODES",
    "EXEC_VERSION",
    "FACINGS",
    "FACING_VECTORS",
    "KIND_EXEC_EVENT",
    "KIND_EXEC_RESULT",
    "KIND_EXEC_STATE",
    "NON_TERMINATING_STATUSES",
    "OP_SEMANTICS",
    "PER_RUN_WALL_MS_BOUND",
    "PHASES",
    "REPORT_WALL_MS_BOUND",
    "RESULT_STATUSES",
    "SEMANTICS_BY_OP",
    "STATUS_CODES",
    "AbortHook",
    "ExecReport",
    "FixtureRun",
    "OpSemantic",
    "SimExecutor",
    "SimRun",
    "WorldFault",
    "WorldState",
    "apply_step",
    "build_report",
    "ir_from_document",
    "canonical_text",
    "events_jsonl",
    "exec_event",
    "exec_result",
    "exec_state",
    "format_report",
    "load_manifest",
    "run_fixture",
    "run_ir",
    "runtime_document_gaps",
    "semantic_for",
    "step_ticks",
    "ticks_to_ms",
    "validate_event_stream",
    "validate_exec_event",
    "validate_exec_result",
    "validate_exec_state",
    "validate_run",
]
