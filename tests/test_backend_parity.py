"""双后端一致性验收（LB-033 / LB-013 的「同 IR 两种后端」口径）。

同一份 ``Ir`` 分别用 **sim**（进程内模拟后端）与 **hw**（串口协议 + pty 设备模拟器）执行，
逐条比较事件流结构与关键状态：

- 事件条数、``seq``、``phase``、``step_id``、``at_ms``、``detail``（唯一例外：起步 ``log`` 的
  ``detail.backend`` 各家写各家的名字）；
- ``ExecResult``：``status`` / ``steps_executed`` / ``duration_ms`` / 诊断码；
- ``ExecState``：``status`` / ``steps_executed`` / ``duration_ms`` / ``limits`` / ``world`` /
  ``events`` / ``last_seq``。

另外三条防「假通过」的检查：

1. hw 那次必须真的交换过帧（``link.frames_out > 0``）——否则设备根本没参与；
2. 比较器本身要能发现差异（人为改一个事件必须报不一致）；
3. hw 侧事件必须通过 ``validate_hw_event_stream``（``backend:"hw"``、``seq`` 严格递增）。

**这是模拟器证据**：hw 侧跑的是 pty 上的纯 Python 设备模拟器，不代表真机行为（LB-020）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.hw import (
    HwRun,
    compare_runs,
    run_ir_over_pty,
    validate_hw_event_stream,
    validate_hw_run,
)
from liveblocks.runtime import SimExecutor, ir_from_document

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_IR_DIR = REPO_ROOT / "fixtures" / "exec" / "ir"
GOLDEN_IR_DIR = REPO_ROOT / "fixtures" / "ir" / "golden"

#: 覆盖成功路径与全部四种终止路径（中断由参数化钩子另测）。
PARITY_CASES = (
    ("all-ops", GOLDEN_IR_DIR / "all-ops.json", None),
    ("from-valid-basic", GOLDEN_IR_DIR / "from-valid-basic.json", None),
    ("from-valid-empty", GOLDEN_IR_DIR / "from-valid-empty.json", None),
    ("drop-empty", EXEC_IR_DIR / "drop-empty.json", None),
    ("timeout-exact", EXEC_IR_DIR / "timeout-exact.json", None),
    ("non-terminating-wait", EXEC_IR_DIR / "non-terminating-wait.json", None),
    ("non-terminating-steps", EXEC_IR_DIR / "non-terminating-steps.json", None),
    ("abort", EXEC_IR_DIR / "abort.json", 3),
)


def _ir(path: Path):
    return ir_from_document(json.loads(path.read_text(encoding="utf-8")))


def _abort_hook(step_index: int):
    return lambda current, elapsed_ms: current >= step_index


def _both(path: Path, abort_at: int | None = None):
    ir = _ir(path)
    run_id = f"run-{path.stem}"
    hook = None if abort_at is None else _abort_hook(abort_at)
    sim_run = SimExecutor(run_id=run_id, should_abort=hook).run(ir)
    hw_run = run_ir_over_pty(ir, run_id=run_id, should_abort=hook)
    return sim_run, hw_run


@pytest.mark.parametrize("name,path,abort_at", PARITY_CASES, ids=[case[0] for case in PARITY_CASES])
def test_sim_and_hw_backends_agree(name: str, path: Path, abort_at: int | None) -> None:
    sim_run, hw_run = _both(path, abort_at)
    validate_hw_run(hw_run)
    report = compare_runs(sim_run, hw_run)
    assert report.ok, "\n".join(report.lines())
    assert report.events == len(sim_run.events) > 0
    assert report.hw_frames_out > 0, "hw 侧必须真的发过命令帧"
    assert sim_run.result["backend"] == "sim"
    assert hw_run.result["backend"] == "hw"


@pytest.mark.parametrize("name,path,abort_at", PARITY_CASES, ids=[case[0] for case in PARITY_CASES])
def test_key_state_matches(name: str, path: Path, abort_at: int | None) -> None:
    """关键状态：最终世界状态、事件条数、``last_seq``、``status``、诊断码全部一致。"""
    sim_run, hw_run = _both(path, abort_at)
    assert hw_run.state["world"] == sim_run.state["world"]
    assert hw_run.state["events"] == sim_run.state["events"]
    assert hw_run.state["last_seq"] == sim_run.state["last_seq"]
    assert hw_run.state["status"] == sim_run.state["status"]
    assert hw_run.state["limits"] == sim_run.state["limits"]
    assert hw_run.codes == sim_run.codes
    assert hw_run.world == sim_run.world


def test_hw_events_conform_to_the_event_stream_rules() -> None:
    _sim_run, hw_run = _both(GOLDEN_IR_DIR / "all-ops.json")
    validate_hw_event_stream(hw_run.events)
    assert [event["seq"] for event in hw_run.events] == list(range(1, len(hw_run.events) + 1))
    at_ms = [event["at_ms"] for event in hw_run.events]
    assert at_ms == sorted(at_ms)
    assert all(event["backend"] == "hw" for event in hw_run.events)
    assert all(event["schema_version"] == "0.0.1" for event in hw_run.events)


def test_parity_is_reproducible() -> None:
    """两次 hw 运行的事件流逐字节一致，因此 parity 结论可重复。"""
    _sim_run, first = _both(GOLDEN_IR_DIR / "all-ops.json")
    _sim_run2, second = _both(GOLDEN_IR_DIR / "all-ops.json")
    assert first.events_text() == second.events_text()


def test_parity_detects_a_single_mutated_event() -> None:
    """反向验证：把 hw 事件改一个字，比较器必须报不一致（否则这个验收是假通过）。"""
    sim_run, hw_run = _both(GOLDEN_IR_DIR / "all-ops.json")
    mutated = [dict(event) for event in hw_run.events]
    mutated[2] = dict(mutated[2], detail={"state_delta": {"pos": {"x": 99, "y": 99}}, "cost_ticks": 2, "cost_ms": 20.0})
    broken = HwRun(
        run_id=hw_run.run_id,
        events=tuple(mutated),
        result=hw_run.result,
        state=hw_run.state,
        world=hw_run.world,
        link=hw_run.link,
        device=hw_run.device,
    )
    report = compare_runs(sim_run, broken)
    assert not report.ok
    assert any("detail" in item for item in report.differences)


def test_parity_detects_a_run_where_the_device_never_spoke() -> None:
    """反向验证：hw 侧一帧都没发（设备没参与）时必须报差异。"""
    sim_run, hw_run = _both(GOLDEN_IR_DIR / "all-ops.json")
    silent = HwRun(
        run_id=hw_run.run_id,
        events=hw_run.events,
        result=hw_run.result,
        state=hw_run.state,
        world=hw_run.world,
        link={**hw_run.link, "frames_out": 0},
        device=hw_run.device,
    )
    report = compare_runs(sim_run, silent)
    assert not report.ok
    assert any("一帧命令都没发出去" in item for item in report.differences)


def test_parity_report_serialises() -> None:
    sim_run, hw_run = _both(EXEC_IR_DIR / "drop-empty.json")
    payload = compare_runs(sim_run, hw_run).to_dict()
    assert payload["ok"] is True
    assert payload["backends"] == {"sim": "sim", "hw": "hw"}
    assert json.dumps(payload, ensure_ascii=False)
