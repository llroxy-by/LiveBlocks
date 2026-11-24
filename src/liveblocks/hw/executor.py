"""S6 硬件后端的**主机侧执行器**（``backend.hw``）：``Ir`` → §7.1 事件流（``backend:"hw"``）。

分工与判定顺序刻意和模拟后端**逐条对齐**（`docs/EXEC.md` §1）：

- **主机负责判定**：中断 → 步数上限 → 语义 → 超时的顺序在主机侧，与 :class:`SimExecutor` 完全一致；
  终止判定的文案也复用 :mod:`liveblocks.runtime.sim` 的同一批函数，因此「同一 IR 两种后端」
  在事件结构、``at_ms``、``detail`` 与最终状态上都能对齐（`tests/test_backend_parity.py`）。
- **设备负责执行**：每一步都以 ``STEP`` 命令下发，``state_delta`` / 世界状态由设备在
  ``STEP_OK`` 里回报；主机把设备回的 ``state_delta`` 写进 ``finish`` 事件，不做第二份世界状态。
- **链路负责上报**：断连 / 超时 / 坏帧 / 序号乱序 / 设备故障都变成 §7.1 的 ``error`` 事件 +
  ``ExecResult.diagnostics`` 里的 ``LB-H0xx``，不新造第二种上报通道。

两点必须说明（也写进 `docs/HW.md`）：

1. **这是模拟器证据**：设备是 pty 上的纯 Python 模拟器（:class:`liveblocks.hw.device.DeviceSimulator`），
   ``backend:"hw"`` 只说明「走了串口协议这条代码路径」，**不是**真机验证。
2. **重连后可恢复**：``error`` 事件带 ``LB-H001`` / ``LB-H002`` 且 ``detail.recovered=true`` 时表示
   运行还在继续——§7.1 原本把 ``error`` 当天花板，hw 后端把它扩成「可恢复的链路诊断」，
   这一点记进 `docs/HW.md` §9 的协议缺口清单。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..ir.protocol import Ir
from ..runtime.protocol import canonical_text, events_jsonl, exec_result, exec_state, validate_exec_state
from ..runtime.semantics import (
    CLOCK_MS_PER_TICK,
    DIAGNOSTICS_BY_CODE,
    step_ticks,
    ticks_to_ms,
)
from ..runtime.sim import (
    DEFAULT_RUN_ID,
    AbortHook,
    WorldState,
    abort_stop,
    runtime_fault_stop,
    step_limit_stop,
    timeout_stop,
)
from ..vision.protocol import diagnostic
from .device import DeviceSimulator, PtyPort
from .frames import Frame, FrameError, FrameParser, SequenceTracker, next_seq
from .protocol import (
    BACKEND_HW,
    CLIENT_NAME,
    DIAG_DEVICE_FAULT,
    DIAG_FRAME_CORRUPT,
    DIAG_LINK_LOST,
    DIAG_LINK_RESTORED,
    DIAG_LINK_TIMEOUT,
    DIAG_SEQUENCE,
    EVENTS_BY_CODE,
    HW_DIAGNOSTICS_BY_CODE,
    command_frame,
    hw_event,
    validate_hw_event_stream,
    validate_hw_result,
)
from .transport import (
    DEFAULT_READ_TIMEOUT_S,
    Transport,
    TransportClosed,
    TransportError,
    TransportOpenError,
)

#: 断连 / 超时 / 坏帧 / 乱序 / 设备故障 → 统一是 ``runtime_error``（§7.2 没有「硬件错误」档）。
LINK_STATUS = "runtime_error"


# ---------------------------------------------------------------------------
# 链路异常（每一种都自带 LB-H0xx 码）
# ---------------------------------------------------------------------------


class HwLinkError(Exception):
    """链路层异常基类：``code`` 是 ``LB-H0xx``，``detail`` 进 ``error`` 事件的 ``detail``。"""

    code = DIAG_DEVICE_FAULT

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = dict(detail)

    @property
    def severity(self) -> str:
        return HW_DIAGNOSTICS_BY_CODE[self.code].severity


class LinkLost(HwLinkError):
    """断连（EOF / EIO）：可以尝试重连。"""

    code = DIAG_LINK_LOST


class LinkTimeout(HwLinkError):
    """设备在期限内没有回应。"""

    code = DIAG_LINK_TIMEOUT


class FrameFault(HwLinkError):
    """坏帧（``LB-H004``）或序号乱序（``LB-H005``）。"""

    def __init__(self, error: FrameError) -> None:
        self.frame_error = error
        code = DIAG_SEQUENCE if error.reason == "sequence" else DIAG_FRAME_CORRUPT
        self.code = code
        super().__init__(f"{error.message}", reason=error.reason, **error.detail)


class DeviceFault(HwLinkError):
    """设备回了 ``FAULT``，或回复与本条命令对不上。"""

    code = DIAG_DEVICE_FAULT


class StepRejected(Exception):
    """设备在执行阶段拒绝了这一步（``STEP_ERROR``，码是 ``LB-R0xx``，比如 ``LB-R005``）。"""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail: dict[str, Any] = dict(detail)


@dataclass
class LinkStats:
    """一条链路的流量统计（写进 ``HwRun.link`` 与 CLI 摘要）。"""

    transport: str = ""
    links_opened: int = 0
    reconnects: int = 0
    link_lost: int = 0
    frames_out: int = 0
    frames_in: int = 0
    heartbeats_ignored: int = 0
    bytes_out: int = 0
    bytes_in: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "links_opened": self.links_opened,
            "reconnects": self.reconnects,
            "link_lost": self.link_lost,
            "frames_out": self.frames_out,
            "frames_in": self.frames_in,
            "heartbeats_ignored": self.heartbeats_ignored,
            "bytes_out": self.bytes_out,
            "bytes_in": self.bytes_in,
        }


class LinkSession:
    """主机侧的一条串口链路：发命令、收事件、校验序号、把底层异常翻成 ``LB-H0xx``。"""

    def __init__(
        self,
        factory: Callable[[], Transport],
        *,
        response_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
        client: str = CLIENT_NAME,
    ) -> None:
        self._factory = factory
        self.response_timeout_s = float(response_timeout_s)
        self.client = client
        self.transport: Transport | None = None
        self.parser = FrameParser()
        self.out_seq = 1
        self.in_seq = SequenceTracker()
        self.stats = LinkStats()
        #: 已解析但还没消费的事件（一次读可能同时拿到 STEP_OK 与心跳，返回时不能把后面的丢了）
        self._pending: list[tuple[dict[str, Any], str]] = []

    # -- 生命周期 -----------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self.transport is None or self.transport.closed

    def connect(self) -> dict[str, Any]:
        """打开传输并握手（``HELLO`` ↔ ``HELLO``）；失败抛 :class:`HwLinkError`。"""
        self.close()
        try:
            transport = self._factory()
            transport.open()
        except (TransportOpenError, OSError) as error:
            raise LinkLost(f"打不开串口链路：{error}", reason="open") from error
        except Exception as error:  # noqa: BLE001 - 工厂可能因为「线没插」抛任何异常
            raise LinkLost(f"拿不到串口链路：{error}", reason="open") from error
        self.transport = transport
        self.parser = FrameParser()
        self.out_seq = 1
        self.in_seq.reset()
        self._pending = []
        self.stats.transport = transport.describe().get("kind", transport.kind)
        self.stats.links_opened += 1
        self.stats.reconnects = max(0, self.stats.links_opened - 1)
        self.stats.frames_out = 0
        self.stats.frames_in = 0
        hello, _name = self.request("HELLO", {"client": self.client}, ("HELLO",), timeout_s=self.response_timeout_s)
        return hello

    def close(self) -> None:
        transport, self.transport = self.transport, None
        if transport is not None:
            transport.close()

    # -- 命令 / 事件 --------------------------------------------------------
    def send(self, name: str, body: dict[str, Any]) -> None:
        transport = self.transport
        if transport is None:
            raise LinkLost("链路未打开", reason="closed")
        frame = command_frame(name, self.out_seq, body)
        try:
            written = transport.write(frame.encode())
        except TransportClosed as error:
            self.stats.link_lost += 1
            raise LinkLost(f"写 {name} 时链路断了：{error.message}", reason=error.reason) from error
        self.out_seq = next_seq(self.out_seq)
        self.stats.frames_out += 1
        self.stats.bytes_out += int(written)

    def request(
        self,
        name: str,
        body: dict[str, Any],
        expect: tuple[str, ...],
        *,
        timeout_s: float | None = None,
    ) -> tuple[dict[str, Any], str]:
        """发一条命令并等期望的事件（忽略心跳）；返回 ``(载荷, 事件名)``。"""
        self.send(name, body)
        deadline = time.monotonic() + (self.response_timeout_s if timeout_s is None else float(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LinkTimeout(
                    f"等 {name} 的回应（{list(expect)}）超时：{self.response_timeout_s:g}s 内没有可用事件",
                    command=name,
                    expected=list(expect),
                )
            transport = self.transport
            if transport is None:
                raise LinkLost("链路在等待回应时被关闭", reason="closed")
            try:
                chunk = transport.read(remaining)
            except TransportClosed as error:
                self.stats.link_lost += 1
                raise LinkLost(f"等 {name} 的回应时链路断了：{error.message}", reason=error.reason) from error
            except TransportError as error:  # pragma: no cover - 防御路径
                raise LinkLost(f"读链路失败：{error}", reason="eio") from error
            if chunk:
                self.stats.bytes_in += len(chunk)
                frames = self.parser.feed(chunk)
                errors = self.parser.drain_errors()
                if errors:
                    raise FrameFault(errors[0])
                for frame in frames:
                    self._pending.append(self._accept(frame))
            while self._pending:
                reply_body, reply_name = self._pending.pop(0)
                if reply_name == "HEARTBEAT":
                    self.stats.heartbeats_ignored += 1
                    continue
                if reply_name == "FAULT":
                    raise DeviceFault(
                        str(reply_body.get("message", "设备报故障")),
                        device_code=reply_body.get("code"),
                        command=name,
                    )
                if reply_name in expect:
                    return reply_body, reply_name
                raise DeviceFault(
                    f"等 {name} 的回应（{list(expect)}）时收到无关事件 {reply_name}",
                    command=name,
                    got=reply_name,
                )

    def _accept(self, frame: Frame) -> tuple[dict[str, Any], str]:
        """校验序号并解析一条事件；序号乱序抛 :class:`FrameFault`。"""
        try:
            self.in_seq.check(frame.seq)
        except FrameError as error:
            raise FrameFault(error) from error
        self.stats.frames_in += 1
        spec = EVENTS_BY_CODE.get(frame.type)
        if spec is None:
            raise DeviceFault(f"设备发来未知事件 0x{frame.type:02X}", got=f"0x{frame.type:02X}")
        try:
            body = frame.body()
        except FrameError as error:
            raise FrameFault(error) from error
        return body, spec.name


# ---------------------------------------------------------------------------
# 一次运行的结果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HwRun:
    """一次 hw 运行的产物：与 :class:`liveblocks.runtime.sim.SimRun` 同形的三份文档 + 链路统计。"""

    run_id: str
    events: tuple[dict[str, Any], ...]
    result: dict[str, Any]
    state: dict[str, Any]
    wall_ms: float = 0.0
    world: dict[str, Any] = field(default_factory=dict)
    link: dict[str, Any] = field(default_factory=dict)
    device: dict[str, Any] = field(default_factory=dict)

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

    @property
    def backend(self) -> str:
        return str(self.result["backend"])

    def events_text(self) -> str:
        return events_jsonl(self.events)

    def result_text(self) -> str:
        return canonical_text(self.result)

    def state_text(self) -> str:
        return canonical_text(self.state)


class HwExecutor:
    """硬件后端执行器：``run(ir)`` 一次，链路可重连。

    ``transport_factory`` 每次调用返回一个**新**的 :class:`~liveblocks.hw.transport.Transport`
    （连接、或者重连）；``reconnect_attempts`` 是断连后允许的额外重连次数（默认 1）。
    """

    def __init__(
        self,
        *,
        transport_factory: Callable[[], Transport],
        run_id: str = DEFAULT_RUN_ID,
        should_abort: AbortHook | None = None,
        response_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
        reconnect_attempts: int = 1,
        reconnect_delay_s: float = 0.0,
        write_timeout_s: float = 1.0,
        clock_ms_per_tick: float = CLOCK_MS_PER_TICK,
        client: str = CLIENT_NAME,
    ) -> None:
        self.run_id = run_id
        self._should_abort = should_abort
        self.response_timeout_s = float(response_timeout_s)
        self.reconnect_attempts = int(reconnect_attempts)
        self.reconnect_delay_s = float(reconnect_delay_s)
        self.write_timeout_s = float(write_timeout_s)
        self.clock_ms_per_tick = float(clock_ms_per_tick)
        self._abort = False
        self.client = client
        self._link = LinkSession(
            transport_factory, response_timeout_s=self.response_timeout_s, client=client
        )
        self._device_hello: dict[str, Any] = {}

    # -- 中断 ---------------------------------------------------------------
    def request_abort(self) -> None:
        """请求中断：下一次步骤边界生效（与 sim 同一口径）。"""
        self._abort = True

    def _aborted(self, step_index: int, elapsed_ms: float) -> bool:
        if self._abort:
            return True
        if self._should_abort is None:
            return False
        return bool(self._should_abort(step_index, elapsed_ms)) or self._abort

    # -- 执行 ---------------------------------------------------------------
    def run(self, ir: Ir) -> HwRun:
        """执行一份 ``Ir``：每步下发 ``STEP``，把设备回报的 ``state_delta`` 写进事件流。"""
        started = time.perf_counter()
        link = self._link
        limits = ir.limits
        events: list[dict[str, Any]] = []
        diagnostics: list[dict[str, Any]] = []
        elapsed_ms = 0.0
        steps_executed = 0
        status = "ok"
        world = WorldState().to_dict()
        counter = 0

        def emit(phase: str, step_id: str, detail: dict[str, Any], at_ms: float | None = None) -> None:
            nonlocal counter
            counter += 1
            events.append(
                hw_event(
                    run_id=self.run_id,
                    seq=counter,
                    at_ms=elapsed_ms if at_ms is None else at_ms,
                    step_id=step_id,
                    phase=phase,
                    detail=detail,
                )
            )

        def stop(new_status: str, code: str, message: str, step_id: str, extra: dict[str, Any]) -> None:
            nonlocal status
            status = new_status
            severity = DIAGNOSTICS_BY_CODE[code][2] if code in DIAGNOSTICS_BY_CODE else _hw_severity(code)
            diagnostics.append(diagnostic(code, severity, message))
            emit("error", step_id, {"code": code, "severity": severity, "message": message, **extra})

        def link_stop(error: HwLinkError, step_id: str, recoverable: bool) -> None:
            """链路类终止：一条 ``error`` 事件 + 一条 ``LB-H0xx`` 诊断。

            ``recoverable=True`` 表示「正要尝试重连」：事件里的 ``recovered`` 先记 ``true``，
            重连失败时后续还会有一条不可恢复的 ``error`` 事件。
            """
            nonlocal status
            extra = {"recovered": recoverable, **error.detail}
            if not recoverable:
                # 可恢复的那一次不改 status：重连成功后运行还是 ok，诊断里保留这条链路故障
                status = LINK_STATUS
            diagnostics.append(diagnostic(error.code, error.severity, error.message))
            emit(
                "error",
                step_id,
                {
                    "code": error.code,
                    "severity": error.severity,
                    "message": error.message,
                    "link": link.stats.transport or "unknown",
                    **extra,
                },
            )

        def connect_and_begin() -> tuple[bool, str]:
            """握手 + BEGIN；返回 ``(是否连上, 失败时用的 step_id)``。"""
            nonlocal world
            try:
                hello = link.connect()
            except HwLinkError as error:
                link_stop(error, "", False)
                return False, "connect"
            self._device_hello = dict(hello)
            emit(
                "log",
                "",
                {
                    "message": "run started",
                    "steps": len(ir.steps),
                    "limits": limits.to_dict(),
                    "backend": BACKEND_HW,
                    "clock_ms_per_tick": self.clock_ms_per_tick,
                },
            )
            try:
                ack, _ = link.request(
                    "BEGIN",
                    {
                        "run_id": self.run_id,
                        "steps": len(ir.steps),
                        "limits": limits.to_dict(),
                        "clock_ms_per_tick": self.clock_ms_per_tick,
                    },
                    ("BEGIN_ACK",),
                )
            except HwLinkError as error:
                link_stop(error, "", False)
                return False, "begin"
            world = dict(ack.get("world", world))
            return True, ""

        try:
            connected, _stage = connect_and_begin()
            if not connected:
                return self._finalize(
                    started, events, diagnostics, status, steps_executed, elapsed_ms, world, link, limits
                )

            for position, step in enumerate(ir.steps):
                # 1) 中断（步骤边界）
                if self._aborted(steps_executed, elapsed_ms):
                    code, message, extra = abort_stop(steps_executed, step.step_id)
                    stop(
                        "aborted",
                        code,
                        message,
                        step.step_id,
                        {**extra, "remaining_steps": len(ir.steps) - position},
                    )
                    break
                # 2) 步数上限
                if steps_executed >= limits.max_steps:
                    code, message, extra = step_limit_stop(
                        max_steps=limits.max_steps,
                        steps_executed=steps_executed,
                        remaining_steps=len(ir.steps) - position,
                        step_id=step.step_id,
                    )
                    stop("limit_exceeded", code, message, step.step_id, extra)
                    break
                # 3) 语义
                ticks = step_ticks(step.op, step.args)
                if ticks is None:
                    code, message, extra = runtime_fault_stop(
                        step.step_id, step.op, step.args if isinstance(step.args, dict) else {}
                    )
                    stop("runtime_error", code, message, step.step_id, extra)
                    break
                cost_ms = ticks_to_ms(ticks)
                # 4) 超时（时钟停在超时上限）
                if elapsed_ms + cost_ms > float(limits.timeout_ms) + 1e-9:
                    overshoot_from = elapsed_ms
                    elapsed_ms = float(limits.timeout_ms)
                    code, message, extra = timeout_stop(
                        step_id=step.step_id,
                        op=step.op,
                        cost_ms=cost_ms,
                        elapsed_ms=overshoot_from,
                        timeout_ms=limits.timeout_ms,
                    )
                    stop("timeout", code, message, step.step_id, extra)
                    break

                emit(
                    "start",
                    step.step_id,
                    {"op": step.op, "args": dict(step.args), "cost_ticks": ticks, "cost_ms": cost_ms},
                )
                reply, link_error = self._run_step(link, step, ticks, emit, link_stop)
                if link_error is not None:
                    break
                if isinstance(reply, StepRejected):
                    stop(
                        "runtime_error",
                        reply.code,
                        f"{reply.message}；运行以 runtime_error 结束",
                        step.step_id,
                        {"op": step.op, **reply.detail},
                    )
                    break
                assert isinstance(reply, dict)
                world = dict(reply.get("world", world))
                elapsed_ms += cost_ms
                steps_executed += 1
                emit(
                    "finish",
                    step.step_id,
                    {
                        "state_delta": dict(reply.get("state_delta", {})),
                        "cost_ticks": ticks,
                        "cost_ms": cost_ms,
                    },
                )

            # 收尾：告诉设备本次运行的结果（失败不影响 §7 产物，链路统计里能看到）
            try:
                link.request("END", {"status": status}, ("END_ACK",))
            except HwLinkError:
                pass
        finally:
            link.close()

        return self._finalize(
            started, events, diagnostics, status, steps_executed, elapsed_ms, world, link, limits
        )

    # -- 单步下发（含一次断连重连） -----------------------------------------
    def _run_step(
        self,
        link: LinkSession,
        step: Any,
        ticks: int,
        emit: Callable[..., None],
        link_stop: Callable[..., None],
    ) -> tuple[dict[str, Any] | StepRejected | None, HwLinkError | None]:
        """下发一步；断连时尝试重连并**重发同一条** ``step_id``（设备侧幂等）。"""
        body = {
            "step_id": step.step_id,
            "op": step.op,
            "args": dict(step.args) if isinstance(step.args, dict) else {},
            "cost_ticks": int(ticks),
        }
        attempts = 0
        while True:
            try:
                reply, name = link.request("STEP", body, ("STEP_OK", "STEP_ERROR"))
            except LinkLost as error:
                attempts += 1
                link_stop(error, step.step_id, recoverable=attempts <= self.reconnect_attempts)
                if attempts > self.reconnect_attempts:
                    return None, error
                if not self._reconnect(link, step.step_id, emit, link_stop):
                    return None, error
                continue
            except HwLinkError as error:
                link_stop(error, step.step_id, recoverable=False)
                return None, error
            if name == "STEP_ERROR":
                return (
                    StepRejected(
                        str(reply.get("code", "")),
                        str(reply.get("message", "")),
                        op=str(reply.get("op", step.op)),
                    ),
                    None,
                )
            return dict(reply), None

    def _reconnect(
        self,
        link: LinkSession,
        step_id: str,
        emit: Callable[..., None],
        link_stop: Callable[..., None],
    ) -> bool:
        """重连一次（可带退避）；成功发一条 ``LB-H003`` 的 ``log`` 事件并返回 ``True``。"""
        try:
            if self.reconnect_delay_s > 0:
                time.sleep(self.reconnect_delay_s)
            hello = link.connect()
        except HwLinkError as error:
            link_stop(error, step_id, recoverable=False)
            return False
        self._device_hello = dict(hello)
        emit(
            "log",
            step_id,
            {
                "message": "link restored",
                "code": DIAG_LINK_RESTORED,
                "severity": HW_DIAGNOSTICS_BY_CODE[DIAG_LINK_RESTORED].severity,
                "backend": BACKEND_HW,
                "link": link.stats.transport,
                "reconnects": link.stats.reconnects,
                "device": hello.get("name"),
            },
        )
        return True

    # -- 收尾 ---------------------------------------------------------------
    def _finalize(
        self,
        started: float,
        events: list[dict[str, Any]],
        diagnostics: list[dict[str, Any]],
        status: str,
        steps_executed: int,
        elapsed_ms: float,
        world: dict[str, Any],
        link: LinkSession,
        limits: Any,
    ) -> HwRun:
        stats = link.stats
        result = exec_result(
            run_id=self.run_id,
            status=status,
            steps_executed=steps_executed,
            duration_ms=elapsed_ms,
            diagnostics=diagnostics,
            backend=BACKEND_HW,
        )
        state = exec_state(
            run_id=self.run_id,
            status=status,
            steps_executed=steps_executed,
            duration_ms=elapsed_ms,
            limits=limits.to_dict(),
            world=world,
            events=len(events),
            last_seq=len(events),
            backend=BACKEND_HW,
        )
        return HwRun(
            run_id=self.run_id,
            events=tuple(events),
            result=result,
            state=state,
            wall_ms=(time.perf_counter() - started) * 1000.0,
            world=dict(world),
            link=stats.to_dict(),
            device=dict(self._device_hello),
        )


def _hw_severity(code: str) -> str:
    entry = HW_DIAGNOSTICS_BY_CODE.get(code)
    return "error" if entry is None else entry.severity


def validate_hw_run(run: HwRun) -> None:
    """校验一次 hw 运行的三份产物（事件流跨事件约束 + ``ExecResult`` + ``ExecState``）。"""
    validate_hw_event_stream(run.events)
    validate_hw_result(run.result)
    validate_exec_state(run.state)
    if run.result["steps_executed"] != run.state["steps_executed"]:
        raise ValueError("ExecResult 与 ExecState 的 steps_executed 不一致")
    if run.result["status"] != run.state["status"]:
        raise ValueError("ExecResult 与 ExecState 的 status 不一致")
    if run.state["last_seq"] != len(run.events):
        raise ValueError(f"ExecState.last_seq={run.state['last_seq']} 与事件条数 {len(run.events)} 不一致")
    for event in run.events:
        if event["backend"] != BACKEND_HW:
            raise ValueError(f"hw 后端不得产出 backend={event['backend']!r} 的事件")


def run_ir_over_pty(
    ir: Ir,
    *,
    run_id: str = DEFAULT_RUN_ID,
    should_abort: AbortHook | None = None,
    response_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
    reconnect_attempts: int = 1,
    device: DeviceSimulator | None = None,
    latency_ms: float = 0.0,
    unplug_after_steps: int | None = None,
    heartbeat_ms: float | None = None,
) -> HwRun:
    """一条命令跑完「起设备 → 走 pty → 收事件」：CLI 与测试的默认入口。

    没传 ``device`` 时自己建一台（跑完关掉）；传了就用调用方的设备（测试里要拔线就用这个）。
    """
    owned = device is None
    simulator = device or DeviceSimulator(
        latency_ms=latency_ms,
        heartbeat_ms=heartbeat_ms,
        unplug_after_steps=unplug_after_steps,
    )
    port = PtyPort(simulator)
    try:
        port.plug()
        executor = HwExecutor(
            transport_factory=port.connect,
            run_id=run_id,
            should_abort=should_abort,
            response_timeout_s=response_timeout_s,
            reconnect_attempts=reconnect_attempts,
        )
        return executor.run(ir)
    finally:
        if owned:
            simulator.close()


__all__ = [
    "DeviceFault",
    "FrameFault",
    "HwExecutor",
    "HwLinkError",
    "HwRun",
    "LINK_STATUS",
    "LinkLost",
    "LinkSession",
    "LinkStats",
    "LinkTimeout",
    "StepRejected",
    "run_ir_over_pty",
    "validate_hw_run",
]
