"""S6 硬件后端的**传输抽象** ``Transport``：pty、真实串口、内存环路三种实现。

``Transport`` 只负责「搬字节」：读一段、写一段、超时、断连。上面一层（:mod:`liveblocks.hw.device`
的设备端、:mod:`liveblocks.hw.executor` 的主机端）都只通过它说话，因此同一份协议代码在
pty 上、在真实串口上、在内存队列上跑的是**同一段代码**。

三种实现：

===================  ==============================================================
实现                 用途
===================  ==============================================================
``PtyTransport``     打开一个 pty 从端设备路径（设备模拟器占主端）——本单的默认路径
``SerialTransport``  打开真实串口设备并配置波特率 / 数据位 / 校验 / 停止位（**待人工验证**）
``MemoryTransport``  进程内一对队列，给协议单测用：确定、不依赖操作系统的 pty
===================  ==============================================================

两条硬约束：

- **读一定有超时**：``read(timeout_s)`` 最多等 ``timeout_s``，没数据返回 ``None``。上层因此
  永远不会被一次读调用挂住；「拔线」表现为 :class:`TransportClosed`，而不是无限阻塞。
- **断连有专门异常**：EOF / ``EIO`` 统一转成 ``TransportClosed``，上层据此发 ``LB-H001`` 事件。
"""

from __future__ import annotations

import errno
import os
import queue
import select
import termios
import threading
import tty
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

#: 默认读超时（秒）：一次 ``read`` 最多等这么久。
DEFAULT_READ_TIMEOUT_S = 1.0

#: 默认写超时（秒）。
DEFAULT_WRITE_TIMEOUT_S = 1.0

#: 一次 ``read`` 最多取多少字节。
READ_CHUNK = 4096


class TransportError(Exception):
    """传输层错误基类。"""


class TransportClosed(TransportError):
    """链路已断：EOF、设备消失（``EIO``）、或对端主动关闭。

    ``reason`` 是机器可读的原因（``eof`` / ``eio`` / ``closed`` / ``open``），上层据此写事件。
    """

    def __init__(self, reason: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.detail: dict[str, Any] = dict(detail)


class TransportOpenError(TransportError):
    """打不开（路径不存在、权限不足、已被占用）。这是「还没连上」，不是「用着用着断了」。

    ``detail`` 带定位用的数值（``path`` / ``errno``）。属性与 :class:`TransportClosed` 对齐，
    上层因此可以用同一段代码处理「打不开」和「断了」。
    """

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = dict(detail)


@dataclass(frozen=True)
class SerialConfig:
    """串口参数（真实串口用；pty 上只用于记录，不影响行为）。"""

    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: int = 1

    def describe(self) -> dict[str, Any]:
        return {
            "baudrate": self.baudrate,
            "bytesize": self.bytesize,
            "parity": self.parity,
            "stopbits": self.stopbits,
        }


#: ESP32-C3 SuperMini 的 USB 串口默认参数（8N1 / 115200；真实设备上的可用性待人工验证）。
DEFAULT_SERIAL = SerialConfig()


class Transport(ABC):
    """传输抽象：能读、能写、能关、能说明自己是什么。"""

    #: 传输种类（写进事件 ``detail.link`` 与 CLI 摘要）。
    kind: str = "transport"

    @abstractmethod
    def open(self) -> None:
        """打开链路；失败抛 :class:`TransportOpenError`。"""

    @abstractmethod
    def close(self) -> None:
        """关闭链路（幂等）。"""

    @abstractmethod
    def read(self, timeout_s: float = DEFAULT_READ_TIMEOUT_S) -> bytes | None:
        """读一段字节：有数据返回 ``bytes``，超时返回 ``None``，断连抛 :class:`TransportClosed`。"""

    @abstractmethod
    def write(self, data: bytes, timeout_s: float = DEFAULT_WRITE_TIMEOUT_S) -> int:
        """写完全部字节，返回写入字节数；链路已断抛 :class:`TransportClosed`。"""

    @property
    @abstractmethod
    def closed(self) -> bool:
        """是否已关闭 / 已断开。"""

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "closed": self.closed}

    def __enter__(self) -> "Transport":
        self.open()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class FdTransport(Transport):
    """基于文件描述符的传输：pty 从端与真实串口共用这一套读写逻辑。

    - 打开后立刻切成 **raw 模式**（8 位、无回显、无行缓冲）：串口上跑的是二进制帧，行缓冲会把
      不带换行的帧一直扣在缓冲里（这是最容易踩的坑，测试里用「不带换行的往返」钉住）。
    - 读走 ``select`` + 超时，写走 ``select`` + 部分写循环，所以两边都不会无限阻塞。
    """

    kind = "fd"

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        fd: int | None = None,
        name: str | None = None,
        read_chunk: int = READ_CHUNK,
    ) -> None:
        self._path = None if path is None else os.fspath(path)
        self._fd = fd
        self._owns_fd = fd is not None
        self._name = name or (self._path or (f"fd:{fd}" if fd is not None else "fd:?"))
        self._closed = fd is None
        self._read_chunk = int(read_chunk)
        self._lock = threading.Lock()

    # -- 生命周期 -----------------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if not self._closed:
                return
            if self._fd is not None and self._owns_fd:
                self._closed = False
                return
            if self._path is None:
                raise TransportOpenError("没有路径也没有 fd，打不开")
            try:
                fd = os.open(self._path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            except OSError as error:
                raise TransportOpenError(
                    f"打不开 {self._path}：{error.strerror or error}", path=self._path, errno=error.errno
                ) from error
            try:
                os.set_blocking(fd, True)
                self._configure(fd)
            except OSError as error:  # pragma: no cover - 非 tty 时才会走到
                os.close(fd)
                raise TransportOpenError(
                    f"配置 {self._path} 失败：{error.strerror or error}", path=self._path
                ) from error
            self._fd = fd
            self._owns_fd = True
            self._closed = False

    def _configure(self, fd: int) -> None:
        """切 raw 模式（pty 与串口都走这里；子类可再覆盖以套用波特率）。"""
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        # VMIN=1 / VTIME=0：配合下面的 select 使用；非阻塞交由 select 判断
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

    def close(self) -> None:
        with self._lock:
            fd, self._fd = self._fd, None
            self._closed = True
        if fd is not None and self._owns_fd:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - 已经关掉就算了
                pass

    @classmethod
    def from_fd(cls, fd: int, *, name: str | None = None, **kwargs: Any) -> "FdTransport":
        """接管一个已打开的 fd（pty 主端就是这种：没有路径；关闭时一并关掉）。"""
        transport = cls(fd=fd, name=name, **kwargs)
        transport._closed = False
        return transport

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def fd(self) -> int | None:
        return self._fd

    @property
    def name(self) -> str:
        return self._name

    # -- 读写 ---------------------------------------------------------------
    def read(self, timeout_s: float = DEFAULT_READ_TIMEOUT_S) -> bytes | None:
        fd = self._fd
        if self._closed or fd is None:
            raise TransportClosed("closed", f"{self._name} 已关闭")
        try:
            ready, _, _ = select.select([fd], [], [], max(0.0, float(timeout_s)))
        except (OSError, ValueError) as error:
            raise TransportClosed("eio", f"{self._name} select 失败：{error}") from error
        if not ready:
            return None
        try:
            chunk = os.read(fd, self._read_chunk)
        except BlockingIOError:
            return None
        except OSError as error:
            # pty 主端关闭后，从端 read 会得到 EIO —— 这就是「拔线」
            reason = "eio" if error.errno == errno.EIO else "eof"
            raise TransportClosed(reason, f"{self._name} 读失败：{error.strerror or error}", errno=error.errno) from error
        if not chunk:
            raise TransportClosed("eof", f"{self._name} 读到 EOF（对端已关闭）")
        return chunk

    def write(self, data: bytes, timeout_s: float = DEFAULT_WRITE_TIMEOUT_S) -> int:
        fd = self._fd
        if self._closed or fd is None:
            raise TransportClosed("closed", f"{self._name} 已关闭")
        payload = bytes(data)
        written = 0
        while written < len(payload):
            try:
                _, writable, _ = select.select([], [fd], [], max(0.0, float(timeout_s)))
            except (OSError, ValueError) as error:
                raise TransportClosed("eio", f"{self._name} select 失败：{error}") from error
            if not writable:
                raise TransportClosed("eio", f"{self._name} 写超时（{timeout_s:g}s 内不可写）")
            try:
                written += os.write(fd, payload[written:])
            except OSError as error:
                raise TransportClosed("eio", f"{self._name} 写失败：{error.strerror or error}", errno=error.errno) from error
        return written

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self._name, "closed": self._closed}


class PtyTransport(FdTransport):
    """打开 pty **从端**设备路径（``/dev/pts/N``）。设备模拟器占主端，主机走这条。

    纯软件闭环里的默认传输：不需要任何硬件、不占用真实串口。
    """

    kind = "pty"


class SerialTransport(FdTransport):
    """打开**真实串口**设备（``/dev/ttyUSB0`` 之类）并套用 :class:`SerialConfig`。

    ⚠️ 本单没有真实硬件联调（属 LB-020）：这条路径只在 pty 上跑通代码，**真机行为待人工验证**，
    见 `docs/HW.md` §8。不要把这里的结果当成「串口可用」的证据。
    """

    kind = "serial"

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        config: SerialConfig = DEFAULT_SERIAL,
        name: str | None = None,
        read_chunk: int = READ_CHUNK,
    ) -> None:
        super().__init__(path, name=name, read_chunk=read_chunk)
        self.config = config

    def _configure(self, fd: int) -> None:
        iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = termios.tcgetattr(fd)
        iflag &= ~(
            termios.IGNBRK
            | termios.BRKINT
            | termios.PARMRK
            | termios.ISTRIP
            | termios.INLCR
            | termios.IGNCR
            | termios.ICRNL
            | termios.IXON
        )
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB)
        cflag |= termios.CS8 if self.config.bytesize == 8 else termios.CS7
        if self.config.parity == "E":
            cflag |= termios.PARENB
        elif self.config.parity == "O":
            cflag |= termios.PARENB | termios.PARODD
        if self.config.stopbits == 2:
            cflag |= termios.CSTOPB
        cflag |= termios.CREAD | termios.CLOCAL
        speeds = {2400: termios.B2400, 9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400,
                  57600: termios.B57600, 115200: termios.B115200, 230400: termios.B230400}
        speed = speeds.get(int(self.config.baudrate))
        if speed is None:
            raise OSError(errno.EINVAL, f"不支持的波特率 {self.config.baudrate}")
        cc[termios.VMIN] = 1
        cc[termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, speed, speed, cc])

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "closed": self.closed,
            "serial": self.config.describe(),
        }


class MemoryTransport(Transport):
    """进程内的一对队列：给协议单测用，确定且不需要操作系统的 pty。

    ``memory_pair()`` 返回 ``(a, b)``：往 ``a`` 写，``b`` 读得到，反之亦然。
    ``a.unplug()`` 会关掉两端（模拟拔线），此后双方读写都抛 :class:`TransportClosed`。
    """

    kind = "memory"

    def __init__(self, name: str = "mem") -> None:
        self._inbox: queue.Queue[bytes | None] = queue.Queue()
        self._outbox: queue.Queue[bytes | None] | None = None
        self._name = name
        self._closed = False

    def attach(self, peer: "MemoryTransport") -> None:
        self._outbox = peer._inbox

    def open(self) -> None:
        if self._closed:
            raise TransportOpenError("内存链路已断开，不能重新打开")

    def close(self) -> None:
        self._closed = True

    def unplug(self) -> None:
        """拔线：两端一起关掉，并唤醒正在等数据的读方。"""
        self._closed = True
        self._inbox.put(None)
        if self._outbox is not None:
            self._outbox.put(None)

    @property
    def closed(self) -> bool:
        return self._closed

    def read(self, timeout_s: float = DEFAULT_READ_TIMEOUT_S) -> bytes | None:
        if self._closed:
            raise TransportClosed("closed", f"{self._name} 已断开")
        try:
            item = self._inbox.get(timeout=max(0.0, float(timeout_s)))
        except queue.Empty:
            return None
        if item is None:
            self._closed = True
            raise TransportClosed("eof", f"{self._name} 对端已关闭")
        return item

    def write(self, data: bytes, timeout_s: float = DEFAULT_WRITE_TIMEOUT_S) -> int:
        if self._closed or self._outbox is None:
            raise TransportClosed("closed", f"{self._name} 已断开")
        self._outbox.put(bytes(data))
        return len(data)

    def describe(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self._name, "closed": self._closed}


def memory_pair(*, name: str = "mem") -> tuple[MemoryTransport, MemoryTransport]:
    """一对相连的内存传输（写一边、另一边读得到）。"""
    left = MemoryTransport(f"{name}-a")
    right = MemoryTransport(f"{name}-b")
    left.attach(right)
    right.attach(left)
    return left, right


def open_transport(spec: str) -> Transport:
    """按 ``"pty:/dev/pts/3"`` / ``"serial:/dev/ttyUSB0"`` 的说法造一个传输（CLI 用）。"""
    if ":" not in spec:
        raise ValueError(f"传输写法应为 <kind>:<path>，实际 {spec!r}")
    kind, path = spec.split(":", 1)
    if kind == "pty":
        return PtyTransport(path)
    if kind == "serial":
        return SerialTransport(path)
    raise ValueError(f"未知传输种类 {kind!r}（支持 pty / serial）")


__all__ = [
    "DEFAULT_READ_TIMEOUT_S",
    "DEFAULT_SERIAL",
    "DEFAULT_WRITE_TIMEOUT_S",
    "FdTransport",
    "MemoryTransport",
    "PtyTransport",
    "READ_CHUNK",
    "SerialConfig",
    "SerialTransport",
    "Transport",
    "TransportClosed",
    "TransportError",
    "TransportOpenError",
    "memory_pair",
    "open_transport",
]
