"""传输抽象 ``Transport`` 的单测：pty / 真实串口（在 pty 上跑代码路径）/ 内存环路。

**证据口径**：这里除了「真实串口设备路径不存在时怎么报错」，全部是在 pty 上跑的。
``SerialTransport`` 的代码路径（termios 配置、部分写、超时、断连）能在 pty 上真跑，
但**不能用来说明真实串口设备可用**——真机串口见 `docs/HW.md` §8「待人工验证」。
"""

from __future__ import annotations

import os
import time

import pytest

from liveblocks.hw import (
    DEFAULT_READ_TIMEOUT_S,
    FdTransport,
    PtyTransport,
    SerialConfig,
    SerialTransport,
    TransportClosed,
    TransportOpenError,
    memory_pair,
    open_transport,
)


# -- 内存环路（协议单测的基础设施） -------------------------------------------
def test_memory_pair_roundtrips_in_both_directions() -> None:
    left, right = memory_pair()
    left.write(b"ping")
    assert right.read(0.5) == b"ping"
    right.write(b"pong")
    assert left.read(0.5) == b"pong"


def test_memory_read_times_out_without_blocking() -> None:
    left, _right = memory_pair()
    started = time.perf_counter()
    assert left.read(0.05) is None
    assert (time.perf_counter() - started) < 1.0


def test_memory_unplug_raises_closed_for_both_ends() -> None:
    left, right = memory_pair()
    left.unplug()
    for side in (left, right):
        with pytest.raises(TransportClosed):
            side.read(0.05)
        with pytest.raises(TransportClosed):
            side.write(b"x")


def test_memory_cannot_reopen_after_unplug() -> None:
    left, _right = memory_pair()
    left.unplug()
    with pytest.raises(TransportOpenError):
        left.open()


# -- pty（本单的默认链路） -----------------------------------------------------
def test_pty_roundtrip_does_not_need_a_newline(tmp_path) -> None:
    """raw 模式的关键点：不带换行的二进制帧必须立刻过去（行缓冲会把它扣住）。"""
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    try:
        device = FdTransport.from_fd(master_fd, name="dev-master")
        host = PtyTransport(path)
        host.open()
        frame = b"LB\x01\x01\x01\x01\x00\x02\x00{}"
        assert host.write(frame) == len(frame)
        assert device.read(1.0) == frame
        device.write(b"\x00\xff\x00")
        assert host.read(1.0) == b"\x00\xff\x00"
        host.close()
        device.close()
    finally:
        os.close(slave_fd)


def test_pty_read_times_out_instead_of_blocking() -> None:
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    try:
        host = PtyTransport(path)
        host.open()
        started = time.perf_counter()
        assert host.read(0.05) is None
        assert (time.perf_counter() - started) < 1.0
        host.close()
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_pty_reports_disconnect_as_transport_closed() -> None:
    """拔线 = 主端被关掉：主机侧下一次读立刻 ``TransportClosed``（不是无限等）。"""
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    host = PtyTransport(path)
    host.open()
    os.close(master_fd)  # 拔线
    try:
        with pytest.raises(TransportClosed) as info:
            for _ in range(5):
                host.read(0.2)
            pytest.fail("拔线后应该抛 TransportClosed")
        assert info.value.reason in ("eio", "eof")
    finally:
        host.close()
        os.close(slave_fd)


def test_pty_open_missing_path_raises_open_error() -> None:
    transport = PtyTransport("/dev/pts/does-not-exist")
    with pytest.raises(TransportOpenError):
        transport.open()


def test_transport_is_context_manager_and_close_is_idempotent() -> None:
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    try:
        with PtyTransport(path) as host:
            assert not host.closed
        assert host.closed
        host.close()  # 幂等
        with pytest.raises(TransportClosed):
            host.read(0.02)
    finally:
        os.close(master_fd)
        os.close(slave_fd)


# -- 真实串口（代码路径在 pty 上验证；真机待人工验证） -------------------------
def test_serial_transport_configures_and_shuttles_bytes_on_a_pty() -> None:
    """``SerialTransport`` 的 termios 配置与读写逻辑：在 pty 上真跑一遍（不是真串口）。"""
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    try:
        device = FdTransport.from_fd(master_fd, name="serial-peer")
        serial = SerialTransport(path, config=SerialConfig(baudrate=115200))
        serial.open()
        assert serial.describe()["serial"]["baudrate"] == 115200
        assert serial.write(b"\x01\x02\x03") == 3
        assert device.read(1.0) == b"\x01\x02\x03"
        device.write(b"\x04\x05")
        assert serial.read(1.0) == b"\x04\x05"
        serial.close()
        device.close()
    finally:
        os.close(slave_fd)


def test_serial_transport_rejects_an_unsupported_baudrate() -> None:
    master_fd, slave_fd = os.openpty()
    path = os.ttyname(slave_fd)
    try:
        serial = SerialTransport(path, config=SerialConfig(baudrate=12345))
        with pytest.raises(TransportOpenError):
            serial.open()
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_serial_transport_on_a_missing_device_is_an_open_error() -> None:
    serial = SerialTransport("/dev/ttyUSB-does-not-exist")
    with pytest.raises(TransportOpenError):
        serial.open()


def test_open_transport_spec_parsing() -> None:
    assert isinstance(open_transport("pty:/dev/pts/1"), PtyTransport)
    assert isinstance(open_transport("serial:/dev/ttyUSB0"), SerialTransport)
    for bad in ("/dev/ttyUSB0", "usb:/dev/x", "pty:"):
        with pytest.raises((ValueError, TransportOpenError)):
            transport = open_transport(bad)
            transport.open()


def test_default_read_timeout_is_positive() -> None:
    assert DEFAULT_READ_TIMEOUT_S > 0
