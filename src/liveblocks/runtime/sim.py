"""模拟执行器（backend ``sim``）：``Ir`` → ``ExecEvent[]`` + ``ExecResult``（+ ``ExecState``）。

这是 LB-007 的核心模块：**虚拟时钟、步数/超时限制、中断**三件事都在 :class:`SimExecutor` 里。

- **虚拟时钟**：不睡真实时间。每执行一步把虚拟时钟往前拨 ``ticks × 10 ms``（语义表见
  :mod:`liveblocks.runtime.semantics`），因此 ``wait 100000`` 也只花毫秒级真实时间，超时判定可测、
  可复现。
- **限制来自 IR**：``max_steps`` / ``timeout_ms`` 只读 ``Ir.limits``（LB-006 已让它们显式出现），
  本层不设第二个上限；判定顺序固定为「中断 → 步数 → 语义 → 超时」（见 ``docs/EXEC.md`` §4）。
- **中断**：两条入口，都只在**步骤边界**生效（不会打断半步）：线程安全的
  :meth:`SimExecutor.request_abort`（``threading.Event``）与 ``should_abort(step_index, elapsed_ms)``
  回调（CLI 的 ``--abort-at-step`` / ``--abort-at-ms`` 就用它）。

执行结果只可能是以下五种之一（``parse_error`` 是 §7.2 的枚举值，但本层的输入已经是 IR，
上游解析错误走不到这里，见 ``docs/EXEC.md`` §10 缺口清单）：

===========  ==============================================  ===============
status       何时                                           诊断码
===========  ==============================================  ===============
``ok``       全部步骤执行完                                  —
``limit_exceeded``  已执行步数达到 ``max_steps`` 而还有步骤  ``LB-R002``
``timeout``  下一步会让虚拟耗时超过 ``timeout_ms``           ``LB-R003``
``aborted``  调用方在步骤边界要求中断                        ``LB-R004``
``runtime_error``  op 没有执行语义 / ``drop`` 时手是空的      ``LB-R001`` / ``LB-R005``
===========  ==============================================  ===============

**模拟就是模拟**：所有事件的 ``backend`` 都是 ``sim``，本模块没有、也不会有硬件路径（LB-013 才谈
``backend.hw``）。模拟结果不能当作硬件证据。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..ir.protocol import Ir, IrLimits, IrStep, validate_ir
from ..vision.protocol import diagnostic
from .protocol import (
    canonical_text,
    events_jsonl,
    exec_event,
    exec_result,
    exec_state,
    validate_event_stream,
    validate_exec_result,
    validate_exec_state,
)
from .semantics import (
    BACKEND_SIM,
    CLOCK_MS_PER_TICK,
    DIAGNOSTICS_BY_CODE,
    DIAG_ABORTED,
    DIAG_EMPTY_HANDS,
    DIAG_RUNTIME_FAULT,
    DIAG_STEP_LIMIT,
    DIAG_TIMEOUT,
    FACINGS,
    FACING_VECTORS,
    step_ticks,
    ticks_to_ms,
)

#: 中断回调的形状：``should_abort(step_index, elapsed_ms) -> bool``。
AbortHook = Callable[[int, float], bool]

#: 一次运行的默认 ``run_id``（调用方可覆盖；``RunHeader`` 由 LB-009 负责）。
DEFAULT_RUN_ID = "run-0001"


class WorldFault(Exception):
    """P0 网格模型的语义检查失败（如手上没有物品却 ``drop``）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class WorldState:
    """最小网格世界状态：位置（格）、朝向、手持数量、跳跃计数。

    坐标系是**网格坐标**（``y`` 向上为正），不是协议 §0 的图像像素坐标；两套坐标不要混用。
    """

    x: int = 0
    y: int = 0
    facing: str = "north"
    holding: int = 0
    jumps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "facing": self.facing,
            "holding": int(self.holding),
            "jumps": int(self.jumps),
        }

    def copy(self) -> "WorldState":
        return WorldState(self.x, self.y, self.facing, self.holding, self.jumps)


def _int_arg(step_args: dict[str, Any], name: str, step_id: str) -> int:
    """取一个非负整数参数；取不到时按 ``LB-R001`` 处理（``Ir`` 已校验过，这是防御路径）。"""
    value = step_args.get(name) if isinstance(step_args, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise WorldFault(
            DIAG_RUNTIME_FAULT,
            f"步骤 {step_id} 的参数 {name} 不是非负整数（实际 {value!r}）",
        )
    return value


def apply_step(world: WorldState, step: Any) -> dict[str, Any]:
    """按语义表把一步作用到世界状态，返回 ``state_delta``（写进 ``finish`` 事件的 ``detail``）。

    语义见 ``docs/EXEC.md`` §2.1；``wait`` 不改变状态（``state_delta`` 是空对象）。
    """
    op = step.op
    if op == "move-forward" or op == "move-back":
        distance = _int_arg(step.args, "distance", step.step_id)
        dx, dy = FACING_VECTORS[world.facing]
        sign = 1 if op == "move-forward" else -1
        world.x += sign * dx * distance
        world.y += sign * dy * distance
        return {"pos": {"x": world.x, "y": world.y}}
    if op == "turn-left" or op == "turn-right":
        angle = _int_arg(step.args, "angle", step.step_id)
        index = FACINGS.index(world.facing)
        index = index - angle if op == "turn-left" else index + angle
        world.facing = FACINGS[index % len(FACINGS)]
        return {"facing": world.facing}
    if op == "jump":
        world.jumps += 1
        return {"jumps": world.jumps}
    if op == "pick":
        world.holding += 1
        return {"holding": world.holding}
    if op == "drop":
        if world.holding <= 0:
            raise WorldFault(
                DIAG_EMPTY_HANDS,
                f"步骤 {step.step_id} 执行 drop 时 holding=0：手上没有可放下的物品",
            )
        world.holding -= 1
        return {"holding": world.holding}
    if op == "wait":
        return {}
    raise WorldFault(
        DIAG_RUNTIME_FAULT,
        f"步骤 {step.step_id} 的 op {op!r} 不在执行语义表里",
    )


@dataclass(frozen=True)
class SimRun:
    """一次模拟运行的产物：事件流 + ``ExecResult`` + 最终状态 + 实测墙钟耗时。"""

    run_id: str
    events: tuple[dict[str, Any], ...]
    result: dict[str, Any]
    state: dict[str, Any]
    wall_ms: float = 0.0
    world: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return str(self.result["status"])

    @property
    def steps_executed(self) -> int:
        return int(self.result["steps_executed"])

    @property
    def duration_ms(self) -> float:
        return float(self.result["duration_ms"])

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(str(entry.get("code", "")) for entry in self.result["diagnostics"])

    def events_text(self) -> str:
        """事件流的 JSONL 文本（§8 风格：一行一个事件）。"""
        return events_jsonl(self.events)

    def result_text(self) -> str:
        return canonical_text(self.result)

    def state_text(self) -> str:
        return canonical_text(self.state)


class SimExecutor:
    """模拟后端执行器：``run(ir)`` 一次，可重复使用（每次运行状态清零）。

    ``should_abort`` 是给「按步数/按虚拟时间中断」用的回调，参数是 ``(已执行步数, 虚拟耗时 ms)``；
    :meth:`request_abort` 是给「另一个线程随时叫停」用的（``threading.Event``，线程安全）。
    两者都在步骤边界生效。
    """

    def __init__(
        self,
        *,
        run_id: str = DEFAULT_RUN_ID,
        should_abort: AbortHook | None = None,
        clock_ms_per_tick: float = CLOCK_MS_PER_TICK,
    ) -> None:
        self.run_id = run_id
        self._should_abort = should_abort
        self._abort = threading.Event()
        self.clock_ms_per_tick = float(clock_ms_per_tick)

    # -- 中断入口 -----------------------------------------------------------
    def request_abort(self) -> None:
        """请求中断；下一次步骤边界生效（可以从别的线程调用）。"""
        self._abort.set()

    def clear_abort(self) -> None:
        self._abort.clear()

    @property
    def abort_requested(self) -> bool:
        return self._abort.is_set()

    def _aborted(self, step_index: int, elapsed_ms: float) -> bool:
        if self._abort.is_set():
            return True
        if self._should_abort is None:
            return False
        requested = bool(self._should_abort(step_index, elapsed_ms))
        # 回调里调用 request_abort() 时，同一边界就要生效（不必等下一步）
        return requested or self._abort.is_set()

    # -- 执行 ---------------------------------------------------------------
    def run(self, ir: Ir) -> SimRun:
        """执行一份 ``Ir``，返回 :class:`SimRun`（事件流 + 结果 + 最终状态）。"""
        started = time.perf_counter()
        limits = ir.limits
        world = WorldState()
        events: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        elapsed_ms = 0.0
        steps_executed = 0
        status = "ok"

        def emit(phase: str, step_id: str, detail: dict[str, Any], at_ms: float | None = None) -> None:
            nonlocal counter
            counter += 1
            events.append(
                exec_event(
                    run_id=self.run_id,
                    seq=counter,
                    at_ms=elapsed_ms if at_ms is None else at_ms,
                    step_id=step_id,
                    phase=phase,
                    detail=detail,
                    backend=BACKEND_SIM,
                )
            )

        counter = 0
        # 事件 1：起步日志（把 limits 与步数写进事件流，使「限制来自 IR」在输出里可见）
        emit(
            "log",
            "",
            {
                "message": "run started",
                "steps": len(ir.steps),
                "limits": limits.to_dict(),
                "backend": BACKEND_SIM,
                "clock_ms_per_tick": self.clock_ms_per_tick,
            },
        )

        def stop(new_status: str, code: str, message: str, step_id: str, extra: dict[str, Any]) -> None:
            nonlocal status, elapsed_ms
            status = new_status
            severity = DIAGNOSTICS_BY_CODE[code][2]
            diagnostics.append(diagnostic(code, severity, message))
            emit("error", step_id, {"code": code, "severity": severity, "message": message, **extra})

        for position, step in enumerate(ir.steps):
            # 1) 中断：调用方要求停就停（步骤边界，不会打断半步）
            if self._aborted(steps_executed, elapsed_ms):
                stop(
                    "aborted",
                    DIAG_ABORTED,
                    f"第 {steps_executed} 步执行前收到中断请求（步骤 {step.step_id} 未执行）；运行以 aborted 结束",
                    step.step_id,
                    {"steps_executed": steps_executed, "remaining_steps": len(ir.steps) - position},
                )
                break
            # 2) 步数上限：达到 max_steps 且还有步骤要跑
            if steps_executed >= limits.max_steps:
                stop(
                    "limit_exceeded",
                    DIAG_STEP_LIMIT,
                    f"已执行 {steps_executed} 步，达到 limits.max_steps={limits.max_steps}，"
                    f"还剩 {len(ir.steps) - position} 步未执行（下一步 {step.step_id}）；"
                    "运行以 limit_exceeded 结束",
                    step.step_id,
                    {
                        "max_steps": limits.max_steps,
                        "steps_executed": steps_executed,
                        "remaining_steps": len(ir.steps) - position,
                    },
                )
                break
            # 3) 语义：op 必须在语义表里、参数要能取到
            ticks = step_ticks(step.op, step.args)
            if ticks is None:
                stop(
                    "runtime_error",
                    DIAG_RUNTIME_FAULT,
                    f"步骤 {step.step_id} 的 op {step.op!r} 没有执行语义（或参数取不到非负整数）；"
                    "运行以 runtime_error 结束",
                    step.step_id,
                    {"op": step.op, "args": dict(step.args) if isinstance(step.args, dict) else {}},
                )
                break
            cost_ms = ticks_to_ms(ticks)
            # 4) 超时：下一步会让虚拟耗时超过 timeout_ms 就不执行它，时钟停在超时上限
            if elapsed_ms + cost_ms > float(limits.timeout_ms) + 1e-9:
                overshoot_from = elapsed_ms
                elapsed_ms = float(limits.timeout_ms)  # 时钟停在超时上限
                stop(
                    "timeout",
                    DIAG_TIMEOUT,
                    f"步骤 {step.step_id}（{step.op}）需要 {cost_ms:g} ms，虚拟时钟已走 {overshoot_from:g} ms，"
                    f"再加会超过 limits.timeout_ms={limits.timeout_ms}；运行以 timeout 结束",
                    step.step_id,
                    {
                        "timeout_ms": limits.timeout_ms,
                        "elapsed_ms": overshoot_from,
                        "next_cost_ms": cost_ms,
                    },
                )
                break

            emit(
                "start",
                step.step_id,
                {"op": step.op, "args": dict(step.args), "cost_ticks": ticks, "cost_ms": cost_ms},
            )
            try:
                delta = apply_step(world, step)
            except WorldFault as fault:
                stop(
                    "runtime_error",
                    fault.code,
                    f"{fault.message}；运行以 runtime_error 结束",
                    step.step_id,
                    {"op": step.op},
                )
                break
            elapsed_ms += cost_ms
            steps_executed += 1
            emit(
                "finish",
                step.step_id,
                {"state_delta": delta, "cost_ticks": ticks, "cost_ms": cost_ms},
            )

        result = exec_result(
            run_id=self.run_id,
            status=status,
            steps_executed=steps_executed,
            duration_ms=elapsed_ms,
            diagnostics=diagnostics,
            backend=BACKEND_SIM,
        )
        state = exec_state(
            run_id=self.run_id,
            status=status,
            steps_executed=steps_executed,
            duration_ms=elapsed_ms,
            limits=limits.to_dict(),
            world=world.to_dict(),
            events=len(events),
            last_seq=counter,
            backend=BACKEND_SIM,
        )
        return SimRun(
            run_id=self.run_id,
            events=tuple(events),
            result=result,
            state=state,
            wall_ms=(time.perf_counter() - started) * 1000.0,
            world=world.to_dict(),
        )


def ir_from_document(document: dict[str, Any]) -> Ir:
    """§6 的 ``Ir`` 文档 → :class:`Ir`；不是合法 ``Ir`` 时抛 ``ValueError``（不伪装成 ``LB-R0xx``）。

    与其它层一致：入口文档先过 LB-006 自己的校验器（schema + 语义），执行层不重新发明校验规则。
    """
    validate_ir(document)
    program = document["program"]
    steps = tuple(
        IrStep(
            step_id=str(step["step_id"]),
            op=str(step["op"]),
            args=dict(step["args"]),
            source=dict(step["source"]),
        )
        for step in program["steps"]
    )
    limits = program["limits"]
    return Ir(
        steps=steps,
        limits=IrLimits(max_steps=int(limits["max_steps"]), timeout_ms=int(limits["timeout_ms"])),
        diagnostics=tuple(document.get("diagnostics", ())),
    )


def run_ir(
    ir: Ir,
    *,
    run_id: str = DEFAULT_RUN_ID,
    should_abort: AbortHook | None = None,
) -> SimRun:
    """一次性入口：跑一份 ``Ir``（内部新建执行器，因此中断只能靠 ``should_abort``）。"""
    return SimExecutor(run_id=run_id, should_abort=should_abort).run(ir)


def validate_run(run: SimRun) -> None:
    """校验一次运行的三份产物（事件流跨事件约束 + ``ExecResult`` + ``ExecState``）。"""
    validate_event_stream(run.events)
    validate_exec_result(run.result)
    validate_exec_state(run.state)
    if run.result["steps_executed"] != run.state["steps_executed"]:
        raise ValueError("ExecResult 与 ExecState 的 steps_executed 不一致")
    if run.result["status"] != run.state["status"]:
        raise ValueError("ExecResult 与 ExecState 的 status 不一致")
    if run.state["last_seq"] != len(run.events):
        raise ValueError(
            f"ExecState.last_seq={run.state['last_seq']} 与事件条数 {len(run.events)} 不一致"
        )
    for event in run.events:
        if event["backend"] != BACKEND_SIM:
            raise ValueError(f"模拟后端不得产出 backend={event['backend']!r} 的事件")


__all__ = [
    "AbortHook",
    "DEFAULT_RUN_ID",
    "SimExecutor",
    "SimRun",
    "WorldFault",
    "WorldState",
    "apply_step",
    "ir_from_document",
    "run_ir",
    "validate_run",
]
