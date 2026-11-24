"""`docs/FIRMWARE.md` 与实现的核对（正反两向），外加「编译证据真的写进 PROGRESS」的检查。

反向验证是重点：删章节、删错误码、删命令、去掉「只编译」、加一句「真机验证通过」、
把例帧的十六进制改一个字节……每一种都必须让核对报出缺口。否则「文档核对通过」
只是因为核对函数什么都没查。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from liveblocks.cli import build_parser
from liveblocks.firmware.docs import (
    DOC_PATH,
    DOC_VERSION,
    EXAMPLE_FRAME_SEQ,
    EXAMPLE_FRAME_STEPS,
    EXAMPLE_FRAME_UPTIME_MS,
    REQUIRED_COMMANDS,
    REQUIRED_SECTIONS,
    example_frame_hex,
    firmware_document_gaps,
    load_document,
)
from liveblocks.firmware.board import ERROR_CODES
from liveblocks.firmware.hosttool import HostTool, format_uptime_ms

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRESS = REPO_ROOT / "docs" / "PROGRESS.md"


@pytest.fixture(scope="module")
def markdown() -> str:
    return load_document()


def test_doc_exists_and_is_marked_as_a_draft(markdown: str) -> None:
    assert (REPO_ROOT / DOC_PATH).is_file()
    assert "草案" in markdown
    assert DOC_VERSION in markdown
    assert "待实测" in markdown
    assert "待人工验证" in markdown


def test_no_gaps_at_all(markdown: str) -> None:
    assert firmware_document_gaps(markdown) == ()


def test_every_error_code_and_title_is_documented(markdown: str) -> None:
    for code, title in ERROR_CODES.items():
        assert code in markdown
        assert title in markdown


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_removing_a_code_is_detected(markdown: str, code: str) -> None:
    broken = markdown.replace(code, "LB-F999")
    assert any(code in gap for gap in firmware_document_gaps(broken))


@pytest.mark.parametrize("section", REQUIRED_SECTIONS)
def test_removing_a_section_is_detected(markdown: str, section: str) -> None:
    broken = markdown.replace(section, "## 已删除的章节")
    gaps = firmware_document_gaps(broken)
    assert any(section in gap or "缺少章节" in gap for gap in gaps)


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_removing_a_command_is_detected(markdown: str, command: str) -> None:
    broken = markdown.replace(command, "echo 略")
    assert any(command in gap for gap in firmware_document_gaps(broken))


@pytest.mark.parametrize("phrase", ["只编译", "不烧写", "待实测", "待人工验证", "模拟器证据"])
def test_removing_a_required_phrase_is_detected(markdown: str, phrase: str) -> None:
    broken = markdown.replace(phrase, "略")
    assert firmware_document_gaps(broken) != ()


def test_claiming_hardware_success_is_detected(markdown: str) -> None:
    broken = markdown + "\n\n真机验证通过，烧写也没问题。\n"
    gaps = firmware_document_gaps(broken)
    assert any("没有实测依据" in gap for gap in gaps)


def test_removing_the_manual_steps_section_is_detected(markdown: str) -> None:
    broken = markdown.replace("## 7. 待人工验证", "## 7. 都验过了")
    gaps = firmware_document_gaps(broken)
    assert any("待人工验证" in gap for gap in gaps)


def test_removing_the_flash_command_is_detected(markdown: str) -> None:
    broken = markdown.replace("idf.py", "烧写命令")
    gaps = firmware_document_gaps(broken)
    assert any("idf.py" in gap for gap in gaps)


def test_removing_a_human_item_is_detected(markdown: str) -> None:
    broken = markdown.replace("串口日志", "输出")
    gaps = firmware_document_gaps(broken)
    assert any("serial-log" in gap or "串口日志" in gap for gap in gaps)


def test_documented_commands_really_parse() -> None:
    """文档里点名的 `liveblocks firmware ...` 子命令必须真能被 CLI 解析。"""
    parser = build_parser()
    commands = {
        match.group(1)
        for match in re.finditer(r"python -m liveblocks firmware ([a-z-]+)", load_document())
    }
    assert commands, "文档里没有 firmware 子命令"
    for command in sorted(commands):
        args = parser.parse_args(["firmware", command])
        assert args.firmware_command == command
        assert hasattr(args, "handler")


def test_example_frame_in_the_doc_is_what_the_firmware_encoder_produces(
    markdown: str, firmware_host_tool: HostTool
) -> None:
    """文档里那串十六进制必须是固件编码器真算出来的字节（改一个字节就该挂）。"""
    documented = bytes.fromhex(example_frame_hex(markdown))
    real = firmware_host_tool.heartbeat_bytes(
        EXAMPLE_FRAME_SEQ, format_uptime_ms(float(EXAMPLE_FRAME_UPTIME_MS)), EXAMPLE_FRAME_STEPS
    )
    assert documented == real, f"文档例帧与固件输出不一致：{documented.hex(' ')} != {real.hex(' ')}"


def test_example_frame_edit_is_detected(markdown: str, firmware_host_tool: HostTool) -> None:
    """反向验证：把例帧的最后一个字节改掉，比对必须失败。"""
    documented = example_frame_hex(markdown)
    tampered = documented[:-2] + ("00" if documented[-2:] != "00" else "01")
    broken = markdown.replace(documented, tampered)
    changed = bytes.fromhex(example_frame_hex(broken))
    real = firmware_host_tool.heartbeat_bytes(
        EXAMPLE_FRAME_SEQ, format_uptime_ms(float(EXAMPLE_FRAME_UPTIME_MS)), EXAMPLE_FRAME_STEPS
    )
    assert changed != real


# ---------------------------------------------------------------------------
# 编译证据必须真的写进 PROGRESS（Issue 的验收项），且不许吹牛
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def progress() -> str:
    return PROGRESS.read_text(encoding="utf-8")


def test_progress_records_the_build_command_and_artifact(progress: str) -> None:
    assert "bash scripts/build_firmware.sh" in progress
    assert "lb_firmware.bin" in progress
    assert re.search(r"sha256\s*`?\s*[0-9a-f]{64}", progress), "PROGRESS 里应当有产物的 sha256"
    assert "待人工验证" in progress


def test_progress_keeps_flashing_as_pending(progress: str) -> None:
    """LB-034 那一行必须把烧写 / 串口日志 / 复位自恢复写成「待人工验证」。"""
    rows = [line for line in progress.splitlines() if "LB-034" in line]
    assert rows, "PROGRESS 里没有 LB-034 的记录"
    joined = "\n".join(rows)
    for word in ("烧写", "串口日志", "复位"):
        assert word in joined, f"LB-034 的记录里缺少「{word}」"
    assert "待人工验证" in joined
    for forbidden in ("真机已验证", "烧写已验证", "硬件已验证"):
        assert forbidden not in joined


def test_progress_lists_the_new_tests(progress: str) -> None:
    assert "tests/test_firmware_" in progress
