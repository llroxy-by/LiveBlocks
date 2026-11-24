"""S6 硬件后端（``backend.hw``）的协议常量、命令 / 事件表、``LB-H0xx`` 错误模型与文档核对。

三件事在这里定死，`docs/HW.md` 是人读的同一份：

1. **命令 / 事件表**（帧载荷的字段名与语义）——固件（LB-034）直接复用；
2. **错误模型**：断连、超时、重连、坏帧、序号乱序、设备故障六种情形各有码，且都落在
   §7 的事件流 / ``ExecResult`` 里，不新造第二种上报通道；
3. **``backend.hw`` 的事件流映射**：与 sim 完全同一份 §7.1 形状（:func:`liveblocks.runtime.protocol.exec_event`），
   只把 ``backend`` 换成 ``"hw"``。**模拟器产出的 ``hw`` 事件是模拟器证据，不是硬件证据。**

关于「不是硬件证据」这条：本模块产出的每条事件都由 pty 上的**纯 Python 设备模拟器**应答，
``hw`` 只表示「走的是串口协议这条代码路径」，不表示真的有一块板子。真实串口 / 真实设备
（LB-020）必须人工实测，见 `docs/HW.md` §8。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..runtime.protocol import KIND_EXEC_EVENT, UNKNOWN, exec_event, validate_exec_event
from ..runtime.semantics import DIAGNOSTICS_BY_CODE as RUNTIME_DIAGNOSTICS
from ..runtime.semantics import RESULT_STATUSES
from ..vision.protocol import VERSION_PATTERN, diagnostic
from .frames import (
    HEADER_SIZE,
    KIND_COMMAND,
    KIND_EVENT,
    MAGIC,
    MAX_PAYLOAD,
    MAX_SEQ,
    WIRE_VERSION,
    Frame,
)

#: 硬件后端的协议版本（``docs/HW.md`` 的草案版本号；改帧格式 / 命令表 / 码表必须提升）。
HW_PROTOCOL_VERSION = "0.0.1"

#: ``docs/HW.md`` 的位置（相对仓库根）。
DOC_PATH = "docs/HW.md"

#: 事件里 ``backend`` 的取值（本层唯一产出）。
BACKEND_HW = "hw"

#: 设备侧上报的板子型号（本单只支持这一块，不做别的板子）。
BOARD = "esp32c3-supermini"


# ---------------------------------------------------------------------------
# 命令（主机 → 设备）与事件（设备 → 主机）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """一条命令：编号、名字、载荷必填字段、期望的回应事件、人读说明。"""

    code: int
    name: str
    body_keys: tuple[str, ...]
    responses: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class EventSpec:
    """一条事件：编号、名字、载荷必填字段、人读说明。"""

    code: int
    name: str
    body_keys: tuple[str, ...]
    description: str


#: 命令表（``KIND=1``，``TYPE < 0x80``）。
COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        code=0x01,
        name="HELLO",
        body_keys=("client",),
        responses=("HELLO",),
        description="握手：主机报客户端名，设备回型号 / 固件 / 协议版本；把本次链路的序号重置为 1",
    ),
    CommandSpec(
        code=0x02,
        name="BEGIN",
        body_keys=("run_id", "steps", "limits", "clock_ms_per_tick"),
        responses=("BEGIN_ACK",),
        description="开一次运行：设备把世界状态清零、记下 limits 与步数",
    ),
    CommandSpec(
        code=0x03,
        name="STEP",
        body_keys=("step_id", "op", "args", "cost_ticks"),
        responses=("STEP_OK", "STEP_ERROR"),
        description="执行一步（``step_id`` 幂等：同一 id 重发只回放上次结果，不重复作用）",
    ),
    CommandSpec(
        code=0x04,
        name="END",
        body_keys=("status",),
        responses=("END_ACK",),
        description="收尾：设备回报本次帧计数与步数",
    ),
    CommandSpec(
        code=0x05,
        name="PING",
        body_keys=("nonce",),
        responses=("PONG",),
        description="链路活性探针（心跳的上行方向）",
    ),
)

#: 事件表（``KIND=2``，``TYPE ≥ 0x80``）。
EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        code=0x81,
        name="HELLO",
        body_keys=("board", "firmware", "protocol_version"),
        description="握手回应：型号 / 固件版本 / 协议版本（``board`` 固定 esp32c3-supermini）",
    ),
    EventSpec(
        code=0x82,
        name="BEGIN_ACK",
        body_keys=("run_id", "steps"),
        description="运行已开始（设备已清零世界状态）",
    ),
    EventSpec(
        code=0x83,
        name="STEP_OK",
        body_keys=("step_id", "state_delta", "world"),
        description="一步完成：``state_delta`` 与 §7.1 的 ``finish.detail.state_delta`` 同形",
    ),
    EventSpec(
        code=0x84,
        name="STEP_ERROR",
        body_keys=("step_id", "code", "message"),
        description="一步被设备拒绝（如手上没有物品却 drop），``code`` 用 ``LB-R0xx`` 执行码",
    ),
    EventSpec(
        code=0x85,
        name="END_ACK",
        body_keys=("status",),
        description="收尾确认，附本次链路帧计数",
    ),
    EventSpec(
        code=0x86,
        name="PONG",
        body_keys=("nonce",),
        description="PING 的回应，附设备运行时长",
    ),
    EventSpec(
        code=0x87,
        name="HEARTBEAT",
        body_keys=("uptime_ms", "steps"),
        description="设备主动上行心跳（默认关闭；本单只定义帧格式，心跳周期留待 LB-034 实测）",
    ),
    EventSpec(
        code=0x88,
        name="FAULT",
        body_keys=("code", "message"),
        description="设备侧故障（未知命令 / 参数不合法等），``code`` 用 ``LB-H0xx``",
    ),
)

COMMANDS_BY_CODE: dict[int, CommandSpec] = {spec.code: spec for spec in COMMANDS}
COMMANDS_BY_NAME: dict[str, CommandSpec] = {spec.name: spec for spec in COMMANDS}
EVENTS_BY_CODE: dict[int, EventSpec] = {spec.code: spec for spec in EVENTS}
EVENTS_BY_NAME: dict[str, EventSpec] = {spec.name: spec for spec in EVENTS}

#: 命令 / 事件名字的全集（测试与文档核对用）。
COMMAND_NAMES: tuple[str, ...] = tuple(spec.name for spec in COMMANDS)
EVENT_NAMES: tuple[str, ...] = tuple(spec.name for spec in EVENTS)


def command_frame(name: str, seq: int, body: dict[str, Any]) -> Frame:
    """按命令名构造一帧；名字不在命令表里抛 ``KeyError``（实现缺陷，不是协议错误）。"""
    spec = COMMANDS_BY_NAME[name]
    return Frame.build(KIND_COMMAND, spec.code, seq, body)


def event_frame(name: str, seq: int, body: dict[str, Any]) -> Frame:
    """按事件名构造一帧。"""
    spec = EVENTS_BY_NAME[name]
    return Frame.build(KIND_EVENT, spec.code, seq, body)


#: 握手用的客户端名（日志里能看出是谁在说话）。
CLIENT_NAME = "liveblocks-hw"

#: 握手协商的协议版本字段名（``EVT_HELLO.protocol_version``）。
PROTOCOL_VERSION_KEY = "protocol_version"


# ---------------------------------------------------------------------------
# 错误模型：LB-H0xx
# ---------------------------------------------------------------------------

#: 断连：链路上再也读不到数据（EOF / EIO），运行以 ``runtime_error`` 结束，不阻塞。
DIAG_LINK_LOST = "LB-H001"
#: 超时：设备在期限内没有回应（读超时或握手超时），运行以 ``runtime_error`` 结束。
DIAG_LINK_TIMEOUT = "LB-H002"
#: 重连成功（info，不终止运行）。
DIAG_LINK_RESTORED = "LB-H003"
#: 坏帧：CRC 错、长度非法、帧头错、版本/类型不认识（丢帧后继续解析）。
DIAG_FRAME_CORRUPT = "LB-H004"
#: 序号乱序 / 重复（严格 +1，重连后从 1 重新开始）。
DIAG_SEQUENCE = "LB-H005"
#: 设备故障：设备回了 ``FAULT``，或回复与本条命令对不上。
DIAG_DEVICE_FAULT = "LB-H006"


@dataclass(frozen=True)
class HwDiagnostic:
    """``LB-H0xx`` 的登记项：标题、含义、严重度、终止时的 ``status``（``None`` = 不终止运行）。"""

    code: str
    title: str
    meaning: str
    severity: str
    status: str | None


#: ``LB-H0xx`` 码表。标题与严重度会与 `docs/HW.md` §6 的表格逐行核对。
HW_DIAGNOSTICS: tuple[HwDiagnostic, ...] = (
    HwDiagnostic(
        DIAG_LINK_LOST,
        "串口断连",
        "链路上读不到数据（EOF / EIO，模拟器拔线即此情形）；发出 ``error`` 事件后尝试重连，"
        "重连不成则运行以 ``runtime_error`` 结束，整个过程受响应超时约束，不会阻塞。",
        "error",
        "runtime_error",
    ),
    HwDiagnostic(
        DIAG_LINK_TIMEOUT,
        "设备响应超时",
        "设备在 ``response_timeout_ms`` 内没有回应（或握手超时）；运行以 ``runtime_error`` 结束。",
        "error",
        "runtime_error",
    ),
    HwDiagnostic(
        DIAG_LINK_RESTORED,
        "链路已恢复",
        "断连后重连成功、握手完成；运行继续。这是一条 ``log`` 事件，不终止运行。",
        "info",
        None,
    ),
    HwDiagnostic(
        DIAG_FRAME_CORRUPT,
        "坏帧",
        "CRC 不符、长度非法、帧头/版本/类型不认识；解析器丢帧后继续，调用方据此决定是否终止运行。",
        "error",
        "runtime_error",
    ),
    HwDiagnostic(
        DIAG_SEQUENCE,
        "序号乱序",
        "对端帧序号不是「上一帧 + 1」（含重复帧）；运行以 ``runtime_error`` 结束。",
        "error",
        "runtime_error",
    ),
    HwDiagnostic(
        DIAG_DEVICE_FAULT,
        "设备故障",
        "设备回了 ``FAULT`` 事件，或回复与本条命令对不上（例如收到无关事件）；运行以 ``runtime_error`` 结束。",
        "error",
        "runtime_error",
    ),
)

HW_DIAGNOSTICS_BY_CODE: dict[str, HwDiagnostic] = {entry.code: entry for entry in HW_DIAGNOSTICS}

#: ``LB-H0xx`` 码的形状。
CODE_PATTERN = re.compile(r"LB-H0\d\d")

#: 所有可用于 ``ExecResult.diagnostics`` 的码：执行码 ``LB-R0xx`` ∪ 链路码 ``LB-H0xx``。
RESULT_CODES: tuple[str, ...] = tuple(sorted(set(RUNTIME_DIAGNOSTICS) | set(HW_DIAGNOSTICS_BY_CODE)))


def hw_diagnostic(code: str, message: str) -> dict[str, Any]:
    """按协议 §9 的诊断对象形状构造一条 ``LB-H0xx`` 诊断（额外数值放事件的 ``detail`` 里）。"""
    entry = HW_DIAGNOSTICS_BY_CODE[code]
    return diagnostic(code, entry.severity, message)


# ---------------------------------------------------------------------------
# §7.1 事件流映射（backend = "hw"）
# ---------------------------------------------------------------------------


def hw_event(
    *,
    run_id: str,
    seq: int,
    at_ms: float,
    step_id: str,
    phase: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一条 ``backend:"hw"`` 的 §7.1 ``ExecEvent``。

    字段与 sim 完全同形：``schema_version`` / ``kind`` / ``run_id`` / ``seq`` / ``at_ms`` /
    ``step_id`` / ``phase`` / ``backend`` / ``detail``，**不加字段**。
    """
    return exec_event(
        run_id=run_id,
        seq=seq,
        at_ms=at_ms,
        step_id=step_id,
        phase=phase,
        detail=detail,
        backend=BACKEND_HW,
    )


def validate_hw_event(document: dict[str, Any]) -> None:
    """校验一条 hw 事件：§7.1 形状 + ``backend`` 必须是 ``"hw"``。"""
    validate_exec_event(document)
    if document.get("backend") != BACKEND_HW:
        raise ValueError(f"hw 事件流里的 backend 必须是 'hw'，实际 {document.get('backend')!r}")


def validate_hw_event_stream(events: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
    """校验整条 hw 事件流（含跨事件约束：``seq`` 严格递增、``at_ms`` 单调不减）。"""
    from ..runtime.protocol import validate_event_stream

    validate_event_stream(events)
    for position, event in enumerate(events):
        if event.get("kind") != KIND_EXEC_EVENT:
            raise ValueError(f"events[{position}]: kind 必须是 {KIND_EXEC_EVENT!r}")
        if event.get("backend") != BACKEND_HW:
            raise ValueError(f"events[{position}]: backend 必须是 {BACKEND_HW!r}，实际 {event.get('backend')!r}")


def validate_hw_result(document: dict[str, Any]) -> None:
    """校验 hw 的 ``ExecResult``：§7.2 形状 + 允许 ``LB-H0xx`` 诊断码。

    与 ``validate_exec_result`` 的差别只有一处：hw 的终止原因可能来自链路（``LB-H0xx``），
    因此码表取 ``LB-R0xx`` ∪ ``LB-H0xx``；``status`` 仍必须是 §7.2 的枚举值。
    """
    if not isinstance(document, dict):
        raise ValueError("ExecResult 必须是对象")
    if document.get("kind") != "ExecResult":
        raise ValueError(f"kind 必须是 'ExecResult'，实际 {document.get('kind')!r}")
    version = document.get("schema_version")
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"schema_version 必须是 'major.minor[.patch]'，实际 {version!r}")
    if not isinstance(document.get("run_id"), str) or not document["run_id"]:
        raise ValueError("run_id 必须是非空字符串")
    if document.get("backend") != BACKEND_HW:
        raise ValueError(f"hw 的 ExecResult.backend 必须是 'hw'，实际 {document.get('backend')!r}")
    status = document.get("status")
    if status not in RESULT_STATUSES and status != UNKNOWN:
        raise ValueError(f"status 必须属于 {list(RESULT_STATUSES)}，实际 {status!r}")
    steps = document.get("steps_executed")
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 0:
        raise ValueError(f"steps_executed 必须是非负整数，实际 {steps!r}")
    duration = document.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
        raise ValueError(f"duration_ms 必须是非负数字，实际 {duration!r}")
    diagnostics = document.get("diagnostics")
    if not isinstance(diagnostics, list):
        raise ValueError("diagnostics 必须是数组")
    for position, entry in enumerate(diagnostics):
        if not isinstance(entry, dict):
            raise ValueError(f"diagnostics[{position}] 必须是对象")
        code = str(entry.get("code", ""))
        if code not in RESULT_CODES:
            raise ValueError(
                f"diagnostics[{position}]: 诊断码 {code!r} 不在 {list(RESULT_CODES)} 里"
            )
        if entry.get("severity") not in ("info", "warning", "error"):
            raise ValueError(f"diagnostics[{position}]: severity 非法 {entry.get('severity')!r}")
        if not isinstance(entry.get("message"), str) or not entry["message"]:
            raise ValueError(f"diagnostics[{position}]: message 必须是非空字符串")


# ---------------------------------------------------------------------------
# 文档核对（与 LB-004/LB-005/LB-006/LB-007 同一做法）
# ---------------------------------------------------------------------------


def _row_for(text: str, code: str) -> str | None:
    for line in text.splitlines():
        if line.strip().startswith(f"| `{code}`"):
            return line
    return None


def hw_document_gaps(text: str) -> list[str]:
    """核对 `docs/HW.md` 是否写全了本模块的定义，返回缺失项（空列表 = 一致）。

    检查八件事：协议版本、帧头常量（MAGIC / 头长 / 载荷上限 / 序号上限）、CRC 参数、
    每条命令与事件的编号和名字、``LB-H0xx`` 每个码（含标题与严重度）、§7.1 映射与
    ``backend:"hw"``、传输抽象的三个实现名、以及「模拟器证据 ≠ 硬件证据」与
    「待人工验证」两段必须在文档里出现。
    """
    gaps: list[str] = []

    if HW_PROTOCOL_VERSION not in text:
        gaps.append(f"缺少 hw_protocol_version {HW_PROTOCOL_VERSION}")
    if f"0x{WIRE_VERSION:02X}" not in text and f"版本 ``{WIRE_VERSION}``" not in text:
        gaps.append(f"缺少线格式版本 {WIRE_VERSION}")
    if MAGIC.hex(" ").upper() not in text.upper():
        gaps.append(f"缺少 MAGIC {MAGIC.hex(' ').upper()}")
    if str(HEADER_SIZE) not in text:
        gaps.append(f"缺少帧头长度 {HEADER_SIZE}")
    if str(MAX_PAYLOAD) not in text:
        gaps.append(f"缺少载荷上限 {MAX_PAYLOAD}")
    if str(MAX_SEQ) not in text and "0xFFFF" not in text:
        gaps.append(f"缺少序号上限 {MAX_SEQ}")
    for token in ("0x1021", "0xFFFF", "CCITT"):
        if token not in text:
            gaps.append(f"缺少 CRC 参数 {token}")

    for spec in COMMANDS:
        row = _row_for(text, f"0x{spec.code:02X}")
        if row is None or spec.name not in row:
            gaps.append(f"命令表缺少 0x{spec.code:02X} {spec.name}")
    for spec in EVENTS:
        row = _row_for(text, f"0x{spec.code:02X}")
        if row is None or spec.name not in row:
            gaps.append(f"事件表缺少 0x{spec.code:02X} {spec.name}")

    for entry in HW_DIAGNOSTICS:
        row = _row_for(text, entry.code)
        if row is None:
            gaps.append(f"缺少诊断码 {entry.code}")
            continue
        if entry.title not in row:
            gaps.append(f"诊断码 {entry.code} 缺少标题「{entry.title}」")
        if entry.severity not in row:
            gaps.append(f"诊断码 {entry.code} 缺少严重度「{entry.severity}」")

    for token in ("`backend`", '"hw"', "§7.1"):
        if token not in text:
            gaps.append(f"缺少 §7.1 映射说明 {token}")
    for name in ("Transport", "PtyTransport", "SerialTransport"):
        if name not in text:
            gaps.append(f"缺少传输抽象 {name}")
    if "模拟器证据" not in text:
        gaps.append("缺少「模拟器证据」声明（不得把模拟结果当硬件验证）")
    if "待人工验证" not in text:
        gaps.append("缺少「待人工验证」小节")
    for name in COMMAND_NAMES + EVENT_NAMES:
        if name not in text:
            gaps.append(f"文档里没有出现 {name} 的名字")
    return gaps


__all__ = [
    "BACKEND_HW",
    "BOARD",
    "CLIENT_NAME",
    "CODE_PATTERN",
    "COMMANDS",
    "COMMANDS_BY_CODE",
    "COMMANDS_BY_NAME",
    "COMMAND_NAMES",
    "CommandSpec",
    "DIAG_DEVICE_FAULT",
    "DIAG_FRAME_CORRUPT",
    "DIAG_LINK_LOST",
    "DIAG_LINK_RESTORED",
    "DIAG_LINK_TIMEOUT",
    "DIAG_SEQUENCE",
    "DOC_PATH",
    "EVENTS",
    "EVENTS_BY_CODE",
    "EVENTS_BY_NAME",
    "EVENT_NAMES",
    "EventSpec",
    "HW_DIAGNOSTICS",
    "HW_DIAGNOSTICS_BY_CODE",
    "HW_PROTOCOL_VERSION",
    "HwDiagnostic",
    "PROTOCOL_VERSION_KEY",
    "RESULT_CODES",
    "command_frame",
    "event_frame",
    "hw_diagnostic",
    "hw_document_gaps",
    "hw_event",
    "validate_hw_event",
    "validate_hw_event_stream",
    "validate_hw_result",
]
