"""端到端回归与运行记录（LB-009）：一条命令跑全链路并落盘 §8 记录。

对应 ``docs/ROADMAP.md`` 第 2 节的 LB-009：

- :func:`liveblocks.e2e.pipeline.run_chain` —— 生成 → 识别 → 排列 → 解析 → IR → 模拟，
  产物按 §8 写成 ``JSONL``（首行 ``RunHeader``，随后逐层对象与事件）；
- :func:`liveblocks.e2e.regression.build_report` —— 全场景回归：记录形状校验 + 黄金基准比对 +
  破坏注入自检 + 固定时间上界；
- 基准在 ``fixtures/e2e/baseline.json``，口径见 ``docs/E2E.md``。

**运行记录的生产在包内完成**，不依赖 LB-008 为夹具拼记录的 ``scripts/build_run_records.py``。
"""

from __future__ import annotations

from .baseline import (
    BASELINE_NAME,
    BaselineError,
    baseline_document,
    compare_run,
    load_baseline,
    write_baseline,
)
from .pipeline import (
    LAYER_CORRUPTIONS,
    SCENE_DIR_NAME,
    ChainError,
    ChainRun,
    StageOutput,
    run_chain,
)
from .protocol import (
    CODE_BASELINE_MISMATCH,
    CODE_BASELINE_MISSING,
    CODE_BAD_EVENTS,
    CODE_BAD_FIRST_LINE,
    CODE_BAD_FRAME,
    CODE_BAD_HEADER,
    CODE_BAD_LAYER_FIELDS,
    CODE_BAD_LINE,
    CODE_CORRUPT_NOT_CAUGHT,
    CODE_MISSING_LAYER,
    CODE_ORDER,
    CODE_TIME_BOUND,
    CODE_TITLES,
    CODE_UNKNOWN_KIND,
    CORRUPT_TARGETS,
    DEFAULT_SEED,
    E2E_CODES,
    E2E_VERSION,
    RECORD_BOUND_MS,
    REQUIRED_KINDS,
    SCHEMA_VERSION,
    STAGE_KINDS,
    STAGE_LABELS,
    STAGE_OF_KIND,
    STAGES,
    TIME_BOUND_MS,
    E2eIssue,
    issue,
    validate_record,
)
from .docs import DOC_PATH, e2e_document_gaps
from .record import fingerprint, read_documents, record_text, write_record
from .regression import (
    DEFAULT_CORRUPT_PROBE,
    DEFAULT_DIR,
    DEFAULT_OUT,
    FIXTURE_SCENE_DIR,
    CorruptionCheck,
    RegressionReport,
    SceneResult,
    build_report,
    failure_summary,
    fixture_matches,
    format_report,
    root_layer,
)

__all__ = [
    "BASELINE_NAME",
    "BaselineError",
    "baseline_document",
    "compare_run",
    "load_baseline",
    "write_baseline",
    "LAYER_CORRUPTIONS",
    "SCENE_DIR_NAME",
    "ChainError",
    "ChainRun",
    "StageOutput",
    "run_chain",
    "CODE_BASELINE_MISMATCH",
    "CODE_BASELINE_MISSING",
    "CODE_BAD_EVENTS",
    "CODE_BAD_FIRST_LINE",
    "CODE_BAD_FRAME",
    "CODE_BAD_HEADER",
    "CODE_BAD_LAYER_FIELDS",
    "CODE_BAD_LINE",
    "CODE_CORRUPT_NOT_CAUGHT",
    "CODE_MISSING_LAYER",
    "CODE_ORDER",
    "CODE_TIME_BOUND",
    "CODE_TITLES",
    "CODE_UNKNOWN_KIND",
    "CORRUPT_TARGETS",
    "DEFAULT_SEED",
    "E2E_CODES",
    "E2E_VERSION",
    "RECORD_BOUND_MS",
    "REQUIRED_KINDS",
    "SCHEMA_VERSION",
    "STAGE_KINDS",
    "STAGE_LABELS",
    "STAGE_OF_KIND",
    "STAGES",
    "TIME_BOUND_MS",
    "E2eIssue",
    "issue",
    "validate_record",
    "DOC_PATH",
    "e2e_document_gaps",
    "fingerprint",
    "read_documents",
    "record_text",
    "write_record",
    "DEFAULT_CORRUPT_PROBE",
    "DEFAULT_DIR",
    "FIXTURE_SCENE_DIR",
    "fixture_matches",
    "DEFAULT_OUT",
    "CorruptionCheck",
    "RegressionReport",
    "SceneResult",
    "build_report",
    "failure_summary",
    "format_report",
    "root_layer",
]
