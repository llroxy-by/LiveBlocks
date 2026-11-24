"""生成固件侧的两个头文件（**都不入库**，构建时由 `scripts/build_firmware.sh` 产出）。

- `lb_protocol.h`：串口帧的线格式常量与命令 / 事件号，来源是
  `src/liveblocks/hw/{frames,protocol}.py`（LB-033 定的权威）；
- `lb_board_config.h`：引脚与串口参数，来源是 `config/board.esp32c3-supermini.json`
  （见 :meth:`liveblocks.firmware.board.BoardConfig.render_board_header`）。

为什么生成而不是手抄：C 与 Python 两端一旦各写一份常量，迟早会漂，
而「心跳帧逐字节一致」的验收正是靠这条不漂。
"""

from __future__ import annotations

from pathlib import Path

from .board import BOARD_CONFIG_PATH, BoardConfig, repo_root

#: 生成目录（相对仓库根）。不入库：见 `firmware/generated/README.md`。
GENERATED_DIR = Path("firmware/generated")

#: 板级头文件名。
BOARD_HEADER_NAME = "lb_board_config.h"

#: 协议头文件名。
PROTOCOL_HEADER_NAME = "lb_protocol.h"


def render_protocol_header() -> str:
    """从 `liveblocks.hw` 的常量与表渲染 `lb_protocol.h`。"""
    from ..hw.frames import (
        KIND_COMMAND,
        KIND_EVENT,
        MAGIC,
        MAX_PAYLOAD,
        MAX_SEQ,
        WIRE_VERSION,
    )
    from ..hw.protocol import (
        COMMANDS,
        DIAG_DEVICE_FAULT,
        DIAG_FRAME_CORRUPT,
        DIAG_LINK_LOST,
        DIAG_LINK_RESTORED,
        DIAG_LINK_TIMEOUT,
        DIAG_SEQUENCE,
        EVENTS,
        HW_PROTOCOL_VERSION,
    )

    magic = bytes(MAGIC)
    lines: list[str] = [
        "/* 自动生成，**不要手改**。",
        " *",
        " * 来源：src/liveblocks/hw/frames.py 与 src/liveblocks/hw/protocol.py"
        "（LB-033 定的线格式，见 docs/HW.md §2/§3/§4）。",
        " * 生成命令：python -m liveblocks firmware headers",
        f" * hw_protocol_version = {HW_PROTOCOL_VERSION}",
        " */",
        "#ifndef LB_PROTOCOL_H",
        "#define LB_PROTOCOL_H",
        "",
        f"#define LB_MAGIC_0 0x{magic[0]:02X}",
        f"#define LB_MAGIC_1 0x{magic[1]:02X}",
        f"#define LB_MAGIC_SIZE {len(magic)}",
        f"#define LB_WIRE_VERSION {WIRE_VERSION}",
        f"#define LB_KIND_COMMAND {KIND_COMMAND}",
        f"#define LB_KIND_EVENT {KIND_EVENT}",
        f"#define LB_MAX_PAYLOAD {MAX_PAYLOAD}",
        f"#define LB_MAX_SEQ {MAX_SEQ}",
        "",
        "/* 命令（KIND=1，TYPE < 0x80） */",
    ]
    for spec in COMMANDS:
        lines.append(f"#define LB_CMD_{spec.name} 0x{spec.code:02X}")
        lines.append(f'#define LB_CMD_{spec.name}_NAME "{spec.name}"')
    lines.append("")
    lines.append("/* 事件（KIND=2，TYPE >= 0x80） */")
    for spec in EVENTS:
        lines.append(f"#define LB_EVT_{spec.name} 0x{spec.code:02X}")
        lines.append(f'#define LB_EVT_{spec.name}_NAME "{spec.name}"')
    lines.append("")
    lines.append("/* 设备侧故障码（LB-H0xx） */")
    for name, code in (
        ("LINK_LOST", DIAG_LINK_LOST),
        ("LINK_TIMEOUT", DIAG_LINK_TIMEOUT),
        ("LINK_RESTORED", DIAG_LINK_RESTORED),
        ("FRAME_CORRUPT", DIAG_FRAME_CORRUPT),
        ("SEQUENCE", DIAG_SEQUENCE),
        ("DEVICE_FAULT", DIAG_DEVICE_FAULT),
    ):
        lines.append(f'#define LB_DIAG_{name} "{code}"')
    lines.append("")
    lines.append("/* 命令分发表：与 hw.protocol.COMMANDS 逐条一致（顺序也一样） */")
    lines.append("#define LB_COMMAND_TABLE_INIT \\")
    for index, spec in enumerate(COMMANDS):
        tail = "" if index == len(COMMANDS) - 1 else " \\"
        lines.append(f'    {{0x{spec.code:02X}, "{spec.name}"}}{"," if index != len(COMMANDS) - 1 else ""}{tail}')
    lines.append("")
    lines.append("#endif /* LB_PROTOCOL_H */")
    return "\n".join(lines) + "\n"


def write_generated_headers(
    out_dir: str | Path | None = None,
    *,
    config_path: str | Path = BOARD_CONFIG_PATH,
) -> tuple[Path, Path]:
    """把两个头文件写进 `out_dir`，返回 ``(协议头, 板级头)`` 路径。"""
    from .board import BoardConfig as _BoardConfig

    target = Path(out_dir) if out_dir is not None else repo_root() / GENERATED_DIR
    target.mkdir(parents=True, exist_ok=True)
    protocol_path = target / PROTOCOL_HEADER_NAME
    board_path = target / BOARD_HEADER_NAME
    protocol_path.write_text(render_protocol_header(), encoding="utf-8")
    board = _BoardConfig.load(config_path)
    board_path.write_text(board.render_board_header(), encoding="utf-8")
    return protocol_path, board_path


def load_board(config_path: str | Path = BOARD_CONFIG_PATH) -> BoardConfig:
    """读板级配置（转发到 :meth:`BoardConfig.load`，方便 CLI 一处 import）。"""
    return BoardConfig.load(config_path)


__all__ = [
    "BOARD_HEADER_NAME",
    "GENERATED_DIR",
    "PROTOCOL_HEADER_NAME",
    "load_board",
    "render_protocol_header",
    "write_generated_headers",
]
