"""S6 模拟执行层的语义表（``exec_version`` 0.0.1）：虚拟时钟、``op`` 的耗时与状态影响、``LB-R0xx``。

`docs/PROTOCOL.md` §7 只给了 ``ExecEvent`` / ``ExecResult`` 的形状与三句约定（``phase`` / ``backend``
枚举、``seq`` 严格递增、``status`` 枚举），**没有**定义：

- 虚拟时钟怎么走（``at_ms`` 是相对运行起点的毫秒数，但一步走多少毫秒没说）；
- 每个 ``op`` 消耗多少虚拟时间、对状态做什么；
- ``limits``（``max_steps`` / ``timeout_ms``）的判定顺序与边界（恰好用完算不算超）；
- ``§9`` 的 ``LB-R0xx`` 只举了 ``LB-R002``（步数上限）与 ``LB-R003``（超时）两个例子；
- 中断（``aborted``）怎么触发、怎么留痕。

本模块把这些写死成机器可读的表，人类可读的同一份在 ``docs/EXEC.md``：:func:`runtime_document_gaps`
用同一份表核对文档是否写全，``liveblocks exec report`` 与 ``tests/test_exec_docs.py`` 都会调它，
因此「文档与实现一致」是可回归的（与 LB-004/LB-005/LB-006 同一做法）。

**保守读法（`docs/DECISIONS.md` D-018）**：

1. **P0 是立即执行**（Q5 的一半答案）：一步做完就写事件，不做求值图、不做并行、不做回溯。
   ``Ir`` 是扁平步骤序列，没有控制流（D-016 第 1 条），因此「死循环」在 P0 里只有两种等价形态：
   步骤数超过 ``max_steps``、虚拟耗时超过 ``timeout_ms``。两者都由 ``limits`` 兜住，**不会挂死**。
2. **虚拟时钟不睡真实时间**：``at_ms = tick × CLOCK_MS_PER_TICK``，``wait 100000`` 只是把虚拟
   时钟往前拨，真实耗时与步数同阶（毫秒级）。这正是「虚拟时钟」的意义：超时判定可测、可复现。
3. **``tick`` / ``cell`` / ``quarter_turn`` 仍然只是标签**（``docs/IR.md`` §9 缺口 5、Q5 未定），
   本层只给出「1 tick = 10 ms」这一条内部约定与每步的 tick 数，物理换算不给。
4. 世界状态是**最小网格模型**（``pos`` / ``facing`` / ``holding`` / ``jumps``），只为让事件的
   ``detail.state_delta`` 有内容、让最终状态 JSON 可读；**不是**产品语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 执行层规范的版本。改语义表、时钟换算、``limits`` 判定或 ``LB-R0xx`` 码表必须提升它，
#: 并在 ``docs/DECISIONS.md`` 记一条。
EXEC_VERSION = "0.0.1"

#: ``docs/EXEC.md`` 的位置（相对仓库根），``runtime_document_gaps`` 的输入是它的正文。
DOC_PATH = "docs/EXEC.md"

#: 虚拟时钟的刻度换算：1 tick = 10 ms（虚拟毫秒，与墙钟无关）。
#:
#: §7.1 只说 ``at_ms`` 是「相对运行起点的毫秒数」，没说一步走多久；本实现取 10 ms/tick，使
#: ``wait`` 的虚拟耗时等于 ``duration × 10 ms``，且所有 ``at_ms`` 都是 10 的整数倍（浮点可精确表示）。
CLOCK_MS_PER_TICK = 10.0

#: 事件里 ``backend`` 的取值（§7.1）。本层只产出 ``sim``：**任何模拟结果都不得标成 ``hw``**。
BACKEND_SIM = "sim"
BACKENDS: tuple[str, ...] = ("sim", "hw")

#: 事件的 ``phase`` 取值（§7.1）。
PHASES: tuple[str, ...] = ("start", "finish", "error", "log", "feedback")

#: ``ExecResult.status`` 的取值（§7.2）。
RESULT_STATUSES: tuple[str, ...] = (
    "ok",
    "parse_error",
    "runtime_error",
    "timeout",
    "aborted",
    "limit_exceeded",
)

#: 世界状态的朝向序列（逆时针，与 ``turn-left`` 同向）。
FACINGS: tuple[str, ...] = ("north", "east", "south", "west")

#: 朝向 → 网格位移（``y`` 向上为正，是**网格坐标**不是图像像素，见 ``docs/EXEC.md`` §2.2）。
FACING_VECTORS: dict[str, tuple[int, int]] = {
    "north": (0, 1),
    "east": (1, 0),
    "south": (0, -1),
    "west": (-1, 0),
}


@dataclass(frozen=True)
class OpSemantic:
    """一个 ``op`` 的执行语义：虚拟耗时（tick）与状态影响。

    ``ticks`` 为 ``None`` 表示耗时由参数决定（只有 ``wait``），此时由 ``duration_arg`` 指定字段名。
    ``effect`` 是**状态影响的短描述**，与 ``docs/EXEC.md`` §2.1 的表格逐字核对。
    """

    op: str
    ticks: int | None
    effect: str
    description: str = ""
    duration_arg: str | None = None

    @property
    def fixed_ticks(self) -> bool:
        return self.ticks is not None


#: P0 的执行语义表：覆盖 ``docs/IR.md`` §2 op 表的全部 8 个命令。
#:
#: 耗时取「最小可解释值」：移动 2 tick（走过去要多于原地转一下）、转向/跳跃/拿放各 1 tick、
#: ``wait`` 就是它自己写的 ``duration``。``effect`` 的写法**逐字**出现在 ``docs/EXEC.md`` 的表格里。
OP_SEMANTICS: tuple[OpSemantic, ...] = (
    OpSemantic("move-forward", 2, "pos += 朝向 × distance", "按当前朝向前进 distance 格"),
    OpSemantic("move-back", 2, "pos -= 朝向 × distance", "按当前朝向后退 distance 格"),
    OpSemantic("turn-left", 1, "facing 逆时针转 angle × 90°", "朝左转 angle 个四分之一圈"),
    OpSemantic("turn-right", 1, "facing 顺时针转 angle × 90°", "朝右转 angle 个四分之一圈"),
    OpSemantic("jump", 1, "jumps += 1", "原地跳一次：P0 不改变位置，只计数（语义待 Q5）"),
    OpSemantic("pick", 1, "holding += 1", "拿起一个物品，手上的数量加一"),
    OpSemantic("drop", 1, "holding -= 1", "放下一个物品；手上为 0 时报 LB-R005"),
    OpSemantic(
        "wait",
        None,
        "无（只推进虚拟时钟）",
        "原地等待 duration 个 tick",
        duration_arg="duration",
    ),
)

#: ``op`` → 语义登记项。
SEMANTICS_BY_OP: dict[str, OpSemantic] = {semantic.op: semantic for semantic in OP_SEMANTICS}

#: §9 的执行诊断区间（``LB-R0xx``）。``LB-R002`` / ``LB-R003`` 沿用 §9 示例的语义。
DIAG_RUNTIME_FAULT = "LB-R001"  # op 没有执行语义（防御路径：合法 IR 走不到，见 docs/EXEC.md §6）
DIAG_STEP_LIMIT = "LB-R002"  # 步数上限（§9 示例沿用）
DIAG_TIMEOUT = "LB-R003"  # 虚拟时钟超时（§9 示例沿用）
DIAG_ABORTED = "LB-R004"  # 被中断
DIAG_EMPTY_HANDS = "LB-R005"  # 手上没有物品却执行 drop（P0 语义检查）

#: 诊断码 → （标题，含义，严重度）。标题会与 ``docs/EXEC.md`` 的码表逐条核对。
DIAGNOSTICS_BY_CODE: dict[str, tuple[str, str, str]] = {
    DIAG_RUNTIME_FAULT: (
        "op 没有执行语义",
        "步骤的 op 不在执行语义表里（§6 的 op 只能来自文法定义，正常链路走不到）；运行以 runtime_error 结束。",
        "error",
    ),
    DIAG_STEP_LIMIT: (
        "步数上限",
        "已执行步数达到 Ir.limits.max_steps 而程序还没跑完；运行以 limit_exceeded 结束。",
        "warning",
    ),
    DIAG_TIMEOUT: (
        "超时",
        "下一步的虚拟耗时会让总耗时超过 Ir.limits.timeout_ms；运行以 timeout 结束，时钟停在超时上限。",
        "warning",
    ),
    DIAG_ABORTED: (
        "被中断",
        "调用方（中断标志或 should_abort 回调）要求在步骤边界停下；运行以 aborted 结束。",
        "info",
    ),
    DIAG_EMPTY_HANDS: (
        "手上没有物品",
        "drop 时 holding 为 0（P0 网格模型的语义检查，Q5 定稿后可能改写）；运行以 runtime_error 结束。",
        "error",
    ),
}

#: 执行诊断码全集（按码值升序）。
EXEC_CODES: tuple[str, ...] = tuple(sorted(DIAGNOSTICS_BY_CODE))

#: ``status`` → 该 status 允许的终止诊断码（``ok`` / ``parse_error`` 没有诊断；``runtime_error``
#: 有两条可能路径：语义表里没有这个 op（``LB-R001``）或 P0 语义检查失败（``LB-R005``））。
STATUS_CODES: dict[str, tuple[str, ...]] = {
    "ok": (),
    "parse_error": (),
    "runtime_error": (DIAG_RUNTIME_FAULT, DIAG_EMPTY_HANDS),
    "timeout": (DIAG_TIMEOUT,),
    "aborted": (DIAG_ABORTED,),
    "limit_exceeded": (DIAG_STEP_LIMIT,),
}

#: 诊断码的形状（``LB-R0xx``）。
CODE_PATTERN = re.compile(r"LB-R0\d\d")


def semantic_for(op: str) -> OpSemantic | None:
    """按 ``op`` 查执行语义；不在表里（或不是字符串）返回 ``None``。"""
    if not isinstance(op, str):
        return None
    return SEMANTICS_BY_OP.get(op)


def step_ticks(op: str, args: dict | None = None) -> int | None:
    """一步消耗多少虚拟 tick；``op`` 没有语义或参数不合法时返回 ``None``。

    固定耗时的 ``op`` 忽略 ``args``；``wait`` 取 ``args.duration``（非负整数）。取不到合法值
    （缺字段、不是整数、负数）时同样返回 ``None`` —— 那是**非法步骤**，由执行器报 ``LB-R001``。
    """
    semantic = semantic_for(op)
    if semantic is None:
        return None
    if semantic.fixed_ticks:
        return int(semantic.ticks)  # type: ignore[arg-type]
    assert semantic.duration_arg is not None
    payload = args or {}
    value = payload.get(semantic.duration_arg)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def ticks_to_ms(ticks: int | float) -> float:
    """``tick`` → 虚拟毫秒。所有结果都是 :data:`CLOCK_MS_PER_TICK` 的整数倍（浮点可精确表示）。"""
    return round(float(ticks) * CLOCK_MS_PER_TICK, 6)


def _semantic_rows(text: str, semantic: OpSemantic) -> list[str]:
    """在文档里找语义表里 ``op`` 所在的行（以 ``| `op` `` 开头）。"""
    rows: list[str] = []
    prefix = f"| `{semantic.op}`"
    for line in text.splitlines():
        if line.strip().startswith(prefix):
            rows.append(line)
    return rows


def runtime_document_gaps(text: str) -> list[str]:
    """核对 ``docs/EXEC.md`` 是否写全了本模块的定义，返回缺失项（空列表 = 一致）。

    检查六件事：``exec_version``、时钟换算常量、语义表每一行（``op`` / 耗时 / 状态影响）、
    诊断码（含标题）、``status`` 与 ``phase`` / ``backend`` 枚举、世界状态字段。
    """
    gaps: list[str] = []

    if EXEC_VERSION not in text:
        gaps.append(f"缺少 exec_version {EXEC_VERSION}")
    if f"{CLOCK_MS_PER_TICK:g} ms/tick" not in text:
        gaps.append(f"缺少时钟换算 {CLOCK_MS_PER_TICK:g} ms/tick")

    for semantic in OP_SEMANTICS:
        rows = _semantic_rows(text, semantic)
        if not rows:
            gaps.append(f"语义表缺少 op {semantic.op}")
            continue
        row = rows[0]
        ticks_token = (
            f"{semantic.ticks}"
            if semantic.fixed_ticks
            else f"`{semantic.duration_arg}`"
        )
        if ticks_token not in row:
            gaps.append(f"语义表 op {semantic.op} 的耗时列不是 {ticks_token}")
        if f"`{semantic.effect}`" not in row:
            gaps.append(f"语义表 op {semantic.op} 的状态影响列不是 `{semantic.effect}`")

    for code, (title, meaning, severity) in sorted(DIAGNOSTICS_BY_CODE.items()):
        rows = [line for line in text.splitlines() if line.strip().startswith(f"| `{code}`")]
        if not rows:
            gaps.append(f"缺少诊断码 {code}")
            continue
        row = rows[0]
        if title not in row:
            gaps.append(f"诊断码 {code} 缺少标题「{title}」")
        if severity not in row:
            gaps.append(f"诊断码 {code} 缺少严重度「{severity}」")

    for status in RESULT_STATUSES:
        if f"`{status}`" not in text:
            gaps.append(f"缺少 status 取值 `{status}`")
    for phase in PHASES:
        if f"`{phase}`" not in text:
            gaps.append(f"缺少 phase 取值 `{phase}`")
    for backend in BACKENDS:
        if f"`{backend}`" not in text:
            gaps.append(f"缺少 backend 取值 `{backend}`")
    for facing in FACINGS:
        if f"`{facing}`" not in text:
            gaps.append(f"缺少世界状态朝向 `{facing}`")
    for field in ("pos", "facing", "holding", "jumps"):
        if f"`{field}`" not in text:
            gaps.append(f"缺少世界状态字段 `{field}`")
    for key in ("max_steps", "timeout_ms"):
        if f"`{key}`" not in text:
            gaps.append(f"缺少 limits 字段 `{key}`")
    return gaps


__all__ = [
    "BACKENDS",
    "BACKEND_SIM",
    "CLOCK_MS_PER_TICK",
    "CODE_PATTERN",
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
    "OP_SEMANTICS",
    "PHASES",
    "RESULT_STATUSES",
    "SEMANTICS_BY_OP",
    "STATUS_CODES",
    "OpSemantic",
    "runtime_document_gaps",
    "semantic_for",
    "step_ticks",
    "ticks_to_ms",
]
