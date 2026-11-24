"""端到端回归（LB-009）的常量、六阶段模型、记录形状校验与 ``LB-E0xx`` 码表。

``docs/PROTOCOL.md`` §8 只规定「首行 ``RunHeader``，随后按产出顺序写各层对象与事件」，
没有定义：``RunHeader`` 的必填字段、**生成层**（LB-002 的合成卡片图像）怎么落进记录、
记录里各层的必填字段、以及「记录不合格」与「回归失败」怎么报（§9 只有各层的 ``LB-*`` 码，
没有端到端回归层的码）。本模块按 §10 规则 1 做**加法式**补充，并把缺口写进
``docs/E2E.md`` 与 ``docs/DECISIONS.md``（D-020），不改 ``docs/PROTOCOL.md``：

- 六阶段固定为 ``synth → vision → layout → parser → ir → exec``，与
  ``docs/ROADMAP.md`` LB-009 的「生成 → 识别 → 排列 → 解析 → IR → 模拟」一一对应；
- **生成层没有 §8 的 ``kind``**（§1 的 S1 是 ``Frame``，属于采集侧），所以它的证据放在
  ``RunHeader`` 里（``frame`` 的帧引用 + 加法式 ``generation`` 块 + ``timings_ms.synth``）：
  记录里不新增 line ``kind``，这样 LB-008 的调试视图不需要改一行代码就能读；
- 回归层的问题码用 ``LB-E0xx`` 区间（E = End-to-end），形状沿用 §9 的诊断对象；
- 「与黄金基准不一致」与「超出固定时间上界」都是**回归失败**，不是记录损坏，但仍用同一
  套诊断对象输出，便于机读。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: 本层实现的 ``schema_version``（与 §0 的 ``major.minor`` 形状一致）。
SCHEMA_VERSION = "0.0.1"

#: 端到端回归层的版本（``docs/E2E.md`` 引用它）。
E2E_VERSION = "0.0.1"

#: 入库夹具用的随机种子（与 ``liveblocks.cli`` 的 ``DEFAULT_SEED`` 同一个值）。
DEFAULT_SEED = 20261123

KIND_RUN_HEADER = "RunHeader"
KIND_MARKER = "MarkerCandidateList"
KIND_CARD = "CardCandidateList"
KIND_LAYOUT = "Layout"
KIND_AST = "Ast"
KIND_IR = "Ir"
KIND_EXEC_EVENT = "ExecEvent"
KIND_EXEC_RESULT = "ExecResult"

#: 链路六阶段，顺序即执行顺序（生成 → 识别 → 排列 → 解析 → IR → 模拟）。
STAGES = ("synth", "vision", "layout", "parser", "ir", "exec")

#: 阶段的中文标签（报告与失败摘要里原样显示）。
STAGE_LABELS = {
    "synth": "生成（synth）",
    "vision": "识别（vision）",
    "layout": "排列（layout）",
    "parser": "解析（parser）",
    "ir": "IR（ir）",
    "exec": "模拟执行（exec）",
}

#: 每个阶段在记录里留下哪些 ``kind``（``synth`` 无：见模块 docstring）。
STAGE_KINDS = {
    "synth": (),
    "vision": (KIND_MARKER, KIND_CARD),
    "layout": (KIND_LAYOUT,),
    "parser": (KIND_AST,),
    "ir": (KIND_IR,),
    "exec": (KIND_EXEC_EVENT, KIND_EXEC_RESULT),
}

#: ``kind`` → 阶段（反查用）。
STAGE_OF_KIND = {
    kind: stage for stage, kinds in STAGE_KINDS.items() for kind in kinds
}

#: 记录行里 ``kind`` 的期望顺序（``ExecEvent`` 在 ``ExecResult`` 之前，可出现 0..n 次）。
KIND_RANK = {
    KIND_MARKER: 0,
    KIND_CARD: 1,
    KIND_LAYOUT: 2,
    KIND_AST: 3,
    KIND_IR: 4,
    KIND_EXEC_EVENT: 5,
    KIND_EXEC_RESULT: 6,
}

#: 缺了就一定失败的五种层对象（与 LB-008 的 ``REQUIRED_KINDS`` 一致）。
#: ``ExecEvent`` 不在其中：空程序的模拟执行也会留 ``ExecResult``，但事件可以只有 1 条起步行。
REQUIRED_KINDS = (KIND_CARD, KIND_LAYOUT, KIND_AST, KIND_IR, KIND_EXEC_RESULT)

#: ``RunHeader`` 的必填字段（§8 的 run_id/帧引用/版本 + 本层加法式的 ``generation``）。
HEADER_REQUIRED_FIELDS = (
    "schema_version",
    "kind",
    "run_id",
    "backend",
    "scene",
    "frame",
    "generation",
    "timings_ms",
    "created_with",
)

#: 层文档的必填字段（够判「这一层产出没产出、是不是空壳」，不求穷尽各层自校验）。
LAYER_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    KIND_MARKER: ("schema_version", "kind", "frame_id", "markers"),
    KIND_CARD: ("schema_version", "kind", "frame_id", "cards"),
    KIND_LAYOUT: ("schema_version", "kind", "frame_id", "rows"),
    KIND_AST: ("schema_version", "kind", "root"),
    KIND_IR: ("schema_version", "kind", "program"),
    KIND_EXEC_EVENT: ("schema_version", "kind", "run_id", "seq", "at_ms", "phase", "backend"),
    KIND_EXEC_RESULT: ("schema_version", "kind", "run_id", "status", "steps_executed"),
}

#: 整份回归的固定时间上界（毫秒）。见 ``docs/E2E.md`` §5。
TIME_BOUND_MS = 60_000.0

#: 单条记录（一个场景跑完六阶段）的固定时间上界（毫秒）。
RECORD_BOUND_MS = 20_000.0

#: 可以注入的破坏目标：``none`` 不注入、``scene-image`` 破坏生成出来的图像、
#: 其余阶段名破坏该阶段的记录文档。只用于证明「回归不是假通过」。
CORRUPT_TARGETS = ("none", "scene-image") + STAGES[1:]

# --- LB-E0xx 码表 ---------------------------------------------------------------

CODE_BAD_FIRST_LINE = "LB-E001"
CODE_MISSING_LAYER = "LB-E002"
CODE_ORDER = "LB-E003"
CODE_BAD_HEADER = "LB-E004"
CODE_UNKNOWN_KIND = "LB-E005"
CODE_BAD_LAYER_FIELDS = "LB-E006"
CODE_BAD_EVENTS = "LB-E007"
CODE_BAD_LINE = "LB-E008"
CODE_BAD_FRAME = "LB-E009"
CODE_BASELINE_MISMATCH = "LB-E010"
CODE_TIME_BOUND = "LB-E011"
CODE_CORRUPT_NOT_CAUGHT = "LB-E012"
CODE_BASELINE_MISSING = "LB-E013"

E2E_CODES = (
    CODE_BAD_FIRST_LINE,
    CODE_MISSING_LAYER,
    CODE_ORDER,
    CODE_BAD_HEADER,
    CODE_UNKNOWN_KIND,
    CODE_BAD_LAYER_FIELDS,
    CODE_BAD_EVENTS,
    CODE_BAD_LINE,
    CODE_BAD_FRAME,
    CODE_BASELINE_MISMATCH,
    CODE_TIME_BOUND,
    CODE_CORRUPT_NOT_CAUGHT,
    CODE_BASELINE_MISSING,
)

CODE_TITLES = {
    CODE_BAD_FIRST_LINE: "首行不是 RunHeader",
    CODE_MISSING_LAYER: "记录缺层（某阶段没有对应的 kind）",
    CODE_ORDER: "kind 顺序不对或层对象重复",
    CODE_BAD_HEADER: "RunHeader 缺必填字段或不合法",
    CODE_UNKNOWN_KIND: "出现 §8 与 §1 都没有的 kind",
    CODE_BAD_LAYER_FIELDS: "层文档缺必填字段",
    CODE_BAD_EVENTS: "事件流不合法（seq 不严格递增或 ExecResult 不在最后）",
    CODE_BAD_LINE: "记录里的行不是合法 JSON 对象",
    CODE_BAD_FRAME: "RunHeader.frame.image_ref 指向的图像找不到",
    CODE_BASELINE_MISMATCH: "与黄金基准不一致",
    CODE_TIME_BOUND: "超出固定时间上界",
    CODE_CORRUPT_NOT_CAUGHT: "破坏注入没有被回归抓到（回归是假通过）",
    CODE_BASELINE_MISSING: "黄金基准缺这个场景",
}

SEVERITIES = ("error", "warning", "info")


@dataclass(frozen=True)
class E2eIssue:
    """一条端到端回归层的问题（形状沿用 §9 的诊断对象）。"""

    code: str
    severity: str
    message: str
    line: int = 0
    layer: str = ""
    scene: str = ""

    def describe(self) -> str:
        """一行式描述：``LB-E010 error [normal/layout] 第 8 行: …``。"""
        where = "/".join(item for item in (self.scene, self.layer) if item) or "整体"
        line = f" 第 {self.line} 行" if self.line else ""
        return f"{self.code} {self.severity} [{where}]{line}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "line": int(self.line),
            "layer": self.layer,
            "scene": self.scene,
        }


def issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    line: int = 0,
    layer: str = "",
    scene: str = "",
) -> E2eIssue:
    """构造一条问题；``code`` 必须是 :data:`E2E_CODES` 里的码，``severity`` 必须已定义。"""
    if code not in E2E_CODES:
        raise ValueError(f"未知的 LB-E 码：{code}")
    if severity not in SEVERITIES:
        raise ValueError(f"未知的严重级：{severity}")
    if layer and layer not in STAGES:
        raise ValueError(f"未知的阶段：{layer}")
    return E2eIssue(code=code, severity=severity, message=message, line=line, layer=layer, scene=scene)


# --- 记录形状校验 ---------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def validate_record(
    documents: list[dict[str, Any]],
    *,
    scene: str = "",
    image_exists: Callable[[str], bool] | None = None,
) -> list[E2eIssue]:
    """按 §8 + 本层加法式约定校验一段运行记录（已解析成字典列表）。

    ``image_exists`` 给了才检查 ``RunHeader.frame.image_ref`` 是否解析得到（``LB-E009``）；
    不给就不做文件系统访问（纯函数，便于测试）。

    返回的问题按「记录顺序」排列；空列表表示这份记录合格。本函数**不抛异常**：
    坏记录只产出问题，调用方决定是失败还是继续。
    """
    issues: list[E2eIssue] = []
    if not documents:
        issues.append(
            issue(CODE_BAD_FIRST_LINE, "记录是空的（§8 要求首行是 RunHeader）", scene=scene)
        )
        return issues

    header = documents[0]
    if not isinstance(header, dict) or header.get("kind") != KIND_RUN_HEADER:
        kind = header.get("kind") if isinstance(header, dict) else type(header).__name__
        issues.append(
            issue(
                CODE_BAD_FIRST_LINE,
                f"首行的 kind 是 {kind!r}，§8 要求首行是 RunHeader",
                scene=scene,
            )
        )
    else:
        issues.extend(_validate_header(header, scene=scene, image_exists=image_exists))

    body = documents[1:]
    seen: dict[str, int] = {}
    previous_rank = -1
    for index, document in enumerate(body, start=2):
        if not isinstance(document, dict):
            issues.append(
                issue(
                    CODE_BAD_LAYER_FIELDS,
                    f"第 {index} 行不是 JSON 对象（{type(document).__name__}）",
                    line=index,
                    scene=scene,
                )
            )
            continue
        kind = document.get("kind")
        if kind not in KIND_RANK:
            issues.append(
                issue(
                    CODE_UNKNOWN_KIND,
                    f"第 {index} 行的 kind 是 {kind!r}，不在 §8 的层对象里",
                    line=index,
                    layer=_layer_of_known_kind(document.get("kind")),
                    scene=scene,
                )
            )
            continue
        stage = STAGE_OF_KIND[kind]
        # 层对象每层只出现一次；``ExecEvent`` 是事件流，本来就会出现很多行，
        # ``seen`` 里只记它的首次出现（用于「缺层」判定与「ExecResult 必须在最后」）。
        if kind in seen and kind != KIND_EXEC_EVENT:
            issues.append(
                issue(
                    CODE_ORDER,
                    f"第 {index} 行的 {kind} 与第 {seen[kind]} 行重复（每层对象只应出现一次）",
                    line=index,
                    layer=stage,
                    scene=scene,
                )
            )
        if kind not in seen:
            seen[kind] = index
        rank = KIND_RANK[kind]
        if rank < previous_rank:
            issues.append(
                issue(
                    CODE_ORDER,
                    f"第 {index} 行的 {kind} 出现在更晚的层之后（§8 要求按产出顺序写）",
                    line=index,
                    layer=stage,
                    scene=scene,
                )
            )
        previous_rank = max(previous_rank, rank)
        required = LAYER_REQUIRED_FIELDS.get(kind, ())
        missing = [field for field in required if field not in document]
        if missing:
            issues.append(
                issue(
                    CODE_BAD_LAYER_FIELDS,
                    f"第 {index} 行的 {kind} 缺字段：{', '.join(missing)}",
                    line=index,
                    layer=stage,
                    scene=scene,
                )
            )
        if kind == KIND_EXEC_RESULT and index != len(documents):
            issues.append(
                issue(
                    CODE_BAD_EVENTS,
                    f"第 {index} 行的 ExecResult 后面还有 {len(documents) - index} 行（ExecResult 必须是最后一行）",
                    line=index,
                    layer=stage,
                    scene=scene,
                )
            )

    for kind in REQUIRED_KINDS:
        if kind not in seen:
            issues.append(
                issue(
                    CODE_MISSING_LAYER,
                    f"记录里没有 {kind}（阶段 {STAGE_LABELS[STAGE_OF_KIND[kind]]} 没有产出）",
                    layer=STAGE_OF_KIND[kind],
                    scene=scene,
                )
            )

    issues.extend(_validate_events(body, scene=scene))
    return issues


def _layer_of_known_kind(kind: Any) -> str:
    if isinstance(kind, str) and kind in STAGE_OF_KIND:
        return STAGE_OF_KIND[kind]
    return ""


def _validate_header(
    header: dict[str, Any],
    *,
    scene: str,
    image_exists: Callable[[str], bool] | None,
) -> list[E2eIssue]:
    issues: list[E2eIssue] = []
    # ``frame`` / ``generation`` / ``timings_ms`` 下面各有更具体的检查，这里不重复报。
    detailed = {"frame", "generation", "timings_ms"}
    missing = [field for field in HEADER_REQUIRED_FIELDS if field not in header and field not in detailed]
    if missing:
        issues.append(
            issue(
                CODE_BAD_HEADER,
                f"RunHeader 缺字段：{', '.join(missing)}",
                line=1,
                scene=scene,
            )
        )
    for field in ("run_id", "backend", "scene"):
        value = header.get(field)
        if not isinstance(value, str) or not value:
            issues.append(
                issue(CODE_BAD_HEADER, f"RunHeader.{field} 不是非空字符串", line=1, scene=scene)
            )

    frame = header.get("frame")
    frame_ref = frame if isinstance(frame, dict) else {}
    if not frame_ref:
        issues.append(issue(CODE_BAD_HEADER, "RunHeader.frame 缺失", line=1, layer="synth", scene=scene))
    else:
        for field in ("frame_id", "image_ref", "width", "height", "source"):
            if field not in frame_ref:
                issues.append(
                    issue(
                        CODE_BAD_HEADER,
                        f"RunHeader.frame 缺 {field}",
                        line=1,
                        layer="vision",
                        scene=scene,
                    )
                )
        if image_exists is not None:
            image_ref = frame_ref.get("image_ref")
            if isinstance(image_ref, str) and image_ref and not image_exists(image_ref):
                issues.append(
                    issue(
                        CODE_BAD_FRAME,
                        f"RunHeader.frame.image_ref={image_ref!r} 指向的图像找不到",
                        line=1,
                        layer="vision",
                        scene=scene,
                    )
                )

    generation = header.get("generation")
    if not isinstance(generation, dict):
        issues.append(
            issue(
                CODE_BAD_HEADER,
                "RunHeader.generation 缺失（生成层没有 §8 的 kind，证据只能放在这里）",
                line=1,
                layer="synth",
                scene=scene,
            )
        )
    else:
        for field in ("stage", "image_sha256", "ground_truth_ref", "ground_truth_sha256"):
            if field not in generation:
                issues.append(
                    issue(
                        CODE_BAD_HEADER,
                        f"RunHeader.generation 缺 {field}",
                        line=1,
                        layer="synth",
                        scene=scene,
                    )
                )

    timings = header.get("timings_ms")
    if not isinstance(timings, dict):
        issues.append(issue(CODE_BAD_HEADER, "RunHeader.timings_ms 缺失", line=1, scene=scene))
    else:
        for stage in STAGES:
            value = timings.get(stage)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                issues.append(
                    issue(
                        CODE_BAD_HEADER,
                        f"RunHeader.timings_ms.{stage} 缺失或不是非负数",
                        line=1,
                        layer=stage,
                        scene=scene,
                    )
                )
    return issues


def _validate_events(body: list[Any], *, scene: str) -> list[E2eIssue]:
    """事件 ``seq`` 必须严格递增；``ExecEvent`` 出现的阶段记作 ``exec``。"""
    issues: list[E2eIssue] = []
    previous = 0
    for index, document in enumerate(body, start=2):
        if not isinstance(document, dict) or document.get("kind") != KIND_EXEC_EVENT:
            continue
        seq = _as_int(document.get("seq"))
        if seq is None:
            issues.append(
                issue(
                    CODE_BAD_EVENTS,
                    f"第 {index} 行的 ExecEvent.seq 不是整数",
                    line=index,
                    layer="exec",
                    scene=scene,
                )
            )
            continue
        if seq <= previous:
            issues.append(
                issue(
                    CODE_BAD_EVENTS,
                    f"第 {index} 行的 ExecEvent.seq={seq} 没有严格大于上一条（{previous}）",
                    line=index,
                    layer="exec",
                    scene=scene,
                )
            )
        previous = max(previous, seq)
    return issues


__all__ = [
    "SCHEMA_VERSION",
    "E2E_VERSION",
    "DEFAULT_SEED",
    "KIND_RUN_HEADER",
    "KIND_MARKER",
    "KIND_CARD",
    "KIND_LAYOUT",
    "KIND_AST",
    "KIND_IR",
    "KIND_EXEC_EVENT",
    "KIND_EXEC_RESULT",
    "STAGES",
    "STAGE_LABELS",
    "STAGE_KINDS",
    "STAGE_OF_KIND",
    "KIND_RANK",
    "REQUIRED_KINDS",
    "HEADER_REQUIRED_FIELDS",
    "LAYER_REQUIRED_FIELDS",
    "TIME_BOUND_MS",
    "RECORD_BOUND_MS",
    "CORRUPT_TARGETS",
    "E2E_CODES",
    "CODE_TITLES",
    "CODE_BAD_FIRST_LINE",
    "CODE_MISSING_LAYER",
    "CODE_ORDER",
    "CODE_BAD_HEADER",
    "CODE_UNKNOWN_KIND",
    "CODE_BAD_LAYER_FIELDS",
    "CODE_BAD_EVENTS",
    "CODE_BAD_LINE",
    "CODE_BAD_FRAME",
    "CODE_BASELINE_MISMATCH",
    "CODE_TIME_BOUND",
    "CODE_CORRUPT_NOT_CAUGHT",
    "CODE_BASELINE_MISSING",
    "SEVERITIES",
    "E2eIssue",
    "issue",
    "validate_record",
]
