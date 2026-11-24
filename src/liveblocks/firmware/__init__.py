"""LB-034 固件骨架的 Python 侧（板级配置 / 头文件生成 / 主机侧编译与核对）。

**这里没有任何能烧写设备的代码**，也不打开串口：

- :mod:`liveblocks.firmware.board` —— 板级配置的读取与零依赖校验、
  「固件源码里搜不到裸 GPIO 数字」的扫描；
- :mod:`liveblocks.firmware.headers` —— 生成 `lb_protocol.h`（源自 ``liveblocks.hw``）与
  `lb_board_config.h`（源自板级配置 JSON）；
- :mod:`liveblocks.firmware.hosttool` —— 用主机 gcc 编译**同一份固件源码**，拿到真实字节；
- :mod:`liveblocks.firmware.docs` —— `docs/FIRMWARE.md` 的机器核对。

证据口径：本包结论全部是离线可复现的静态证据（编译、编码、扫描、文档核对）；
真板子上的引脚、烧写、串口日志与复位自恢复属**待人工验证**（`docs/FIRMWARE.md` §7）。
"""

from __future__ import annotations

from .board import (
    BOARD_CONFIG_PATH,
    BOARD_SCHEMA_PATH,
    BOARD_SCHEMA_VERSION,
    ERROR_CODES,
    PENDING_MARK,
    SUPPORTED_BOARD_ID,
    BoardConfig,
    BoardConfigError,
    PinSpec,
    bare_gpio_violations,
    firmware_source_files,
    repo_root,
    validation_diagnostics,
)
from .docs import firmware_document_gaps, load_document
from .headers import (
    BOARD_HEADER_NAME,
    GENERATED_DIR,
    PROTOCOL_HEADER_NAME,
    render_protocol_header,
    write_generated_headers,
)
from .hosttool import (
    HOST_SOURCES,
    HostTool,
    HostToolError,
    build_host_tool,
    find_idf,
    format_uptime_ms,
    idf_toolchain_installed,
)

__all__ = [
    "BOARD_CONFIG_PATH",
    "BOARD_HEADER_NAME",
    "BOARD_SCHEMA_PATH",
    "BOARD_SCHEMA_VERSION",
    "BoardConfig",
    "BoardConfigError",
    "ERROR_CODES",
    "GENERATED_DIR",
    "HOST_SOURCES",
    "HostTool",
    "HostToolError",
    "PENDING_MARK",
    "PROTOCOL_HEADER_NAME",
    "PinSpec",
    "SUPPORTED_BOARD_ID",
    "bare_gpio_violations",
    "build_host_tool",
    "firmware_document_gaps",
    "firmware_source_files",
    "find_idf",
    "format_uptime_ms",
    "idf_toolchain_installed",
    "load_document",
    "render_protocol_header",
    "repo_root",
    "validation_diagnostics",
    "write_generated_headers",
]
