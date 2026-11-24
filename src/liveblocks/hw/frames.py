"""S6 硬件后端的**串口帧格式**（`docs/HW.md` §2，草案 ``hw_protocol_version`` 0.0.1）。

一帧的线格式（小端；``LEN`` 只算 ``PAYLOAD``）：

======  ======  ============================================================
字段    长度    说明
======  ======  ============================================================
MAGIC   2 B     ``b"LB"``（0x4C 0x42）——重同步锚点
VER     1 B     线格式版本，当前 ``1``（对应文档的 ``0.0.1``）
KIND    1 B     ``1`` = 命令（主机→设备），``2`` = 事件（设备→主机）
TYPE    1 B     命令 / 事件类型；命令 < 0x80，事件 ≥ 0x80
SEQ     2 B LE  发送方在**本次链路**内的帧序号，从 1 开始、每帧 +1、0xFFFF 后回到 1
LEN     2 B LE  ``PAYLOAD`` 的字节数，上限 :data:`MAX_PAYLOAD`
PAYLOAD LEN B   UTF-8 JSON 对象（键排序、无空格的规范写法，见 :func:`encode_body`）
CRC     2 B LE  CRC-16/CCITT-FALSE（poly 0x1021、init 0xFFFF），覆盖 VER..PAYLOAD
======  ======  ============================================================

为什么是这个形状：

- **帧头 + 长度 + CRC + 序号**四件事正是本单要求的四项，且都是「固件能一口气实现」的量级：
  CRC 查表 256 项、JSON 只用来传小对象（一帧 ≤ ``MAX_PAYLOAD``）。
- **JSON 载荷**保留可读性（人能直接看日志），并且键排序让同一对象在 C/Python 两端能逐字节一致
  （LB-034 的「心跳帧逐字节一致」验收要靠这条）。
- **重同步**：解析器遇到坏 MAGIC 只丢 1 字节继续找下一次 MAGIC；CRC 错或长度非法则丢掉整帧
  候选并记一条错误。三种异常（截断、CRC 错、序号乱序）各自有独立的错误原因，测试逐个覆盖。

本模块只做「字节 ↔ :class:`Frame`」，不理解业务；业务常量在 :mod:`liveblocks.hw.protocol`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

#: 帧头固定前缀（重同步锚点）。
MAGIC: bytes = b"LB"

#: 线格式版本。改帧头布局 / CRC 参数 / 载荷编码必须提升它（并在 `docs/DECISIONS.md` 记一条）。
WIRE_VERSION: int = 1

#: ``KIND`` 取值：命令（主机→设备）与事件（设备→主机）。
KIND_COMMAND: int = 1
KIND_EVENT: int = 2
KINDS: tuple[int, ...] = (KIND_COMMAND, KIND_EVENT)

#: 帧头字节数（含 MAGIC）。
HEADER_SIZE: int = 9

#: CRC 字节数。
CRC_SIZE: int = 2

#: ``PAYLOAD`` 上限：一帧最多 512 字节（ESP32-C3 的 UART 缓冲友好，也够一步命令/事件用）。
MAX_PAYLOAD: int = 512

#: 最短合法帧长度（空载荷）。
MIN_FRAME_SIZE: int = HEADER_SIZE + CRC_SIZE

#: ``SEQ`` 的上限；超过回到 1。
MAX_SEQ: int = 0xFFFF


def next_seq(seq: int) -> int:
    """序号递增：``0xFFFF`` 之后回到 ``1``（``0`` 不用，避免「未初始化为 0」与 0 号帧混淆）。"""
    value = int(seq) + 1
    return 1 if value > MAX_SEQ else value


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT-FALSE：poly 0x1021、init 0xFFFF、不反射、xorout 0。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


class FrameError(Exception):
    """帧层的错误。``reason`` 是机器可读的原因，``detail`` 带定位用的数值。

    原因取值（文档 §2.4 与 §6 的错误模型逐条对应）：

    ================  =====================================================
    reason            含义
    ================  =====================================================
    ``truncated``     数据还不够一帧（截断）——**不是**错误，等更多字节
    ``bad_magic``     帧头不是 ``LB``（丢 1 字节重同步）
    ``bad_version``   线格式版本不认识
    ``bad_kind``      ``KIND`` 不是 1 / 2
    ``bad_type``      ``TYPE`` 与 ``KIND`` 不匹配，或不在命令 / 事件表里
    ``length``        ``LEN`` 超过 :data:`MAX_PAYLOAD`
    ``crc``           CRC 校验失败
    ``payload``       载荷不是 UTF-8 JSON 对象
    ``sequence``      序号乱序 / 重复
    ================  =====================================================
    """

    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail: dict[str, Any] = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "message": self.message, **self.detail}


def encode_body(body: dict[str, Any]) -> bytes:
    """按**规范写法**编码载荷：键排序、紧凑分隔符、非 ASCII 原样。

    键排序是「同一对象在 C 与 Python 两端逐字节一致」的唯一前提（LB-034 的心跳比对要靠它），
    所以编码只走这一个函数，不用 ``json.dumps`` 的默认顺序。
    """
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode_body(payload: bytes) -> dict[str, Any]:
    """载荷 → 对象；不是 UTF-8 JSON 对象时抛 ``FrameError('payload')``。"""
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrameError("payload", f"载荷不是 UTF-8 JSON：{error}", size=len(payload)) from error
    if not isinstance(body, dict):
        raise FrameError("payload", f"载荷必须是 JSON 对象，实际 {type(body).__name__}", size=len(payload))
    return body


@dataclass(frozen=True)
class Frame:
    """一帧：``kind`` / ``type`` / ``seq`` + 已编码的 ``payload``。"""

    kind: int
    type: int
    seq: int
    payload: bytes
    version: int = WIRE_VERSION

    def encode(self) -> bytes:
        """编码成字节（含帧头、长度与 CRC）。"""
        if not 1 <= int(self.seq) <= MAX_SEQ:
            raise ValueError(f"seq 必须在 1..{MAX_SEQ}，实际 {self.seq!r}")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload 超过 {MAX_PAYLOAD} 字节（实际 {len(self.payload)}）")
        header = bytearray()
        header += MAGIC
        header.append(self.version)
        header.append(self.kind)
        header.append(self.type)
        header += int(self.seq).to_bytes(2, "little")
        header += len(self.payload).to_bytes(2, "little")
        body = bytes(header[2:]) + bytes(self.payload)
        return bytes(header) + bytes(self.payload) + crc16_ccitt(body).to_bytes(2, "little")

    def body(self) -> dict[str, Any]:
        """载荷解析成对象。"""
        return decode_body(self.payload)

    def hexdump(self) -> str:
        """给日志用的十六进制形态（帧头 + 载荷 + CRC）。"""
        return self.encode().hex(" ")

    @classmethod
    def build(
        cls,
        kind: int,
        type: int,
        seq: int,
        body: dict[str, Any] | None = None,
        *,
        version: int = WIRE_VERSION,
    ) -> "Frame":
        """用对象构造一帧（载荷走 :func:`encode_body`）。"""
        return cls(kind=kind, type=type, seq=seq, payload=encode_body({} if body is None else body), version=version)


def decode_frame(data: bytes | bytearray) -> tuple[Frame, int]:
    """从缓冲区开头解一帧，返回 ``(帧, 消耗字节数)``。

    数据不够一帧时抛 ``FrameError('truncated')``——调用方据此保留缓冲等更多字节（不是错误）。
    """
    buffer = bytes(data)
    if len(buffer) < len(MAGIC):
        raise FrameError("truncated", f"还不到帧头（{len(buffer)}/{len(MAGIC)} 字节）", have=len(buffer))
    if buffer[: len(MAGIC)] != MAGIC:
        raise FrameError("bad_magic", f"帧头不是 {MAGIC!r}", head=buffer[: len(MAGIC)].hex(" "))
    if len(buffer) < HEADER_SIZE:
        raise FrameError("truncated", f"帧头不完整（{len(buffer)}/{HEADER_SIZE} 字节）", have=len(buffer))
    version = buffer[2]
    if version != WIRE_VERSION:
        raise FrameError("bad_version", f"线格式版本 {version} 不认识（当前 {WIRE_VERSION}）", version=version)
    kind = buffer[3]
    if kind not in KINDS:
        raise FrameError("bad_kind", f"KIND {kind} 不是命令(1)也不是事件(2)", kind=kind)
    frame_type = buffer[4]
    if (kind == KIND_COMMAND and frame_type >= 0x80) or (kind == KIND_EVENT and frame_type < 0x80):
        raise FrameError(
            "bad_type",
            f"TYPE 0x{frame_type:02X} 与 KIND {kind} 不匹配（命令 < 0x80、事件 ≥ 0x80）",
            kind=kind,
            type=frame_type,
        )
    seq = int.from_bytes(buffer[5:7], "little")
    length = int.from_bytes(buffer[7:9], "little")
    if length > MAX_PAYLOAD:
        raise FrameError("length", f"LEN {length} 超过上限 {MAX_PAYLOAD}", length=length, limit=MAX_PAYLOAD)
    total = HEADER_SIZE + length + CRC_SIZE
    if len(buffer) < total:
        raise FrameError("truncated", f"帧不完整（{len(buffer)}/{total} 字节）", have=len(buffer), need=total)
    payload = buffer[HEADER_SIZE : HEADER_SIZE + length]
    expected = crc16_ccitt(buffer[2 : HEADER_SIZE + length])
    actual = int.from_bytes(buffer[HEADER_SIZE + length : total], "little")
    if expected != actual:
        raise FrameError(
            "crc",
            f"CRC 不符：算出 0x{expected:04X}，帧里是 0x{actual:04X}",
            expected=expected,
            actual=actual,
        )
    return Frame(kind=kind, type=frame_type, seq=seq, payload=payload, version=version), total


class FrameParser:
    """增量解析器：``feed`` 任意切片，拿回已完整的帧；坏帧记进 :attr:`errors` 后继续。

    三种异常的处理口径（与 `docs/HW.md` §6 一致）：

    - **截断**：留在缓冲里，等更多字节（不产生错误、不丢数据）；
    - **CRC 错 / 长度非法**：丢掉这一帧候选（按帧头里的 ``LEN`` 决定丢多少），记一条错误后继续；
    - **坏 MAGIC**：只丢 1 字节，继续找下一个 ``LB``（``skipped_bytes`` 记下丢了多少）。
    """

    def __init__(self, *, max_payload: int = MAX_PAYLOAD) -> None:
        self._buffer = bytearray()
        self.max_payload = int(max_payload)
        self.errors: list[FrameError] = []
        self.skipped_bytes = 0

    @property
    def pending(self) -> bytes:
        """还没凑成一帧的字节（截断时就是「不完整的那一截」）。"""
        return bytes(self._buffer)

    def feed(self, data: bytes) -> list[Frame]:
        """喂一段字节，返回本次凑齐的帧（顺序保持）。"""
        if data:
            self._buffer += data
        frames: list[Frame] = []
        while self._buffer:
            try:
                frame, consumed = decode_frame(self._buffer)
            except FrameError as error:
                if error.reason == "truncated":
                    break
                if error.reason == "bad_magic":
                    # 丢 1 字节继续找 MAGIC（真正的坏字节流也不至于让解析器卡死）
                    del self._buffer[0]
                    self.skipped_bytes += 1
                    self.errors.append(error)
                    continue
                self.errors.append(error)
                # 已知帧头但内容不可信：按帧头里的 LEN 丢掉整个候选帧，避免二次解析错位
                length = int.from_bytes(self._buffer[7:9], "little") if len(self._buffer) >= HEADER_SIZE else 0
                drop = min(len(self._buffer), HEADER_SIZE + length + CRC_SIZE)
                del self._buffer[:drop]
                continue
            frames.append(frame)
            del self._buffer[:consumed]
        return frames

    def drain_errors(self) -> list[FrameError]:
        """取出并清空已累积的错误。"""
        errors, self.errors = self.errors, []
        return errors


class SequenceTracker:
    """一个方向的序号校验：期望值逐帧 +1（``0xFFFF`` 后回到 1），否则抛 ``sequence`` 错误。"""

    def __init__(self) -> None:
        self.expected = 1
        self.seen = 0

    def reset(self) -> None:
        """新链路（重连后）从 1 重新开始。"""
        self.expected = 1
        self.seen = 0

    def check(self, seq: int) -> int:
        if int(seq) != self.expected:
            raise FrameError(
                "sequence",
                f"序号乱序：期望 {self.expected}，收到 {seq}",
                expected=self.expected,
                actual=int(seq),
                seen=self.seen,
            )
        self.seen += 1
        self.expected = next_seq(self.expected)
        return int(seq)


def encode_frames(frames: Iterable[Frame]) -> bytes:
    """把若干帧拼成字节流（测试与离线夹具用）。"""
    return b"".join(frame.encode() for frame in frames)


__all__ = [
    "CRC_SIZE",
    "Frame",
    "FrameError",
    "FrameParser",
    "HEADER_SIZE",
    "KINDS",
    "KIND_COMMAND",
    "KIND_EVENT",
    "MAGIC",
    "MAX_PAYLOAD",
    "MAX_SEQ",
    "MIN_FRAME_SIZE",
    "SequenceTracker",
    "WIRE_VERSION",
    "crc16_ccitt",
    "decode_body",
    "decode_frame",
    "encode_body",
    "encode_frames",
    "next_seq",
]
