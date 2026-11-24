"""S6 硬件后端的**设备模拟器**：用 pty 假装一块 ESP32-C3 SuperMini，跑完整串口协议。

它是「固件的替身」：占住 pty 主端，按 :mod:`liveblocks.hw.protocol` 的命令表收命令、按事件表回事件，
自己维护世界状态与序号。主机（:class:`liveblocks.hw.executor.HwExecutor`）走 pty 从端，
因此**整条协议路径（帧编码 / CRC / 序号 / 命令分发 / 事件回传 / 断连）都是真跑的**，
不是打桩。

三条要说清楚的事：

1. **模拟器证据 ≠ 硬件证据**。这里的一切都由纯 Python 应答；``backend:"hw"`` 只表示「走的是串口
   协议这条路径」。真实串口与真实设备（LB-020）必须人工实测，见 `docs/HW.md` §8。
2. **拔线是可复现的**：:meth:`DeviceSimulator.unplug` 关掉主端，主机侧读立刻得到 ``EIO`` →
   :class:`liveblocks.hw.transport.TransportClosed`。设备**保留内部状态**（相当于板子还在跑，
   只是线掉了），重新 :meth:`plug` 后换一条新 pty，主机可重连接着跑。
3. **STEP 幂等**：同一 ``step_id`` 重发不重复作用，只回放上次的 ``STEP_OK`` / ``STEP_ERROR``。
   没有这条，「断连发生在设备已执行、回复没送到」时会重复执行一步。

仿真语义直接复用 :mod:`liveblocks.runtime.sim` 的 :func:`apply_step` / :class:`WorldState`——
设备与模拟后端共用同一张语义表，这是「同 IR 双后端一致」的第一个前提。
"""

from __future__ import annotations

import os
import pty
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..runtime.sim import WorldFault, WorldState, apply_step
from .frames import Frame, FrameError, FrameParser, KIND_COMMAND, SequenceTracker, next_seq
from .protocol import (
    BOARD,
    DIAG_DEVICE_FAULT,
    DIAG_FRAME_CORRUPT,
    DIAG_SEQUENCE,
    HW_PROTOCOL_VERSION,
    event_frame,
)
from .transport import FdTransport, TransportClosed, TransportError

#: 设备的固件版本（模拟器自报；真机固件版本由 LB-034 定）。
SIM_FIRMWARE = "sim-hw-0.0.1"

#: 设备线程的默认读超时（秒）：决定「拔线多久被发现」与 CPU 占用。
DEFAULT_POLL_S = 0.02


@dataclass
class _StepView:
    """给 :func:`apply_step` 用的最小步骤视图（与 ``IrStep`` 同形）。"""

    step_id: str
    op: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceStats:
    """一次运行里设备侧看到的流量（写进 ``END_ACK``，也供测试断言）。"""

    frames_in: int = 0
    frames_out: int = 0
    steps: int = 0
    errors: int = 0
    links: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_in": self.frames_in,
            "frames_out": self.frames_out,
            "steps": self.steps,
            "errors": self.errors,
            "links": self.links,
        }


class DeviceSimulator:
    """一块「插在 pty 上」的虚拟设备。``plug()`` 上电、``unplug()`` 拔线。

    参数：

    - ``latency_ms``：每条命令的模拟处理时延（默认 0；测超时/断连时用它制造窗口）；
    - ``heartbeat_ms``：设备主动上行心跳的周期（``None`` = 不主动发，默认）；
    - ``unplug_after_steps``：处理完第 n 条 ``STEP`` 后自己拔线（复现「跑到一半线掉了」）。
    """

    def __init__(
        self,
        *,
        name: str = "lb-sim-esp32c3",
        firmware: str = SIM_FIRMWARE,
        latency_ms: float = 0.0,
        heartbeat_ms: float | None = None,
        unplug_after_steps: int | None = None,
        poll_s: float = DEFAULT_POLL_S,
    ) -> None:
        self.name = name
        self.firmware = firmware
        self.latency_ms = float(latency_ms)
        self.heartbeat_ms = None if heartbeat_ms is None else float(heartbeat_ms)
        self.unplug_after_steps = unplug_after_steps
        self.poll_s = float(poll_s)

        self._lock = threading.Lock()
        self._world = WorldState()
        self._stats = DeviceStats()
        self._faults: list[dict[str, Any]] = []
        self._run_id = ""
        self._run_steps = 0
        self._limits: dict[str, Any] = {}
        self._last_step_id: str | None = None
        self._last_step_reply: dict[str, Any] | None = None
        self._status = "ok"

        self._master_fd: int | None = None
        self._slave_fd: int | None = None
        self._slave_path: str | None = None
        self._transport: FdTransport | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._out_seq = 1
        self._in_seq = SequenceTracker()
        self._uptime_start = time.monotonic()
        self._last_heartbeat = time.monotonic()
        self._plug_count = 0

    # -- 上电 / 拔线 --------------------------------------------------------
    @property
    def is_plugged(self) -> bool:
        return self._master_fd is not None and self._thread is not None and self._thread.is_alive()

    @property
    def slave_path(self) -> str:
        """主机要打开的 pty 从端路径（没插上时抛 ``RuntimeError``）。"""
        if self._slave_path is None:
            raise RuntimeError("设备还没 plug()，没有 pty 路径")
        return self._slave_path

    def plug(self) -> str:
        """上电（或插回）：建一对新的 pty，起设备线程，返回从端路径。已插着则直接返回原路径。"""
        with self._lock:
            if self.is_plugged and self._slave_path is not None:
                return self._slave_path
            master_fd, slave_fd = pty.openpty()
            self._master_fd = master_fd
            self._slave_fd = slave_fd
            self._slave_path = os.ttyname(slave_fd)
            self._transport = FdTransport.from_fd(master_fd, name=f"{self.name}:master")
            self._out_seq = 1
            self._in_seq.reset()
            self._stop = threading.Event()
            self._plug_count += 1
            self._stats.links += 1
            self._thread = threading.Thread(target=self._serve, name=f"{self.name}-device", daemon=True)
            self._thread.start()
            return self._slave_path

    def unplug(self) -> None:
        """拔线：关掉主端（主机侧读立刻 ``EIO``），设备线程退出；**内部状态保留**。"""
        with self._lock:
            thread = self._thread
            transport = self._transport
            slave_fd = self._slave_fd
            self._thread = None
            self._master_fd = None
            self._slave_fd = None
            self._transport = None
            self._stop.set()
        if transport is not None:
            transport.close()
        if slave_fd is not None:
            try:
                os.close(slave_fd)
            except OSError:  # pragma: no cover
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def close(self) -> None:
        """拆掉设备（等价于拔线 + 丢掉状态）。"""
        self.unplug()

    def __enter__(self) -> "DeviceSimulator":
        self.plug()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- 主机侧连回设备 -----------------------------------------------------
    def reconnect(self) -> str:
        """插回并返回**新的**从端路径（旧路径在拔线后不可用）。"""
        self.unplug()
        return self.plug()

    # -- 设备侧状态（测试与报告用） -----------------------------------------
    def world(self) -> dict[str, Any]:
        with self._lock:
            return self._world.to_dict()

    def stats(self) -> DeviceStats:
        with self._lock:
            return DeviceStats(**self._stats.to_dict())

    def faults(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._faults]

    def set_limits(self, limits: dict[str, Any]) -> None:
        """设备自己记下 limits（只做回显；判定在主机侧，见 `docs/HW.md` §5）。"""
        with self._lock:
            self._limits = dict(limits)

    # -- 设备线程 -----------------------------------------------------------
    def _serve(self) -> None:
        transport = self._transport
        if transport is None:  # pragma: no cover - plug() 保证非空
            return
        parser = FrameParser()
        while not self._stop.is_set():
            try:
                chunk = transport.read(self.poll_s)
            except TransportClosed:
                break
            except (TransportError, OSError):  # pragma: no cover - 关 fd 的竞态
                break
            if chunk:
                for frame in parser.feed(chunk):
                    if self._stop.is_set():
                        break
                    self._handle_frame(frame)
                for error in parser.drain_errors():
                    self._record_error(DIAG_FRAME_CORRUPT, error)
            self._maybe_heartbeat()
        return

    def _maybe_heartbeat(self) -> None:
        if self.heartbeat_ms is None:
            return
        now = time.monotonic()
        if (now - self._last_heartbeat) * 1000.0 < self.heartbeat_ms:
            return
        self._last_heartbeat = now
        with self._lock:
            steps = self._stats.steps
        self._emit("HEARTBEAT", {"uptime_ms": self._uptime_ms(), "steps": steps})

    def _uptime_ms(self) -> float:
        return round((time.monotonic() - self._uptime_start) * 1000.0, 3)

    def _record_error(self, code: str, error: FrameError | str) -> None:
        message = error.message if isinstance(error, FrameError) else str(error)
        with self._lock:
            self._stats.errors += 1
            self._faults.append({"code": code, "message": message, "at_ms": self._uptime_ms()})

    def _emit(self, name: str, body: dict[str, Any]) -> None:
        """发一条事件帧（序号在设备这一侧自增）。"""
        transport = self._transport
        if transport is None:
            return
        with self._lock:
            seq = self._out_seq
            self._out_seq = next_seq(seq)
        frame = event_frame(name, seq, body)
        try:
            transport.write(frame.encode())
        except (TransportClosed, TransportError, OSError):
            # 线掉了：这次回复送不出去；主机侧会看到断连并重连，重连后按 step_id 幂等回放
            return
        with self._lock:
            self._stats.frames_out += 1

    def _handle_frame(self, frame: Frame) -> None:
        if frame.kind != KIND_COMMAND:  # 设备只收命令帧
            message = f"设备收到了非命令帧（kind={frame.kind} type=0x{frame.type:02X}）"
            self._record_error(DIAG_DEVICE_FAULT, message)
            self._emit("FAULT", {"code": DIAG_DEVICE_FAULT, "message": message})
            return
        try:
            body = frame.body()
        except FrameError as error:
            self._record_error(DIAG_FRAME_CORRUPT, error)
            return
        with self._lock:
            self._stats.frames_in += 1
        name = _command_name(frame.type)
        if name is None:
            self._record_error(DIAG_DEVICE_FAULT, f"未知命令 0x{frame.type:02X}")
            self._emit("FAULT", {"code": DIAG_DEVICE_FAULT, "message": f"未知命令 0x{frame.type:02X}"})
            return
        # HELLO 是新链路的开始：先把序号期望重置为 1，再校验本帧
        if name == "HELLO":
            with self._lock:
                self._in_seq.reset()
                self._out_seq = 1
        try:
            self._in_seq.check(frame.seq)
        except FrameError as error:
            self._record_error(DIAG_SEQUENCE, error)
            self._emit(
                "FAULT",
                {"code": DIAG_SEQUENCE, "message": error.message, "expected": error.detail.get("expected")},
            )
            return
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)
        handler = getattr(self, f"_cmd_{name.lower()}", None)
        if handler is None:  # pragma: no cover - 表里都有处理函数（测试会核对）
            self._record_error(DIAG_DEVICE_FAULT, f"命令 {name} 没有处理函数")
            self._emit("FAULT", {"code": DIAG_DEVICE_FAULT, "message": f"命令 {name} 没有处理函数"})
            return
        handler(body)

    # -- 命令处理 -----------------------------------------------------------
    def _cmd_hello(self, body: dict[str, Any]) -> None:
        self._emit(
            "HELLO",
            {
                "board": BOARD,
                "firmware": self.firmware,
                "protocol_version": HW_PROTOCOL_VERSION,
                "name": self.name,
                "client": str(body.get("client", "")),
            },
        )

    def _cmd_begin(self, body: dict[str, Any]) -> None:
        with self._lock:
            self._world = WorldState()
            self._run_id = str(body.get("run_id", ""))
            self._run_steps = int(body.get("steps", 0) or 0)
            self._limits = dict(body.get("limits", {}) or {})
            self._last_step_id = None
            self._last_step_reply = None
            self._stats.steps = 0
            world = self._world.to_dict()
            run_id = self._run_id
            steps = self._run_steps
        self._emit("BEGIN_ACK", {"run_id": run_id, "steps": steps, "world": world})

    def _cmd_step(self, body: dict[str, Any]) -> None:
        step_id = str(body.get("step_id", ""))
        with self._lock:
            cached = self._last_step_reply if self._last_step_id == step_id else None
            cached = None if cached is None else dict(cached)
        if cached is not None:
            # 幂等：同一 step_id 重发只回放上次结果（断连后重试不会重复作用）
            name = "STEP_OK" if "state_delta" in cached else "STEP_ERROR"
            self._emit(name, cached)
            self._after_step_maybe_unplug()
            return

        view = _StepView(step_id=step_id, op=str(body.get("op", "")), args=dict(body.get("args", {}) or {}))
        try:
            with self._lock:
                delta = apply_step(self._world, view)
                world = self._world.to_dict()
                self._stats.steps += 1
            reply = {"step_id": step_id, "state_delta": delta, "world": world}
            name = "STEP_OK"
        except WorldFault as fault:
            reply = {"step_id": step_id, "code": fault.code, "message": fault.message, "op": view.op}
            name = "STEP_ERROR"
            with self._lock:
                self._stats.errors += 1
        with self._lock:
            self._last_step_id = step_id
            self._last_step_reply = dict(reply)
        self._emit(name, reply)
        self._after_step_maybe_unplug()

    def _after_step_maybe_unplug(self) -> None:
        """复现「跑到一半线被拔掉」：回复已经发出去了，然后主端被关掉（只拔一次）。"""
        limit = self.unplug_after_steps
        if limit is None:
            return
        if self.stats().steps >= limit:
            self.unplug_after_steps = None
            self.unplug()

    def _cmd_end(self, body: dict[str, Any]) -> None:
        with self._lock:
            self._status = str(body.get("status", "ok"))
            stats = self._stats
            reply = {
                "status": self._status,
                "frames_in": stats.frames_in,
                "frames_out": stats.frames_out,
                "steps": stats.steps,
            }
        self._emit("END_ACK", reply)

    def _cmd_ping(self, body: dict[str, Any]) -> None:
        self._emit("PONG", {"nonce": body.get("nonce"), "uptime_ms": self._uptime_ms()})


def _command_name(code: int) -> str | None:
    from .protocol import COMMANDS_BY_CODE

    spec = COMMANDS_BY_CODE.get(code)
    return None if spec is None else spec.name


class PtyPort:
    """主机侧的「一根 USB 线」：按需打开当前 pty 从端；拔线后自动重新插上再给新路径。

    :class:`liveblocks.hw.executor.HwExecutor` 拿它的 :meth:`connect` 当传输工厂，
    于是「拔线 → 断连事件 → 重连 → 继续跑」不需要主机知道 pty 细节。
    """

    def __init__(self, device: DeviceSimulator) -> None:
        self.device = device
        self._lock = threading.Lock()
        self.plugs = 0

    def connect(self):  # noqa: ANN201 - 返回 PtyTransport（避免顶层 import 循环）
        from .transport import PtyTransport

        with self._lock:
            if not self.device.is_plugged:
                self.device.plug()
                self.plugs += 1
            return PtyTransport(self.device.slave_path)

    def plug(self) -> str:
        with self._lock:
            path = self.device.plug()
            self.plugs += 1
            return path

    def pull(self) -> None:
        """拔线（给测试 / 演示用的显式动作）。"""
        self.device.unplug()

    def describe(self) -> dict[str, Any]:
        return {"kind": "pty-port", "device": self.device.name, "plugs": self.plugs}


__all__ = [
    "DEFAULT_POLL_S",
    "DeviceSimulator",
    "DeviceStats",
    "PtyPort",
    "SIM_FIRMWARE",
]
