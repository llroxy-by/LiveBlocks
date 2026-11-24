"""`docs/FIRMWARE.md` 与实现的核对（文档缺口）。

与其它层的 ``*_document_gaps`` 同一思路：**入库文档必须能被机器核对**——章节、错误码、
命令行、路径、「只编译不烧写」的口径、以及「待人工验证」的步骤是否被悄悄改成「已验证」。
`tests/test_firmware_docs.py` 还会做反向验证（删一行 / 改一句就必须报缺口），防止核对函数
退化成永真式。
"""

from __future__ import annotations

import re
from pathlib import Path

from .board import (
    BOARD_CONFIG_PATH,
    BOARD_SCHEMA_PATH,
    ERROR_CODES,
    PENDING_MARK,
    repo_root,
)

#: 文档路径。
DOC_PATH = Path("docs/FIRMWARE.md")

#: 文档版本（草案；改配置格式 / 帧用法必须提升）。
DOC_VERSION = "0.0.1"

#: 文档必须有的章节标题（精确到 `## ` 行）。
REQUIRED_SECTIONS: tuple[str, ...] = (
    "## 1. 范围与不做",
    "## 2. 板级配置与「零硬编码 GPIO」",
    "## 3. 构建：只编译、不烧写",
    "## 4. 帧格式与串口心跳",
    "## 5. 烧写与串口日志（人来做）",
    "## 6. 命令分发占位与灯效占位",
    "## 7. 待人工验证",
    "## 8. 已知限制",
    "## 9. 错误码与证据索引",
)

#: 文档里必须出现的命令行（可复制粘贴）。
REQUIRED_COMMANDS: tuple[str, ...] = (
    "python -m liveblocks firmware check",
    "python -m liveblocks firmware pins",
    "python -m liveblocks firmware headers",
    "python -m liveblocks firmware scan",
    "bash scripts/build_firmware.sh",
    "bash scripts/check.sh",
)

#: 文档里必须出现的路径（且必须真实存在）。
REQUIRED_PATHS: tuple[str, ...] = (
    "config/board.esp32c3-supermini.json",
    "config/board.schema.json",
    "scripts/build_firmware.sh",
    "firmware/main/main.c",
    "firmware/tools/lb_host_tool.c",
    "docs/HW.md",
)

#: 「待人工验证」三件事：文档里必须留下这三句与它们的人工步骤。
HUMAN_ITEMS: tuple[tuple[str, str], ...] = (
    ("flashing", "烧写"),
    ("serial-log", "串口日志"),
    ("reset-recovery", "复位自恢复"),
)

#: 必须出现的口径用语（少一个就说明有人把「只编译」或「待实测」写丢了）。
REQUIRED_PHRASES: tuple[str, ...] = (
    "只编译",
    "不烧写",
    PENDING_MARK,
    "待人工验证",
    "模拟器证据",
)

#: 文档里禁止出现的说法（没有实测就宣布通过）。
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "真机验证通过",
    "硬件已验证",
    "实测通过（烧写）",
    "烧写已验证",
)

#: 例帧在文档里的标记行；十六进制必须与固件编码器的真实输出一致（测试比对）。
EXAMPLE_FRAME_MARKER = "例帧（seq=1, steps=3, uptime_ms=12.5）："

#: 例帧的输入参数。
EXAMPLE_FRAME_SEQ = 1
EXAMPLE_FRAME_STEPS = 3
EXAMPLE_FRAME_UPTIME_MS = "12.5"

_HEX_LINE = re.compile(r"^[0-9a-f]{2}(?: [0-9a-f]{2})*$")


def load_document(doc_path: str | Path | None = None) -> str:
    path = Path(doc_path) if doc_path is not None else repo_root() / DOC_PATH
    return path.read_text(encoding="utf-8")


def firmware_document_gaps(text: str) -> tuple[str, ...]:
    """返回文档缺口（空 = 文档与实现一致）。"""
    gaps: list[str] = []

    if "草案" not in text:
        gaps.append("缺少「草案」状态说明")
    if DOC_VERSION not in text:
        gaps.append(f"缺少版本号 {DOC_VERSION}")

    for section in REQUIRED_SECTIONS:
        if section not in text:
            gaps.append(f"缺少章节：{section}")

    for code, title in ERROR_CODES.items():
        if code not in text:
            gaps.append(f"缺少错误码：{code}")
        if title not in text:
            gaps.append(f"缺少错误码标题：{code} {title}")

    for command in REQUIRED_COMMANDS:
        if command not in text:
            gaps.append(f"缺少命令：{command}")

    root = repo_root()
    for relative in REQUIRED_PATHS:
        if relative not in text:
            gaps.append(f"文档没有提到路径：{relative}")
        if not (root / relative).exists():
            gaps.append(f"文档提到的路径不存在：{relative}")

    for phrase in REQUIRED_PHRASES:
        if phrase not in text:
            gaps.append(f"缺少口径用语：{phrase}")

    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            gaps.append(f"出现了没有实测依据的说法：{phrase}")

    # 「待人工验证」那一节必须把三件事与「怎么验 / 通过标准」写清楚
    human_section = _section_text(text, "## 7. 待人工验证")
    if human_section is None:
        gaps.append("找不到「待人工验证」章节")
    else:
        for key, phrase in HUMAN_ITEMS:
            if phrase not in human_section:
                gaps.append(f"「待人工验证」缺少一项：{key}（{phrase}）")
        for column in ("怎么验", "通过标准"):
            if column not in human_section:
                gaps.append(f"「待人工验证」缺少列：{column}")
        if "idf.py" not in human_section:
            gaps.append("「待人工验证」缺少真实烧写命令（idf.py）")

    # 例帧：标记行后必须紧跟一行十六进制
    example = _example_hex(text)
    if example is None:
        gaps.append(f"缺少例帧或例帧格式不对（标记行：{EXAMPLE_FRAME_MARKER}）")

    return tuple(gaps)


def _section_text(text: str, heading: str) -> str | None:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == heading:
            start = index + 1
            break
    if start is None:
        return None
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        collected.append(line)
    return "\n".join(collected)


def _example_hex(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if EXAMPLE_FRAME_MARKER in line:
            for candidate in lines[index + 1 : index + 5]:
                stripped = candidate.strip().strip("`").strip()
                if _HEX_LINE.match(stripped):
                    return stripped
            return None
    return None


def example_frame_hex(text: str) -> str:
    """文档里的例帧十六进制（供测试与固件编码器比对）。"""
    found = _example_hex(text)
    if found is None:
        raise ValueError("文档里没有可用的例帧")
    return found


__all__ = [
    "DOC_PATH",
    "DOC_VERSION",
    "EXAMPLE_FRAME_MARKER",
    "EXAMPLE_FRAME_SEQ",
    "EXAMPLE_FRAME_STEPS",
    "EXAMPLE_FRAME_UPTIME_MS",
    "FORBIDDEN_PHRASES",
    "HUMAN_ITEMS",
    "REQUIRED_COMMANDS",
    "REQUIRED_PATHS",
    "REQUIRED_PHRASES",
    "REQUIRED_SECTIONS",
    "example_frame_hex",
    "firmware_document_gaps",
    "load_document",
]
