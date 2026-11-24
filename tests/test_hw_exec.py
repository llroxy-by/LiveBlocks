"""``backend.hw`` 执行器（:class:`liveblocks.hw.executor.HwExecutor`）的单测。

覆盖四类事情：

1. **正常路径**：事件流符合 §7.1、``backend:"hw"``、每次运行真的交换过帧；
2. **终止路径**：中断 / 步数上限 / 超时 / 设备拒绝（``LB-R0xx``）与 sim 同一口径；
3. **链路路径**：拔线（``LB-H001``）、无回应（``LB-H002``）、坏帧（``LB-H004``）、
   序号乱序（``LB-H005``）、设备故障（``LB-H006``），且**都不阻塞**；
4. **重连**：拔线后插回同一条设备，运行接着跑完、最终状态与 sim 一致。

全部证据来自 pty / 内存链路上的**设备模拟器**，属于模拟器证据，不是硬件证据。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from liveblocks.hw import (
    DeviceSimulator,
    HwExecutor,
    PtyPort,
    validate_hw_run,
)
from liveblocks.hw.frames import FrameParser
from liveblocks.hw import event_frame, memory_pair
from liveblocks.hw.protocol import COMMANDS_BY_CODE, EVENTS_BY_NAME
from liveblocks.runtime import SimExecutor, ir_from_document

REPO_ROOT = Path(__file__).resolve().parents[1]
ALL_OPS = json.loads((REPO_ROOT / "fixtures" / "ir" / "golden" / "all-ops.json").read_text(encoding="utf-8"))
DROP_EMPTY = json.loads((REPO_ROOT / "fixtures" / "exec" / "ir" / "drop-empty.json").read_text(encoding="utf-8"))
NON_TERMINATING = json.loads(
    (REPO_ROOT / "fixtures" / "exec" / "ir" / "non-terminating-steps.json").read_text(encoding="utf-8")
)


def _abort_after(n: int):
    return lambda step_index, elapsed_ms: step_index >= n


class ScriptedPeer:
    """一台「脚本化」的设备：用来注入坏帧 / 乱序 / 无关事件这类真实设备不该发的帧。

    它走的是内存链路（:func:`memory_pair`），因此注入是确定性的、毫秒级。
    """

    def __init__(self, fault: str | None = None, fault_at_step: int = 1) -> None:
        self.host_end, self.peer_end = memory_pair(name="scripted")
        self.fault = fault
        self.fault_at_step = fault_at_step
        self.out_seq = 1
        self.steps_seen = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.parser = FrameParser()

    def start(self) -> "ScriptedPeer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.host_end.unplug()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def factory(self):
        return self.host_end

    # -- 内部 ---------------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self.peer_end.read(0.05)
            except Exception:
                return
            if not chunk:
                continue
            for frame in self.parser.feed(chunk):
                self._handle(frame)
            for _error in self.parser.drain_errors():
                pass

    def _emit_raw(self, frame) -> None:
        self.peer_end.write(frame.encode())

    def _emit(self, name: str, body: dict) -> None:
        self._emit_raw(event_frame(name, self.out_seq, body))
        self.out_seq += 1

    def _handle(self, frame) -> None:
        spec = COMMANDS_BY_CODE.get(frame.type)
        if spec is None:
            return
        body = frame.body()
        if spec.name == "HELLO":
            self.out_seq = 1
            self._emit(
                "HELLO",
                {
                    "board": "esp32c3-supermini",
                    "firmware": "scripted",
                    "protocol_version": "0.0.1",
                    "name": "scripted-peer",
                },
            )
        elif spec.name == "BEGIN":
            self._emit(
                "BEGIN_ACK",
                {
                    "run_id": body.get("run_id"),
                    "steps": body.get("steps"),
                    "world": {"x": 0, "y": 0, "facing": "north", "holding": 0, "jumps": 0},
                },
            )
        elif spec.name == "STEP":
            self.steps_seen += 1
            if self.steps_seen == self.fault_at_step and self.fault is not None:
                self._inject()
                return
            self._emit(
                "STEP_OK",
                {
                    "step_id": body.get("step_id"),
                    "state_delta": {},
                    "world": {"x": 0, "y": 0, "facing": "north", "holding": 0, "jumps": 0},
                },
            )
        elif spec.name == "END":
            self._emit("END_ACK", {"status": body.get("status", "ok"), "steps": self.steps_seen})
        elif spec.name == "PING":
            self._emit("PONG", {"nonce": body.get("nonce"), "uptime_ms": 1.0})

    def _inject(self) -> None:
        if self.fault == "crc":
            raw = bytearray(event_frame("STEP_OK", self.out_seq, {"step_id": "s0", "state_delta": {}, "world": {}}).encode())
            raw[-1] ^= 0xFF  # 弄坏 CRC
            self.peer_end.write(bytes(raw))
            self.out_seq += 1
        elif self.fault == "seq":
            self._emit_raw(event_frame("STEP_OK", 9, {"step_id": "s0", "state_delta": {}, "world": {}}))
            self.out_seq += 1
        elif self.fault == "wrong_event":
            self._emit("PONG", {"nonce": 1, "uptime_ms": 1.0})
        elif self.fault == "silent":
            return


def _run_over_scripted(fault: str, ir, *, timeout_s: float = 0.5):
    peer = ScriptedPeer(fault=fault).start()
    executor = HwExecutor(
        transport_factory=peer.factory,
        run_id="run-scripted",
        response_timeout_s=timeout_s,
        reconnect_attempts=0,
    )
    try:
        return executor.run(ir), peer
    finally:
        peer.stop()


def _run_over_pty(ir, *, device: DeviceSimulator | None = None, **kwargs):
    sim = device or DeviceSimulator()
    port = PtyPort(sim)
    port.plug()
    executor = HwExecutor(transport_factory=port.connect, run_id="run-hw", **kwargs)
    try:
        return executor.run(ir)
    finally:
        if device is None:
            sim.close()


# -- 正常路径 ---------------------------------------------------------------
def test_ok_run_produces_a_valid_hw_event_stream() -> None:
    ir = ir_from_document(ALL_OPS)
    run = _run_over_pty(ir, response_timeout_s=1.0)
    validate_hw_run(run)
    assert run.status == "ok"
    assert run.steps_executed == len(ir.steps)
    assert run.backend == "hw"
    assert all(event["backend"] == "hw" for event in run.events)
    assert len(run.events) == 1 + 2 * len(ir.steps)
    assert run.link["frames_out"] >= len(ir.steps) + 2  # HELLO + BEGIN + STEP×n
    assert run.link["frames_in"] == run.link["frames_out"]
    assert run.device["board"] == "esp32c3-supermini"


def test_two_pty_runs_produce_identical_events() -> None:
    """同样的 IR 跑两次 → 事件流逐字节一致（事件里不含墙钟时间）。"""
    ir = ir_from_document(ALL_OPS)
    first = _run_over_pty(ir)
    second = _run_over_pty(ir)
    assert first.events_text() == second.events_text()
    assert first.result_text() == second.result_text()
    assert first.state_text() == second.state_text()


def test_device_state_is_the_source_of_the_finish_events() -> None:
    ir = ir_from_document(ALL_OPS)
    device = DeviceSimulator()
    run = _run_over_pty(ir, device=device)
    try:
        assert run.world == device.world()
        finish = [event for event in run.events if event["phase"] == "finish"]
        # 至少有一条 finish 带非空 state_delta：那是设备在 STEP_OK 里回报的，不是主机自己算的
        assert any(event["detail"]["state_delta"] for event in finish)
    finally:
        device.close()


# -- 终止路径（与 sim 同口径） -----------------------------------------------
def test_abort_hook_stops_at_a_step_boundary() -> None:
    ir = ir_from_document(ALL_OPS)
    run = _run_over_pty(ir, should_abort=_abort_after(2))
    validate_hw_run(run)
    assert run.status == "aborted"
    assert run.codes == ("LB-R004",)
    assert run.steps_executed == 2


def test_step_limit_is_enforced_exactly_like_the_sim_backend() -> None:
    ir = ir_from_document(NON_TERMINATING)
    run = _run_over_pty(ir)
    validate_hw_run(run)
    assert run.status == "limit_exceeded"
    assert run.codes == ("LB-R002",)
    assert run.steps_executed == ir.limits.max_steps


def test_device_rejection_becomes_a_runtime_error_with_the_r005_code() -> None:
    ir = ir_from_document(DROP_EMPTY)
    run = _run_over_pty(ir)
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-R005",)
    errors = [event for event in run.events if event["phase"] == "error"]
    assert len(errors) == 1
    assert "holding=0" in errors[0]["detail"]["message"]


# -- 链路路径 ---------------------------------------------------------------
def test_unplug_without_reconnect_gives_a_clear_error_event_and_does_not_block() -> None:
    ir = ir_from_document(ALL_OPS)
    device = DeviceSimulator(unplug_after_steps=2)
    port = PtyPort(device)
    port.plug()
    executor = HwExecutor(
        transport_factory=port.connect, run_id="run-unplug", response_timeout_s=0.5, reconnect_attempts=0
    )
    started = time.perf_counter()
    run = executor.run(ir)
    wall_ms = (time.perf_counter() - started) * 1000.0
    device.close()
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H001",)
    assert wall_ms < 2000.0, f"拔线后不该阻塞（实测 {wall_ms:.1f} ms）"
    errors = [event for event in run.events if event["phase"] == "error"]
    assert errors and errors[-1]["detail"]["code"] == "LB-H001"
    assert errors[-1]["detail"]["recovered"] is False
    assert run.steps_executed < len(ir.steps)


def test_unplug_then_reconnect_finishes_the_run_with_the_same_state() -> None:
    ir = ir_from_document(ALL_OPS)
    sim_run = SimExecutor(run_id="run-reconnect").run(ir)
    device = DeviceSimulator(unplug_after_steps=3)
    port = PtyPort(device)
    port.plug()
    executor = HwExecutor(
        transport_factory=port.connect, run_id="run-reconnect", response_timeout_s=0.5, reconnect_attempts=2
    )
    run = executor.run(ir)
    device.close()
    validate_hw_run(run)
    assert run.status == "ok"
    assert run.steps_executed == len(ir.steps)
    assert run.world == sim_run.world
    assert "LB-H001" in run.codes  # 断连留痕
    restored = [event for event in run.events if event["detail"].get("code") == "LB-H003"]
    assert restored, "重连成功要有一条 LB-H003 的 log 事件"
    assert run.link["reconnects"] >= 1


def test_missing_cable_gives_one_link_lost_event() -> None:
    ir = ir_from_document(ALL_OPS)

    def no_cable():
        raise RuntimeError("没有串口设备")

    run = HwExecutor(transport_factory=no_cable, run_id="run-nocable", response_timeout_s=0.2).run(ir)
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H001",)
    assert len(run.events) == 1
    assert run.events[0]["detail"]["reason"] == "open"


def test_device_that_never_answers_times_out() -> None:
    ir = ir_from_document(ALL_OPS)
    device = DeviceSimulator(latency_ms=300.0)
    run = _run_over_pty(ir, device=device, response_timeout_s=0.05, reconnect_attempts=0)
    device.close()
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H002",)


def test_crc_error_from_the_device_is_reported_as_a_corrupt_frame() -> None:
    run, _peer = _run_over_scripted("crc", ir_from_document(ALL_OPS))
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H004",)
    assert run.events[-1]["detail"]["reason"] == "crc"


def test_out_of_order_sequence_number_is_reported() -> None:
    run, _peer = _run_over_scripted("seq", ir_from_document(ALL_OPS))
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H005",)
    # HELLO(1) 与 BEGIN_ACK(2) 已经消耗掉前两个序号，所以第三步的期望值是 3
    assert run.events[-1]["detail"]["expected"] == 3


def test_unexpected_event_from_the_device_is_a_device_fault() -> None:
    run, _peer = _run_over_scripted("wrong_event", ir_from_document(ALL_OPS))
    validate_hw_run(run)
    assert run.status == "runtime_error"
    assert run.codes == ("LB-H006",)


def test_heartbeats_are_ignored_without_breaking_the_stream() -> None:
    ir = ir_from_document(ALL_OPS)
    device = DeviceSimulator(heartbeat_ms=5.0)
    run = _run_over_pty(ir, device=device, response_timeout_s=1.0)
    device.close()
    validate_hw_run(run)
    assert run.status == "ok"
    assert run.link["heartbeats_ignored"] >= 1
    assert EVENTS_BY_NAME["HEARTBEAT"].code == 0x87


# -- 校验器的反向验证 -------------------------------------------------------
def test_validator_rejects_a_sim_backend_event() -> None:
    ir = ir_from_document(ALL_OPS)
    run = _run_over_pty(ir)
    from liveblocks.hw import HwRun

    broken_event = dict(run.events[0], backend="sim")
    broken = HwRun(
        run_id=run.run_id,
        events=(broken_event,) + run.events[1:],
        result=run.result,
        state=run.state,
        world=run.world,
        link=run.link,
    )
    with pytest.raises(ValueError):
        validate_hw_run(broken)


def test_validator_accepts_link_codes_in_diagnostics() -> None:
    from liveblocks.hw import validate_hw_result

    document = {
        "schema_version": "0.0.1",
        "kind": "ExecResult",
        "run_id": "run-x",
        "status": "runtime_error",
        "steps_executed": 0,
        "duration_ms": 0.0,
        "backend": "hw",
        "diagnostics": [{"code": "LB-H001", "severity": "error", "message": "断了"}],
    }
    validate_hw_result(document)
    document["diagnostics"] = [{"code": "LB-X999", "severity": "error", "message": "不在码表里"}]
    with pytest.raises(ValueError):
        validate_hw_result(document)
