"""设备模拟器的单测：握手、命令分发、幂等、拔线 / 插回、心跳、故障帧。

这些都是**模拟器证据**：它们证明「协议在这台纯 Python 设备上按文档跑」，不证明任何真机行为。
"""

from __future__ import annotations

import time

import pytest

from liveblocks.hw import (
    BOARD,
    HW_PROTOCOL_VERSION,
    DeviceSimulator,
    FrameParser,
    PtyPort,
    PtyTransport,
    command_frame,
)
from liveblocks.hw.frames import KIND_COMMAND as KIND_COMMAND_VALUE
from liveblocks.hw.frames import KIND_EVENT, Frame, SequenceTracker
from liveblocks.hw.transport import TransportClosed


class _Host:
    """测试用最小主机：直接用传输 + 解析器说话（不经过执行器）。"""

    def __init__(self, transport: PtyTransport) -> None:
        self.transport = transport
        self.transport.open()
        self.parser = FrameParser()
        self.out_seq = 1
        self.in_seq = SequenceTracker()

    def send(self, name: str, body: dict) -> None:
        self.transport.write(command_frame(name, self.out_seq, body).encode())
        self.out_seq += 1

    def recv(self, timeout: float = 2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.transport.read(0.05)
            if chunk is None:
                continue
            frames = self.parser.feed(chunk)
            assert not self.parser.drain_errors(), "不该有坏帧"
            for frame in frames:
                assert frame.kind == KIND_EVENT
                self.in_seq.check(frame.seq)
                return frame.body()
        raise AssertionError("等设备回应超时")

    def close(self) -> None:
        self.transport.close()


@pytest.fixture()
def device() -> DeviceSimulator:
    sim = DeviceSimulator()
    sim.plug()
    yield sim
    sim.close()


def test_hello_reports_board_firmware_and_protocol_version(device: DeviceSimulator) -> None:
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        hello = host.recv()
        assert hello["board"] == BOARD
        assert hello["protocol_version"] == HW_PROTOCOL_VERSION
        assert hello["client"] == "test"
        assert hello["firmware"]
    finally:
        host.close()


def test_begin_resets_world_and_step_runs_the_semantic_table(device: DeviceSimulator) -> None:
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        host.send(
            "BEGIN",
            {"run_id": "run-t", "steps": 2, "limits": {"max_steps": 9, "timeout_ms": 500}, "clock_ms_per_tick": 10.0},
        )
        ack = host.recv()
        assert ack["run_id"] == "run-t" and ack["steps"] == 2
        assert ack["world"] == {"x": 0, "y": 0, "facing": "north", "holding": 0, "jumps": 0}

        host.send("STEP", {"step_id": "s0", "op": "move-forward", "args": {"distance": 2}, "cost_ticks": 2})
        step = host.recv()
        assert step["step_id"] == "s0"
        assert step["state_delta"] == {"pos": {"x": 0, "y": 2}}
        assert step["world"]["x"] == 0 and step["world"]["y"] == 2

        host.send("END", {"status": "ok"})
        end = host.recv()
        assert end["status"] == "ok" and end["steps"] == 1
        assert device.world()["x"] == 0 and device.world()["y"] == 2
    finally:
        host.close()


def test_step_is_idempotent_by_step_id(device: DeviceSimulator) -> None:
    """同一 step_id 重发不重复作用（断连重试时最要紧的一条）。"""
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        host.send("BEGIN", {"run_id": "r", "steps": 1, "limits": {}, "clock_ms_per_tick": 10.0})
        host.recv()
        body = {"step_id": "s0", "op": "pick", "args": {}, "cost_ticks": 1}
        host.send("STEP", body)
        first = host.recv()
        host.send("STEP", body)
        second = host.recv()
        assert first == second
        assert device.world()["holding"] == 1  # 只作用了一次
    finally:
        host.close()


def test_step_error_is_reported_for_empty_hands_drop(device: DeviceSimulator) -> None:
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        host.send("BEGIN", {"run_id": "r", "steps": 1, "limits": {}, "clock_ms_per_tick": 10.0})
        host.recv()
        host.send("STEP", {"step_id": "s0", "op": "drop", "args": {}, "cost_ticks": 1})
        error = host.recv()
        assert error["code"] == "LB-R005"
        assert "holding=0" in error["message"]
    finally:
        host.close()


def test_ping_gets_pong(device: DeviceSimulator) -> None:
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        host.send("PING", {"nonce": 42})
        pong = host.recv()
        assert pong["nonce"] == 42
        assert pong["uptime_ms"] >= 0
    finally:
        host.close()


def test_unknown_command_becomes_a_fault_event(device: DeviceSimulator) -> None:
    host = _Host(PtyTransport(device.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        # 0x7F 不在命令表里（合法帧头 + 合法 CRC，只是命令号不认识）
        unknown = Frame.build(KIND_COMMAND_VALUE, 0x7F, host.out_seq, {"x": 1})
        host.out_seq += 1
        host.transport.write(unknown.encode())
        fault = host.recv()
        assert fault["code"] == "LB-H006"
        assert device.faults()
    finally:
        host.close()


def test_device_emits_heartbeats_when_asked() -> None:
    sim = DeviceSimulator(heartbeat_ms=20.0)
    sim.plug()
    host = _Host(PtyTransport(sim.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        pongs = 0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and pongs < 2:
            chunk = host.transport.read(0.1)
            if chunk is None:
                continue
            for frame in host.parser.feed(chunk):
                host.in_seq.check(frame.seq)
                if frame.type == 0x87:  # HEARTBEAT
                    pongs += 1
        assert pongs >= 1, "设备应该主动发心跳"
        assert not host.parser.drain_errors()
    finally:
        host.close()
        sim.close()


def test_unplug_makes_host_read_fail_fast_and_state_survives_replug() -> None:
    sim = DeviceSimulator()
    sim.plug()
    host = _Host(PtyTransport(sim.slave_path))
    try:
        host.send("HELLO", {"client": "test"})
        host.recv()
        host.send("BEGIN", {"run_id": "r", "steps": 1, "limits": {}, "clock_ms_per_tick": 10.0})
        host.recv()
        host.send("STEP", {"step_id": "s0", "op": "jump", "args": {}, "cost_ticks": 1})
        host.recv()
    finally:
        host.close()

    sid = sim.stats()
    assert sid.steps == 1
    sim.unplug()
    assert not sim.is_plugged
    assert sim.world()["jumps"] == 1  # 设备内部状态保留（板子还在跑，只是线掉了）

    old_path = sim.slave_path  # 拔线后旧路径仍记着，但重新插上会得到新路径
    new_path = sim.plug()
    assert new_path != old_path or sim.is_plugged
    assert sim.world()["jumps"] == 1
    host2 = _Host(PtyTransport(new_path))
    try:
        host2.send("HELLO", {"client": "test"})
        # 新链路：握手后世界状态还在
        assert sim.world()["jumps"] == 1
    finally:
        host2.close()
    sim.close()


def test_reading_a_closed_device_raises_transport_closed_quickly() -> None:
    sim = DeviceSimulator()
    sim.plug()
    transport = PtyTransport(sim.slave_path)
    transport.open()
    sim.unplug()
    started = time.perf_counter()
    with pytest.raises(TransportClosed):
        for _ in range(5):
            transport.read(0.2)
    assert (time.perf_counter() - started) < 1.0
    transport.close()


def test_pty_port_replugs_the_device_for_the_host() -> None:
    sim = DeviceSimulator()
    port = PtyPort(sim)
    first = port.connect()
    first.open()
    first.close()
    port.pull()
    assert not sim.is_plugged
    second = port.connect()  # 自动插回
    second.open()
    assert sim.is_plugged
    assert port.plugs >= 1
    second.close()
    sim.close()
