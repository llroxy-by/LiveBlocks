"""在主机上用 gcc 编译**同一份固件源码**，拿到固件编码器的真实字节。

LB-034 有两条验收必须在主机上判死：

1. 心跳帧与 LB-033 的模拟器逐字节一致——要能跑固件的编码器（不是「读代码看像不像」）；
2. 启动日志 / 命令分发占位 / 灯效占位的行为——要能真跑一遍（不是「注释里说会打印」）。

所以 `firmware/main/` 里除 `main.c`、`lb_uart.c`（这两个只认 ESP-IDF）以外的源码全部只用
C11 标准库；`firmware/tools/lb_host_tool.c` 把它们打进一个命令行工具。
**这不会烧写、不碰串口、不需要 ESP-IDF**，因此可以进普通 pytest。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .board import BOARD_CONFIG_PATH, repo_root

#: 参与主机编译的源码（相对 `firmware/`）：可移植部分 + 主机工具本身。
HOST_SOURCES: tuple[str, ...] = (
    "main/lb_board.c",
    "main/lb_boot.c",
    "main/lb_dispatch.c",
    "main/lb_frame.c",
    "main/lb_heartbeat.c",
    "main/lb_json.c",
    "main/lb_led.c",
    "tools/lb_host_tool.c",
)

#: 主机编译参数（`-Werror` 是为了让「能编译」包含「没有警告」）。
HOST_CFLAGS: tuple[str, ...] = ("-std=c11", "-O1", "-Wall", "-Wextra", "-Werror")

#: 主机工具的可执行文件名。
HOST_TOOL_NAME = "lb_host_tool"


class HostToolError(Exception):
    """主机编译或运行失败（不是「跳过」：这是实现缺陷）。"""


@dataclass(frozen=True)
class HostTool:
    """编好的主机工具。"""

    path: Path
    generated_dir: Path
    command: tuple[str, ...]

    def run(self, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.path), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def heartbeat_bytes(self, seq: int, uptime_ms: str, steps: int) -> bytes:
        """固件编码器给出的一帧 HEARTBEAT（原始字节 + 它的载荷文本）。"""
        result = self.run("heartbeat", "--seq", str(seq), "--uptime-ms", uptime_ms, "--steps", str(steps))
        if result.returncode != 0:
            raise HostToolError(f"heartbeat 失败（退出码 {result.returncode}）：{result.stderr.strip()}")
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 2 or not lines[0].startswith("body="):
            raise HostToolError(f"heartbeat 输出形状不对：{result.stdout!r}")
        return bytes.fromhex(lines[1].strip())


def firmware_dir() -> Path:
    return repo_root() / "firmware"


def host_compiler() -> str:
    """主机 C 编译器（默认 gcc；可用 ``LB_HOST_CC`` 覆盖）。"""
    return os.environ.get("LB_HOST_CC", "gcc")


def build_host_tool(
    workdir: str | Path,
    *,
    config_path: str | Path = BOARD_CONFIG_PATH,
    compiler: str | None = None,
) -> HostTool:
    """生成头文件 → 编译主机工具。``workdir`` 用 pytest 的 tmp_path。"""
    from .headers import write_generated_headers

    cc = compiler or host_compiler()
    if shutil.which(cc) is None:
        raise HostToolError(f"找不到主机 C 编译器 {cc!r}（用 LB_HOST_CC 指定）")
    target = Path(workdir)
    generated_dir = target / "generated"
    protocol_header, board_header = write_generated_headers(generated_dir, config_path=config_path)
    binary = target / HOST_TOOL_NAME
    base = firmware_dir()
    sources = [str(base / relative) for relative in HOST_SOURCES]
    command = [
        cc,
        *HOST_CFLAGS,
        "-I",
        str(base / "main"),
        "-I",
        str(generated_dir),
        *sources,
        "-o",
        str(binary),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise HostToolError(
            "主机编译失败（这就是验收不过）：\n"
            f"命令：{' '.join(command)}\n"
            f"stdout：{result.stdout}\nstderr：{result.stderr}"
        )
    if not binary.is_file():
        raise HostToolError(f"编译没有产出 {binary}")
    return HostTool(
        path=binary,
        generated_dir=generated_dir,
        command=tuple(command),
    )


def format_uptime_ms(value: float) -> str:
    """与固件 ``lb_writer_ms`` 同口径的十进制文本：Python ``round(x, 3)`` 之后 ``repr``。

    模拟器侧也是 ``round((time.monotonic() - start) * 1000.0, 3)``，所以
    「模拟器给出的 uptime_ms」→ 这个文本 → 固件重新格式化，应当逐字节回到原样。
    """
    return repr(round(float(value), 3))


def find_idf() -> Path | None:
    """找一份 ESP-IDF（``IDF_PATH`` 或 ``~/esp/idf``）；找不到返回 None。"""
    candidate = os.environ.get("IDF_PATH")
    for path in ([Path(candidate)] if candidate else []) + [Path.home() / "esp" / "idf"]:
        if (path / "export.sh").is_file() and (path / "tools" / "idf.py").is_file():
            return path
    return None


def idf_toolchain_installed(idf_path: Path | None = None) -> bool:
    """ESP-IDF 的工具链是否真的装好（``~/.espressif/tools`` 里有 riscv32 工具链）。"""
    base = Path(os.environ.get("IDF_TOOLS_PATH", str(Path.home() / ".espressif")))
    tools = base / "tools"
    if not tools.is_dir():
        return False
    return any("riscv32" in path.name for path in tools.iterdir() if path.is_dir())


__all__ = [
    "HOST_CFLAGS",
    "HOST_SOURCES",
    "HOST_TOOL_NAME",
    "HostTool",
    "HostToolError",
    "build_host_tool",
    "find_idf",
    "firmware_dir",
    "format_uptime_ms",
    "host_compiler",
    "idf_toolchain_installed",
]
