"""LB-034 板级配置：schema 校验、待实测标注、生成头文件、零裸 GPIO 数字。

这里的每一条都是**离线可复现的静态证据**：配置本身合不合格、生成的头文件里到底写了什么、
固件源码里搜不搜得到 GPIO 数字。至于「板子上真的是这些脚吗」属**待人工验证**
（`docs/FIRMWARE.md` §7），本文件不做任何此类断言。

反向验证是重点：`test_broken_configs_are_rejected` 每条都改一处配置，断言校验器必须报出
对应的 `LB-F0xx`——否则「校验通过」只是因为它什么都没查。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from liveblocks.firmware import (
    BOARD_CONFIG_PATH,
    BOARD_SCHEMA_PATH,
    BoardConfig,
    BoardConfigError,
    PENDING_MARK,
    bare_gpio_violations,
    firmware_source_files,
    repo_root,
    validation_diagnostics,
)
from liveblocks.firmware.board import BOARD_SCHEMA_VERSION, SUPPORTED_BOARD_ID

REPO_ROOT = repo_root()
CONFIG_PATH = REPO_ROOT / BOARD_CONFIG_PATH
SCHEMA_PATH = REPO_ROOT / BOARD_SCHEMA_PATH


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config() -> BoardConfig:
    return BoardConfig.load(CONFIG_PATH)


# ---------------------------------------------------------------------------
# 正例：配置合格、schema 一致、默认值都标了「待实测」
# ---------------------------------------------------------------------------


def test_config_file_and_schema_exist() -> None:
    assert CONFIG_PATH.is_file(), "缺 config/board.esp32c3-supermini.json（引脚的唯一来源）"
    assert SCHEMA_PATH.is_file(), "缺 config/board.schema.json"


def test_config_passes_the_builtin_validator(document: dict) -> None:
    assert validation_diagnostics(document) == []


def test_config_is_the_only_supported_board(document: dict) -> None:
    assert document["board_id"] == SUPPORTED_BOARD_ID
    assert document["mcu"] == "esp32c3"
    assert document["schema_version"] == BOARD_SCHEMA_VERSION


def test_schema_file_agrees_with_the_config(document: dict) -> None:
    """schema 文件里声明的必填字段与校验器口径一致（两边都得改，不能只改一边）。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["type"] == "object"
    for key in schema["required"]:
        assert key in document, f"schema 要求 {key}，但入库配置里没有"
    pin_schema = schema["properties"]["pins"]["additionalProperties"]
    for key in pin_schema["required"]:
        for role, entry in document["pins"].items():
            assert key in entry, f"schema 要求 pins.{role}.{key}"


def test_optional_jsonschema_cross_check(document: dict) -> None:
    """装得上官方 jsonschema 时，用**独立实现**校验同一份配置（口径必须一致）。"""
    jsonschema = pytest.importorskip("jsonschema", reason="可选交叉核对：需要 jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(document, schema)


def test_every_unverified_default_is_marked_pending(document: dict) -> None:
    """凡是没核对的默认值，都必须带「待实测」——这是 Issue 的硬要求。"""
    assert PENDING_MARK in document["config_status"]
    for role, entry in document["pins"].items():
        if entry["verified"] is False:
            assert PENDING_MARK in entry["note"], f"pins.{role} 没核对却没标「待实测」"
            assert "verified_by" not in entry, f"pins.{role} 一边说没核对，一边给了核对记录"
    assert PENDING_MARK in document["serial"]["note"]
    assert PENDING_MARK in document["firmware"]["heartbeat_note"]


def test_pending_items_are_reported(config: BoardConfig) -> None:
    """待实测项必须被数出来（人一眼看到还差哪些核对，代理不得代填）。"""
    assert config.pending_items == (
        "pins.led_strip_data.gpio",
        "pins.status_led.gpio",
        "pins.user_button.gpio",
        "serial.tx_gpio/rx_gpio",
        "firmware.heartbeat_ms",
    )


def test_describe_marks_pending_items(config: BoardConfig) -> None:
    text = config.describe()
    assert "待实测" in text
    assert "ESP32-C3 SuperMini" in text


# ---------------------------------------------------------------------------
# 反例：改一处就必须报出来（否则校验器是永真式）
# ---------------------------------------------------------------------------


def _mutate(document: dict, mutate) -> dict:
    copy_document = copy.deepcopy(document)
    mutate(copy_document)
    return copy_document


def test_missing_top_level_key_is_reported(document: dict) -> None:
    broken = _mutate(document, lambda doc: doc.pop("pins"))
    codes = [item["code"] for item in validation_diagnostics(broken)]
    assert "LB-F001" in codes


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        # 类型 / 取值不合法
        (lambda doc: doc["pins"]["status_led"].__setitem__("gpio", "8"), "LB-F002"),
        (lambda doc: doc.__setitem__("board_id", "esp32s3-devkit"), "LB-F002"),
        (lambda doc: doc["firmware"].__setitem__("led_driver", "ws2812"), "LB-F002"),
        (lambda doc: doc["serial"].__setitem__("baud", 1234), "LB-F002"),
        (lambda doc: doc["pins"]["status_led"].__setitem__("active_low", "yes"), "LB-F002"),
        # 引脚冲突 / 越界
        (lambda doc: doc["pins"]["user_button"].__setitem__("gpio", 99), "LB-F004"),
        (lambda doc: doc["pins"]["user_button"].__setitem__("gpio", 8), "LB-F004"),
        (lambda doc: doc["serial"].__setitem__("rx_gpio", doc["serial"]["tx_gpio"]), "LB-F004"),
        # 「待实测」的标注口径
        (lambda doc: doc["pins"]["status_led"].__setitem__("note", "板载 LED"), "LB-F003"),
        (lambda doc: doc["firmware"].__setitem__("heartbeat_note", "心跳周期"), "LB-F003"),
        (lambda doc: doc.__setitem__("config_status", "已核对"), "LB-F003"),
        # 声称已验证却没给记录
        (lambda doc: doc["pins"]["status_led"].__setitem__("verified", True), "LB-F003"),
    ],
)
def test_broken_configs_are_rejected(document: dict, mutate, expected: str) -> None:
    broken = _mutate(document, mutate)
    codes = [item["code"] for item in validation_diagnostics(broken)]
    assert expected in codes, f"期望报 {expected}，实际 {codes}"


def test_rejected_config_cannot_be_loaded(tmp_path: Path, document: dict) -> None:
    broken = _mutate(document, lambda doc: doc["pins"]["status_led"].__setitem__("gpio", 999))
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(BoardConfigError):
        BoardConfig.load(target)


def test_verified_pin_with_record_is_accepted(document: dict) -> None:
    """正向：真核对了的项（verified=true + 核对记录）不该被误报。"""
    checked = _mutate(
        document,
        lambda doc: doc["pins"]["status_led"].update(
            {"verified": True, "verified_by": "Issue #34 实拍：GPIO8 低有效，2026-11-24"}
        ),
    )
    codes = [item["code"] for item in validation_diagnostics(checked)]
    assert "LB-F003" not in codes


# ---------------------------------------------------------------------------
# 生成的头文件：引脚从配置来，数字不写进源码
# ---------------------------------------------------------------------------


def test_board_header_carries_every_pin(config: BoardConfig) -> None:
    header = config.render_board_header()
    assert "#ifndef LB_BOARD_CONFIG_H" in header
    for entry in config.pins:
        assert f"#define LB_PIN_ROLE_{entry.role.upper()} \"{entry.role}\"" in header
        assert f", {entry.gpio}, " in header, f"{entry.role} 的 GPIO 没进生成头文件"
    assert f"#define LB_BOARD_SERIAL_TX_GPIO {config.serial.tx_gpio}" in header
    assert f"#define LB_BOARD_SERIAL_RX_GPIO {config.serial.rx_gpio}" in header
    assert f"#define LB_BOARD_HEARTBEAT_MS {config.firmware.heartbeat_ms}" in header
    assert PENDING_MARK in header


def test_board_header_is_deterministic(config: BoardConfig) -> None:
    assert config.render_board_header() == config.render_board_header()


def test_pins_are_sorted_by_role(config: BoardConfig) -> None:
    roles = [entry.role for entry in config.pins]
    assert roles == sorted(roles)


def test_generated_headers_stay_out_of_git(tmp_path: Path) -> None:
    """生成的头文件不入库：引脚数字的唯一来源必须是那份 JSON。"""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "firmware/generated/" in gitignore


# ---------------------------------------------------------------------------
# 「源码里搜不到裸 GPIO 数字」
# ---------------------------------------------------------------------------


def test_no_bare_gpio_numbers_in_firmware_sources() -> None:
    files = firmware_source_files()
    assert files, "没找到固件源码，扫描就成了假通过"
    assert bare_gpio_violations() == []


def test_scanner_covers_every_firmware_source() -> None:
    """扫描范围必须覆盖入库的每个 .c/.h（生成目录除外），不然「搜不到」可能只是没搜。"""
    files = firmware_source_files()
    suffixes = {path.suffix for path in files}
    assert suffixes == {".c", ".h"}
    assert any("main.c" == path.name for path in files)
    assert any(path.name == "lb_uart.c" for path in files)
    assert not any("generated" in path.parts for path in files)


@pytest.mark.parametrize(
    ("source", "expected_token"),
    [
        ("static const int pin = GPIO8;\n", "GPIO8"),
        ("void f(void) { gpio_set_level(8, 1); }\n", "8"),
        ("const int p = GPIO_NUM_9;\n", "GPIO_NUM_9"),
        ("cfg.gpio = 21;\n", "21"),
        ("const int x = (gpio_num_t)3;\n", "3"),
    ],
)
def test_scanner_catches_injected_pins(tmp_path: Path, source: str, expected_token: str) -> None:
    """反向验证：塞一行硬编码引脚进去，必须被抓出来。"""
    target = tmp_path / "firmware" / "main"
    target.mkdir(parents=True)
    (target / "leak.c").write_text(source, encoding="utf-8")
    violations = bare_gpio_violations(tmp_path)
    assert violations, f"没抓出 {expected_token}"
    assert all(item["code"] == "LB-F005" for item in violations)
    assert any(expected_token in item["message"] for item in violations)


@pytest.mark.parametrize(
    "source",
    [
        'boot_printf(&writer, "tx=GPIO%d rx=GPIO%d\\n", serial.tx_gpio, serial.rx_gpio);\n',
        "// LB-034 只做占位，不调用任何 GPIO 接口\n",
        "const int uart = UART_NUM_0; // 串口 0，不是引脚\n",
        "snprintf(buf, sizeof buf, \"0x%02X\", code);\n",
        "const int level = 0;\n",
    ],
)
def test_scanner_does_not_flag_harmless_lines(tmp_path: Path, source: str) -> None:
    """误报会让人学会忽略告警：这些行里没有引脚数字，不许报。"""
    target = tmp_path / "firmware" / "main"
    target.mkdir(parents=True)
    (target / "ok.c").write_text(source, encoding="utf-8")
    assert bare_gpio_violations(tmp_path) == []
