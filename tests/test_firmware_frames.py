"""固件骨架的**行为**证据：用主机 gcc 编出的同一份源码真跑一遍。

跑的是 `firmware/main/` 里除 `main.c` / `lb_uart.c`（只认 ESP-IDF）以外的源码，
入口是 `firmware/tools/lb_host_tool.c`。所以这里能断言的是：

- CRC / 帧编码与 `src/liveblocks/hw/frames.py` 逐字节一致（不是「看起来一样」）；
- 心跳载荷的键排序与浮点毫秒格式和 Python 的 `encode_body` 对得上；
- 命令分发占位真的认命令号、真的回 `LB-H006`、坏帧真的丢；
- 启动日志与灯效占位真的把配置里的引脚打出来了。

**这里不证明任何真机行为**：烧写、串口收发、复位自恢复都是「待人工验证」
（`docs/FIRMWARE.md` §7）。
"""

from __future__ import annotations

import pytest

from liveblocks.firmware import HOST_SOURCES, HostTool, format_uptime_ms
from liveblocks.hw.frames import KIND_COMMAND, KIND_EVENT, crc16_ccitt, encode_body
from liveblocks.hw.protocol import COMMANDS, DIAG_DEVICE_FAULT, EVENTS_BY_NAME, command_frame, event_frame

HEARTBEAT_CODE = EVENTS_BY_NAME["HEARTBEAT"].code
FAULT_CODE = EVENTS_BY_NAME["FAULT"].code


def _lines(result) -> list[str]:
    assert result.returncode == 0, f"主机工具退出码 {result.returncode}：{result.stderr}"
    return [line for line in result.stdout.splitlines() if line.strip()]


def _dispatch(tool: HostTool, payload: bytes) -> tuple[list[str], list[str], str, list[str]]:
    """跑一次分发表，返回 ``(日志行, 回帧十六进制, summary 行, 错误行)``。"""
    lines = _lines(tool.run("dispatch", "--hex", payload.hex(" ")))
    logs = [line[len("log: ") :] for line in lines if line.startswith("log: ")]
    replies = [line[len("reply: ") :] for line in lines if line.startswith("reply: ")]
    summary = next((line for line in lines if line.startswith("summary: ")), "")
    errors = [line[len("error: ") :] for line in lines if line.startswith("error: ")]
    assert summary, "主机工具没有打印 summary，输出形状不对"
    return logs, replies, summary, errors


def test_host_sources_exclude_the_idf_only_files() -> None:
    """主机编译清单里不能有 main.c / lb_uart.c（它们只认 ESP-IDF），也不能漏掉可移植源码。"""
    assert "main/main.c" not in HOST_SOURCES
    assert "main/lb_uart.c" not in HOST_SOURCES
    for required in ("main/lb_frame.c", "main/lb_json.c", "main/lb_heartbeat.c", "main/lb_dispatch.c"):
        assert required in HOST_SOURCES


def test_crc_selfcheck_matches_the_python_implementation(firmware_host_tool: HostTool) -> None:
    expected = crc16_ccitt(b"123456789")
    assert expected == 0x29B1, "Python 侧实现变了？这条是 LB-033 的标准校验值"
    lines = _lines(firmware_host_tool.run("crc-selfcheck"))
    assert f"0x{expected:04X}" in lines[0]
    assert "一致" in lines[1]


def test_heartbeat_body_uses_the_same_key_order_as_python(firmware_host_tool: HostTool) -> None:
    lines = _lines(firmware_host_tool.run("heartbeat", "--seq", "1", "--uptime-ms", "12.5", "--steps", "3"))
    assert lines[0] == "body=" + encode_body({"steps": 3, "uptime_ms": 12.5}).decode("utf-8")


@pytest.mark.parametrize(
    ("seq", "uptime_ms", "steps"),
    [
        (1, 12.5, 3),
        (1, 0.0, 0),
        (7, 1234.567, 42),
        (65535, 60000.0, 4294967295),
        (3, 0.001, 1),
    ],
)
def test_heartbeat_frame_matches_the_reference_encoder(
    firmware_host_tool: HostTool, seq: int, uptime_ms: float, steps: int
) -> None:
    """固件编码器与 Python 参考编码器的字节必须完全一样。"""
    mine = firmware_host_tool.heartbeat_bytes(seq, format_uptime_ms(uptime_ms), steps)
    reference = event_frame("HEARTBEAT", seq, {"uptime_ms": uptime_ms, "steps": steps}).encode()
    assert mine == reference
    assert mine[:2] == b"LB"
    assert mine[3] == KIND_EVENT
    assert mine[4] == HEARTBEAT_CODE


@pytest.mark.parametrize(
    "value",
    [0.0, 0.001, 1.0, 12.5, 999.999, 1234.567, 60000.0, 3600000.0],
)
def test_uptime_ms_formatting_matches_python(firmware_host_tool: HostTool, value: float) -> None:
    """浮点毫秒的文本形状必须和 Python `round(x, 3)` + `repr()` 一致（否则逐字节比对必然失败）。"""
    body = _lines(firmware_host_tool.run("heartbeat", "--seq", "9", "--uptime-ms", repr(value), "--steps", "0"))[0]
    assert body == "body=" + encode_body({"steps": 0, "uptime_ms": value}).decode("utf-8")


@pytest.mark.parametrize("command", COMMANDS, ids=[spec.name for spec in COMMANDS])
def test_dispatch_placeholder_answers_every_command_with_a_fault(firmware_host_tool: HostTool, command) -> None:
    """骨架不做业务，但必须明确回答：认出命令号 + 回 `LB-H006`，而不是静默丢弃。"""
    payload = command_frame(command.name, 1, {key: "x" for key in command.body_keys}).encode()
    logs, replies, summary, errors = _dispatch(firmware_host_tool, payload)
    assert len(logs) == 1
    assert command.name in logs[0]
    assert f"0x{command.code:02X}" in logs[0]
    assert DIAG_DEVICE_FAULT in logs[0]
    assert errors == []
    assert "frames=1" in summary
    assert len(replies) == 1

    reply = bytes.fromhex(replies[0])
    reference = event_frame(
        "FAULT", 1, {"code": DIAG_DEVICE_FAULT, "message": f"命令分发占位：{command.name} 未实现业务"}
    ).encode()
    assert reply == reference, "回帧必须与 Python 参考编码逐字节一致"


def test_unknown_command_type_gets_a_fault(firmware_host_tool: HostTool) -> None:
    from liveblocks.hw.frames import Frame

    unknown = Frame.build(KIND_COMMAND, 0x7F, 1, {"x": 1}).encode()
    logs, replies, summary, _errors = _dispatch(firmware_host_tool, unknown)
    assert "未知命令号" in logs[0]
    assert "0x7F" in logs[0]
    assert len(replies) == 1
    assert "frames=1" in summary


def test_non_command_frame_is_ignored(firmware_host_tool: HostTool) -> None:
    """设备只收命令帧：收到事件帧只记一行日志，不回帧（不会自己跟自己说话）。"""
    payload = event_frame("HEARTBEAT", 1, {"uptime_ms": 1.0, "steps": 0}).encode()
    logs, replies, _summary, errors = _dispatch(firmware_host_tool, payload)
    assert len(logs) == 1
    assert "非命令帧" in logs[0]
    assert replies == []
    assert errors == []


def test_crc_error_drops_only_the_bad_frame(firmware_host_tool: HostTool) -> None:
    """坏帧丢一个，紧跟的好帧照收（LB-033 §2.4 的重同步口径）。"""
    good = command_frame("PING", 1, {"nonce": 7}).encode()
    broken = bytearray(command_frame("HELLO", 1, {"client": "x"}).encode())
    broken[-1] ^= 0xFF  # 打坏 CRC
    logs, replies, summary, errors = _dispatch(firmware_host_tool, bytes(broken) + good)
    assert errors == ["crc"], f"期望先报 crc，实际 {errors}"
    assert len(logs) == 1 and "PING" in logs[0]
    assert len(replies) == 1
    assert "frames=1" in summary
    assert "errors=1" in summary


def test_leading_log_text_is_resynchronised(firmware_host_tool: HostTool) -> None:
    """串口上混进日志文本时靠 MAGIC 重同步（真机上日志与帧共用 UART0，见文档已知限制）。"""
    payload = b"boot ok\r\n" + command_frame("END", 1, {"status": "ok"}).encode()
    logs, replies, summary, _errors = _dispatch(firmware_host_tool, payload)
    assert len(logs) == 1 and "END" in logs[0]
    assert len(replies) == 1
    assert "frames=1" in summary


def test_log_text_containing_the_band_letters_does_not_eat_frames(firmware_host_tool: HostTool) -> None:
    """日志里出现 `LB-H006` 这种字样时，假 MAGIC 不许把后面真正的帧吃掉。

    这是真机上「日志与协议帧共用 UART0」的直接后果，也是本单写测试时真踩到的坑：
    起先解析器对「帧头字段不认识」按 LEN 丢整帧候选，日志文本里的 `LB` 会让它吞掉后一帧。
    现在帧头字段不认识时逐字节重同步（`lb_frame.c`），本条守住这个行为。
    """
    text = "[fw] 命令分发占位：回 FAULT LB-H006\r\n".encode("utf-8")
    payload = text + command_frame("PING", 1, {"nonce": 1}).encode()
    logs, replies, summary, _errors = _dispatch(firmware_host_tool, payload)
    assert len(logs) == 1, f"真帧被日志文本吃掉了：{logs}"
    assert "PING" in logs[0]
    assert len(replies) == 1
    assert "frames=1" in summary


def test_boot_log_lists_the_config_and_every_pin(firmware_host_tool: HostTool) -> None:
    text = firmware_host_tool.run("boot-log").stdout
    assert "序界 LiveBlocks 固件骨架" in text
    # 日志前缀不许掺帧 MAGIC（`[LB]` 里的 "LB" 会被解析器当成假的帧头）
    assert text.startswith("[fw] ")
    assert not any(line.startswith("[LB]") for line in text.splitlines())
    assert "config/board.esp32c3-supermini.json" in text
    assert "待实测" in text
    for role, gpio in (("status_led", "GPIO8"), ("led_strip_data", "GPIO3"), ("user_button", "GPIO9")):
        assert role in text
        assert gpio in text
    for spec in COMMANDS:
        assert spec.name in text
    assert "HEARTBEAT" in text


def test_led_placeholder_claims_no_output(firmware_host_tool: HostTool) -> None:
    text = firmware_host_tool.run("boot-log").stdout
    assert "灯效驱动占位" in text
    assert "placeholder" in text
    assert "未驱动任何灯" in text


def test_pins_command_reports_the_configured_values(firmware_host_tool: HostTool) -> None:
    text = firmware_host_tool.run("pins").stdout
    assert "board=esp32c3-supermini" in text
    assert "serial uart=0 tx=21 rx=20 baud=115200 verified=0" in text
    assert "heartbeat_ms=1000 verified=0" in text
