"""模拟执行器的行为：虚拟时钟、``op`` 语义、``limits``（步数/超时）、中断、确定性与耗时。

这里的断言都对着 ``docs/EXEC.md`` 写：语义表的耗时、世界状态、判定顺序（中断 → 步数 → 语义 →
超时）、边界（恰好用满 ``timeout_ms`` 算 ``ok``）、以及「虚拟时钟不睡真实时间」。
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from liveblocks.ir import Ir, IrLimits, IrStep
from liveblocks.runtime import (
    CLOCK_MS_PER_TICK,
    OP_SEMANTICS,
    SEMANTICS_BY_OP,
    SimExecutor,
    SimRun,
    WorldState,
    apply_step,
    ir_from_document,
    run_ir,
    semantic_for,
    step_ticks,
    validate_run,
)

#: 单次测试运行的墙钟上限（毫秒）：虚拟时钟不睡真实时间，跑几千步也该在几十毫秒内。
WALL_MS_BOUND = 500.0


def make_ir(*ops: tuple[str, dict] | str, max_steps: int = 1000, timeout_ms: int = 5000) -> Ir:
    """按 ``op``（可带 ``args``）列表构造一份 ``Ir``，``step_id`` 密集编号。"""
    steps = []
    for index, item in enumerate(ops):
        op, args = (item, {}) if isinstance(item, str) else item
        steps.append(IrStep(step_id=f"s{index}", op=op, args=dict(args), source={"row": index, "slot": 0}))
    return Ir(steps=tuple(steps), limits=IrLimits(max_steps=max_steps, timeout_ms=timeout_ms))


def move(distance: int = 1) -> tuple[str, dict]:
    return ("move-forward", {"distance": distance, "unit": "cell"})


def back(distance: int = 1) -> tuple[str, dict]:
    return ("move-back", {"distance": distance, "unit": "cell"})


def turn_left(angle: int = 1) -> tuple[str, dict]:
    return ("turn-left", {"angle": angle, "unit": "quarter_turn"})


def turn_right(angle: int = 1) -> tuple[str, dict]:
    return ("turn-right", {"angle": angle, "unit": "quarter_turn"})


def wait(duration: int) -> tuple[str, dict]:
    return ("wait", {"duration": duration, "unit": "tick"})


# --------------------------------------------------------------------------
# 语义表 / 虚拟时钟
# --------------------------------------------------------------------------
def test_semantics_table_covers_the_ir_op_table() -> None:
    from liveblocks.ir import OP_SPECS

    assert {spec.op for spec in OP_SPECS} == set(SEMANTICS_BY_OP)


@pytest.mark.parametrize("semantic", OP_SEMANTICS, ids=lambda s: s.op)
def test_each_op_costs_the_documented_ticks(semantic) -> None:
    args = {"duration": 3, "unit": "tick"} if semantic.op == "wait" else {}
    if semantic.op.startswith("move"):
        args = {"distance": 1, "unit": "cell"}
    elif semantic.op.startswith("turn"):
        args = {"angle": 1, "unit": "quarter_turn"}
    ticks = step_ticks(semantic.op, args)
    assert ticks == (semantic.ticks if semantic.fixed_ticks else 3)

    # drop 前先 pick，否则会撞上 LB-R005 语义检查（那是另一条用例）
    steps = (("pick", {}), (semantic.op, args)) if semantic.op == "drop" else ((semantic.op, args),)
    run = run_ir(make_ir(*steps))
    finish = [event for event in run.events if event["phase"] == "finish"]
    assert len(finish) == len(steps)
    assert finish[-1]["detail"]["cost_ticks"] == ticks
    assert finish[-1]["detail"]["cost_ms"] == ticks * CLOCK_MS_PER_TICK
    extra = CLOCK_MS_PER_TICK if semantic.op == "drop" else 0.0  # 前置的 pick 也要 1 tick
    assert run.duration_ms == ticks * CLOCK_MS_PER_TICK + extra
    assert run.status == "ok"


def test_step_ticks_rejects_bad_wait_duration() -> None:
    assert step_ticks("wait", {}) is None
    assert step_ticks("wait", {"duration": -1}) is None
    assert step_ticks("wait", {"duration": 1.5}) is None
    assert step_ticks("teleport", {}) is None
    assert semantic_for("teleport") is None


def test_world_state_follows_the_semantics_table() -> None:
    world = WorldState()
    assert apply_step(world, IrStep("s0", "move-forward", {"distance": 2, "unit": "cell"}, {"row": 0, "slot": 0})) == {
        "pos": {"x": 0, "y": 2}
    }
    assert apply_step(world, IrStep("s1", "turn-left", {"angle": 1, "unit": "quarter_turn"}, {"row": 1, "slot": 0})) == {
        "facing": "west"
    }
    assert apply_step(world, IrStep("s2", "move-forward", {"distance": 1, "unit": "cell"}, {"row": 2, "slot": 0})) == {
        "pos": {"x": -1, "y": 2}
    }
    assert apply_step(world, IrStep("s3", "turn-right", {"angle": 3, "unit": "quarter_turn"}, {"row": 3, "slot": 0})) == {
        "facing": "south"
    }
    assert apply_step(world, IrStep("s4", "pick", {}, {"row": 4, "slot": 0})) == {"holding": 1}
    assert apply_step(world, IrStep("s5", "drop", {}, {"row": 5, "slot": 0})) == {"holding": 0}
    assert apply_step(world, IrStep("s6", "jump", {}, {"row": 6, "slot": 0})) == {"jumps": 1}
    assert apply_step(world, IrStep("s7", "wait", {"duration": 2, "unit": "tick"}, {"row": 7, "slot": 0})) == {}
    assert world.to_dict() == {"x": -1, "y": 2, "facing": "south", "holding": 0, "jumps": 1}


def test_event_stream_is_start_finish_pairs_with_rising_seq() -> None:
    run = run_ir(make_ir(move(1), turn_right(1), wait(2)))
    assert run.status == "ok"
    assert len(run.events) == 1 + 2 * 3
    assert [event["seq"] for event in run.events] == list(range(1, len(run.events) + 1))
    assert run.events[0]["phase"] == "log"
    assert run.events[0]["step_id"] == ""
    assert [event["phase"] for event in run.events[1:]] == [
        "start",
        "finish",
        "start",
        "finish",
        "start",
        "finish",
    ]
    assert [event["step_id"] for event in run.events[1:]] == ["s0", "s0", "s1", "s1", "s2", "s2"]
    times = [event["at_ms"] for event in run.events]
    assert times == sorted(times)
    assert all(event["backend"] == "sim" for event in run.events)
    validate_run(run)


def test_empty_program_only_has_the_start_log_event() -> None:
    run = run_ir(make_ir())
    assert run.status == "ok"
    assert run.steps_executed == 0
    assert run.duration_ms == 0.0
    assert len(run.events) == 1
    assert run.events[0]["phase"] == "log"
    assert run.events[0]["detail"]["limits"] == {"max_steps": 1000, "timeout_ms": 5000}
    assert run.state["world"]["pos"] == {"x": 0, "y": 0}


def test_clock_advances_only_with_steps() -> None:
    run = run_ir(make_ir(move(1), wait(3)))
    finishes = [event["at_ms"] for event in run.events if event["phase"] == "finish"]
    assert finishes == [2 * CLOCK_MS_PER_TICK, 5 * CLOCK_MS_PER_TICK]
    assert run.duration_ms == 5 * CLOCK_MS_PER_TICK


# --------------------------------------------------------------------------
# limits：步数与超时
# --------------------------------------------------------------------------
def test_step_limit_is_enforced_and_leaves_a_diagnostic() -> None:
    run = run_ir(make_ir(*[move(1) for _ in range(5)], max_steps=3))
    assert run.status == "limit_exceeded"
    assert run.steps_executed == 3
    assert run.codes == ("LB-R002",)
    assert run.events[-1]["phase"] == "error"
    assert run.events[-1]["detail"]["max_steps"] == 3
    assert run.events[-1]["detail"]["remaining_steps"] == 2
    assert run.state["status"] == "limit_exceeded"
    validate_run(run)


def test_exact_step_budget_is_ok() -> None:
    run = run_ir(make_ir(*[move(1) for _ in range(3)], max_steps=3))
    assert run.status == "ok"
    assert run.steps_executed == 3
    assert run.codes == ()


def test_timeout_stops_the_clock_at_the_limit() -> None:
    run = run_ir(make_ir(wait(5), wait(500), timeout_ms=1000))
    assert run.status == "timeout"
    assert run.steps_executed == 1
    assert run.codes == ("LB-R003",)
    assert run.duration_ms == 1000.0
    assert run.events[-1]["phase"] == "error"
    assert run.events[-1]["at_ms"] == 1000.0
    validate_run(run)


def test_exact_timeout_budget_is_ok() -> None:
    run = run_ir(make_ir(wait(100), timeout_ms=1000))
    assert run.status == "ok"
    assert run.duration_ms == 1000.0


def test_limits_come_from_the_ir() -> None:
    run = run_ir(make_ir(wait(5), max_steps=7, timeout_ms=90))
    assert run.state["limits"] == {"max_steps": 7, "timeout_ms": 90}
    assert run.events[0]["detail"]["limits"] == {"max_steps": 7, "timeout_ms": 90}
    assert run.status == "ok"


# --------------------------------------------------------------------------
# 中断
# --------------------------------------------------------------------------
def test_abort_hook_stops_before_the_step() -> None:
    run = run_ir(make_ir(*[move(1) for _ in range(6)]), should_abort=lambda step_index, _ms: step_index >= 2)
    assert run.status == "aborted"
    assert run.steps_executed == 2
    assert run.codes == ("LB-R004",)
    assert run.events[-1]["detail"]["steps_executed"] == 2
    validate_run(run)


def test_abort_at_the_first_boundary_executes_nothing() -> None:
    run = run_ir(make_ir(move(1)), should_abort=lambda _index, _ms: True)
    assert run.status == "aborted"
    assert run.steps_executed == 0
    assert run.state["world"]["pos"] == {"x": 0, "y": 0}


def test_request_abort_is_honoured_from_a_should_abort_callback() -> None:
    executor = SimExecutor(run_id="run-abort-flag")

    def hook(step_index: int, _elapsed_ms: float) -> bool:
        if step_index >= 2:
            executor.request_abort()
        return False

    executor._should_abort = hook  # 只按 request_abort 的标志停下（回调自己永远返回 False）
    run = executor.run(make_ir(*[move(1) for _ in range(6)]))
    assert run.status == "aborted"
    assert run.steps_executed == 2
    assert executor.abort_requested


def test_request_abort_works_across_threads() -> None:
    """另一个线程在步骤边界叫停：主线程在回调里等它把标志置上，因此结果完全确定。"""
    executor = SimExecutor(run_id="run-thread-abort")
    requested = threading.Event()
    armed = threading.Event()
    seen: list[int] = []

    def hook(step_index: int, _elapsed_ms: float) -> bool:
        seen.append(step_index)
        if step_index < 2:
            return False
        armed.set()
        return requested.wait(timeout=5.0)

    def worker() -> None:
        assert armed.wait(timeout=5.0)
        executor.request_abort()
        requested.set()

    executor._should_abort = hook
    thread = threading.Thread(target=worker)
    thread.start()
    run = executor.run(make_ir(*[move(1) for _ in range(20)]))
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert run.status == "aborted"
    assert run.steps_executed == 2
    validate_run(run)


def test_clear_abort_lets_the_executor_run_again() -> None:
    executor = SimExecutor(run_id="run-reuse")
    executor.request_abort()
    assert executor.run(make_ir(move(1))).status == "aborted"
    executor.clear_abort()
    assert executor.run(make_ir(move(1))).status == "ok"


# --------------------------------------------------------------------------
# 运行错误（防御路径与语义检查）
# --------------------------------------------------------------------------
def test_undefined_op_is_a_runtime_error_not_a_hang() -> None:
    ir = make_ir(("teleport", {"distance": 1, "unit": "cell"}))
    run = run_ir(ir)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-R001",)
    assert run.steps_executed == 0
    assert "error" == run.events[-1]["phase"]
    validate_run(run)


def test_drop_with_empty_hands_is_a_runtime_error() -> None:
    run = run_ir(make_ir(("pick", {}), ("drop", {}), ("drop", {})))
    assert run.status == "runtime_error"
    assert run.codes == ("LB-R005",)
    assert run.steps_executed == 2
    validate_run(run)


def test_ir_document_with_unknown_op_is_rejected_before_running() -> None:
    document = make_ir(move(1)).to_dict()
    document["program"]["steps"][0]["op"] = "teleport"
    with pytest.raises(ValueError, match="op"):
        ir_from_document(document)


# --------------------------------------------------------------------------
# 确定性与「虚拟时钟不睡真实时间」
# --------------------------------------------------------------------------
def test_two_runs_are_byte_identical() -> None:
    ir = make_ir(move(2), turn_left(1), ("pick", {}), ("drop", {}), wait(4))
    first = run_ir(ir, run_id="run-det")
    second = run_ir(ir, run_id="run-det")
    assert first.events_text() == second.events_text()
    assert first.result_text() == second.result_text()
    assert first.state_text() == second.state_text()
    assert json.loads(first.state_text())["status"] == "ok"


def test_virtual_wait_does_not_sleep_wall_clock() -> None:
    """``wait 100000`` = 1 000 000 虚拟毫秒，真实耗时必须是毫秒级。"""
    started = time.perf_counter()
    run = run_ir(make_ir(wait(100000), timeout_ms=2_000_000))
    wall_ms = (time.perf_counter() - started) * 1000.0
    assert run.status == "ok"
    assert run.duration_ms == 1_000_000.0
    assert wall_ms < WALL_MS_BOUND
    assert run.wall_ms < WALL_MS_BOUND


def test_many_steps_stay_fast() -> None:
    started = time.perf_counter()
    run = run_ir(make_ir(*[move(1) for _ in range(1000)], timeout_ms=1_000_000))
    wall_ms = (time.perf_counter() - started) * 1000.0
    assert run.steps_executed == 1000
    assert len(run.events) == 2001
    assert wall_ms < WALL_MS_BOUND


def test_validate_run_catches_a_broken_stream() -> None:
    good = run_ir(make_ir(move(1)))
    broken_events = list(good.events)
    broken_events[1] = dict(broken_events[1], seq=99, backend="hw")
    broken = SimRun(
        run_id=good.run_id,
        events=tuple(broken_events),
        result=good.result,
        state=good.state,
        wall_ms=0.0,
    )
    with pytest.raises(ValueError):
        validate_run(broken)
