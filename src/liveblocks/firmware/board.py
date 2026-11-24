"""LB-034 固件骨架的 Python 侧支持：板级配置、头文件生成与「裸 GPIO 数字」扫描。

**引脚只有一个来源**：`config/board.esp32c3-supermini.json`。固件源码里不许出现 GPIO 数字，
它们只经由生成的头文件 `lb_board_config.h` 进入 C 代码（`render_board_header`）。
同理，串口帧的线格式常量与命令 / 事件号只有 `src/liveblocks/hw/` 一个来源
（`render_protocol_header`），C 侧不再抄一份。

零运行时依赖：校验器是自己写的（与 `config/board.schema.json` 同口径），
装得上官方 ``jsonschema`` 时 `tests/test_firmware_board.py` 会拿它当**独立实现**交叉核对。

证据口径：本模块的一切结论都是**离线可复现**的静态证据；真板子上的引脚是不是这样、
烧写后串口有没有心跳，属**待人工验证**（`docs/FIRMWARE.md` §7）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

#: 板级配置（唯一引脚来源；路径与 `docs/ROADMAP.md` LB-034 条目一致）。
BOARD_CONFIG_PATH = Path("config/board.esp32c3-supermini.json")

#: 板级配置的 schema（人读 + 可选 jsonschema 交叉核对用）。
BOARD_SCHEMA_PATH = Path("config/board.schema.json")

#: 板级配置格式版本。
BOARD_SCHEMA_VERSION = "0.0.1"

#: 本单只支持这一块板子（与 `docs/HW.md` 里设备自报的 ``board`` 一致）。
SUPPORTED_BOARD_ID = "esp32c3-supermini"
SUPPORTED_MCU = "esp32c3"

#: 板级配置的「待实测」标记：没核对的默认值必须带这个词。
PENDING_MARK = "待实测"

#: 校验与扫描用到的错误码（文档里的码表与这里必须一致，`tests/test_firmware_docs.py` 核对）。
ERROR_CODES: dict[str, str] = {
    "LB-F001": "板级配置缺字段",
    "LB-F002": "板级配置字段取值不合法",
    "LB-F003": "默认值未标「待实测」，或声称已验证却没有核对记录",
    "LB-F004": "引脚冲突或越界",
    "LB-F005": "固件源码里出现裸 GPIO 数字",
}

#: 一个引脚条目必须有的键。
PIN_KEYS: tuple[str, ...] = ("label", "gpio", "active_low", "verified", "note")

#: 串口条目必须有的键。
SERIAL_KEYS: tuple[str, ...] = ("uart", "tx_gpio", "rx_gpio", "baud", "verified", "note")

#: ``firmware`` 段必须有的键。
FIRMWARE_KEYS: tuple[str, ...] = (
    "heartbeat_ms",
    "heartbeat_ms_verified",
    "heartbeat_note",
    "led_count",
    "led_driver",
)

#: 顶层必须有的键。
TOP_KEYS: tuple[str, ...] = (
    "schema_version",
    "board_id",
    "board_name",
    "mcu",
    "config_status",
    "pins",
    "serial",
    "firmware",
)

#: GPIO 合法范围（ESP32-C3 的可用编号上限；超出的值一定是抄错了）。
GPIO_MIN = 0
GPIO_MAX = 48

#: 只有一种灯效驱动被允许：占位（真实驱动不在 LB-034 范围）。
ALLOWED_LED_DRIVERS: tuple[str, ...] = ("placeholder",)


def repo_root() -> Path:
    """仓库根目录（`src/liveblocks/firmware/board.py` 往上三层）。"""
    return Path(__file__).resolve().parents[3]


class BoardConfigError(Exception):
    """板级配置不合法。``problems`` 是 :func:`validation_diagnostics` 的原始输出。"""

    def __init__(self, message: str, problems: Iterable[dict[str, str]] = ()) -> None:
        super().__init__(message)
        self.problems: tuple[dict[str, str], ...] = tuple(problems)


@dataclass(frozen=True)
class PinSpec:
    """一个命名引脚。"""

    role: str
    label: str
    gpio: int
    active_low: bool
    verified: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "label": self.label,
            "gpio": self.gpio,
            "active_low": self.active_low,
            "verified": self.verified,
            "note": self.note,
        }


@dataclass(frozen=True)
class SerialSpec:
    """串口参数。"""

    uart: int
    tx_gpio: int
    rx_gpio: int
    baud: int
    verified: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "uart": self.uart,
            "tx_gpio": self.tx_gpio,
            "rx_gpio": self.rx_gpio,
            "baud": self.baud,
            "verified": self.verified,
            "note": self.note,
        }


@dataclass(frozen=True)
class FirmwareSpec:
    """固件行为参数（心跳周期、灯效占位）。"""

    heartbeat_ms: int
    heartbeat_ms_verified: bool
    heartbeat_note: str
    led_count: int
    led_driver: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "heartbeat_ms": self.heartbeat_ms,
            "heartbeat_ms_verified": self.heartbeat_ms_verified,
            "heartbeat_note": self.heartbeat_note,
            "led_count": self.led_count,
            "led_driver": self.led_driver,
        }


@dataclass(frozen=True)
class BoardConfig:
    """一块板子的完整配置（只读）。"""

    schema_version: str
    board_id: str
    board_name: str
    mcu: str
    config_status: str
    pins: tuple[PinSpec, ...]
    serial: SerialSpec
    firmware: FirmwareSpec
    source_path: Path
    sha256: str

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def from_dict(
        cls,
        document: dict[str, Any],
        *,
        source_path: Path = BOARD_CONFIG_PATH,
        sha256: str = "",
    ) -> "BoardConfig":
        problems = validation_diagnostics(document)
        if problems:
            text = "；".join(f"{item['code']} {item['message']}" for item in problems)
            raise BoardConfigError(f"板级配置不合法：{text}", problems)
        pins = tuple(
            PinSpec(
                role=role,
                label=str(entry["label"]),
                gpio=int(entry["gpio"]),
                active_low=bool(entry["active_low"]),
                verified=bool(entry["verified"]),
                note=str(entry["note"]),
            )
            for role, entry in sorted(document["pins"].items())
        )
        serial_entry = document["serial"]
        firmware_entry = document["firmware"]
        return cls(
            schema_version=str(document["schema_version"]),
            board_id=str(document["board_id"]),
            board_name=str(document["board_name"]),
            mcu=str(document["mcu"]),
            config_status=str(document["config_status"]),
            pins=pins,
            serial=SerialSpec(
                uart=int(serial_entry["uart"]),
                tx_gpio=int(serial_entry["tx_gpio"]),
                rx_gpio=int(serial_entry["rx_gpio"]),
                baud=int(serial_entry["baud"]),
                verified=bool(serial_entry["verified"]),
                note=str(serial_entry["note"]),
            ),
            firmware=FirmwareSpec(
                heartbeat_ms=int(firmware_entry["heartbeat_ms"]),
                heartbeat_ms_verified=bool(firmware_entry["heartbeat_ms_verified"]),
                heartbeat_note=str(firmware_entry["heartbeat_note"]),
                led_count=int(firmware_entry["led_count"]),
                led_driver=str(firmware_entry["led_driver"]),
            ),
            source_path=Path(source_path),
            sha256=sha256,
        )

    @classmethod
    def load(cls, path: str | Path = BOARD_CONFIG_PATH) -> "BoardConfig":
        """读并校验一份板级配置；不合法抛 :class:`BoardConfigError`。"""
        target = Path(path)
        data = target.read_bytes()
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BoardConfigError(f"读不了板级配置 {target}：{error}") from error
        if not isinstance(document, dict):
            raise BoardConfigError(f"板级配置 {target} 的顶层必须是 JSON 对象")
        import hashlib

        return cls.from_dict(
            document, source_path=target, sha256=hashlib.sha256(data).hexdigest()
        )

    # -- 查询 -------------------------------------------------------------
    def pin(self, role: str) -> PinSpec:
        for entry in self.pins:
            if entry.role == role:
                return entry
        raise KeyError(f"板级配置里没有角色 {role!r}（有：{[p.role for p in self.pins]}）")

    def roles(self) -> tuple[str, ...]:
        return tuple(entry.role for entry in self.pins)

    @property
    def pending_items(self) -> tuple[str, ...]:
        """仍是「待实测」的项（人读了就知道还差哪些核对）。"""
        pending: list[str] = []
        for entry in self.pins:
            if not entry.verified:
                pending.append(f"pins.{entry.role}.gpio")
        if not self.serial.verified:
            pending.append("serial.tx_gpio/rx_gpio")
        if not self.firmware.heartbeat_ms_verified:
            pending.append("firmware.heartbeat_ms")
        return tuple(pending)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "board_id": self.board_id,
            "board_name": self.board_name,
            "mcu": self.mcu,
            "config_status": self.config_status,
            "pins": {entry.role: entry.to_dict() for entry in self.pins},
            "serial": self.serial.to_dict(),
            "firmware": self.firmware.to_dict(),
        }

    def describe(self) -> str:
        pins = "、".join(
            f"{entry.role}=GPIO{entry.gpio}" + ("" if entry.verified else f"（{PENDING_MARK}）")
            for entry in self.pins
        )
        return (
            f"{self.board_name}（{self.board_id} / {self.mcu}）：{pins}；"
            f"串口 UART{self.serial.uart} tx=GPIO{self.serial.tx_gpio} "
            f"rx=GPIO{self.serial.rx_gpio} @{self.serial.baud}；"
            f"心跳 {self.firmware.heartbeat_ms} ms；待实测项 {len(self.pending_items)} 个"
        )

    # -- 生成 -------------------------------------------------------------
    def render_board_header(self) -> str:
        """渲染 `lb_board_config.h`（引脚从配置来，代码里因此没有裸 GPIO 数字）。"""
        lines: list[str] = [
            "/* 自动生成，**不要手改**。",
            " *",
            f" * 来源：{self.source_path.as_posix()}（sha256 {self.sha256[:16]}…）",
            " * 生成命令：python -m liveblocks firmware headers "
            f"--config {self.source_path.as_posix()}",
            f" * 格式版本：{self.schema_version}；标注「{PENDING_MARK}」的项 = 无实物核对的默认值。",
            " */",
            "#ifndef LB_BOARD_CONFIG_H",
            "#define LB_BOARD_CONFIG_H",
            "",
            f"#define LB_BOARD_ID {_c_string(self.board_id)}",
            f"#define LB_BOARD_NAME {_c_string(self.board_name)}",
            f"#define LB_BOARD_MCU {_c_string(self.mcu)}",
            f"#define LB_BOARD_CONFIG_PATH {_c_string(self.source_path.as_posix())}",
            f"#define LB_BOARD_CONFIG_SHA256 {_c_string(self.sha256)}",
            f"#define LB_BOARD_CONFIG_STATUS {_c_string(self.config_status)}",
            "",
            f"#define LB_BOARD_SERIAL_UART {self.serial.uart}",
            f"#define LB_BOARD_SERIAL_TX_GPIO {self.serial.tx_gpio}",
            f"#define LB_BOARD_SERIAL_RX_GPIO {self.serial.rx_gpio}",
            f"#define LB_BOARD_SERIAL_BAUD {self.serial.baud}",
            f"#define LB_BOARD_SERIAL_VERIFIED {_c_bool(self.serial.verified)}",
            f"#define LB_BOARD_SERIAL_NOTE {_c_string(self.serial.note)}",
            "",
            f"#define LB_BOARD_HEARTBEAT_MS {self.firmware.heartbeat_ms}",
            f"#define LB_BOARD_HEARTBEAT_MS_VERIFIED {_c_bool(self.firmware.heartbeat_ms_verified)}",
            f"#define LB_BOARD_HEARTBEAT_NOTE {_c_string(self.firmware.heartbeat_note)}",
            f"#define LB_BOARD_LED_COUNT {self.firmware.led_count}",
            f"#define LB_BOARD_LED_DRIVER {_c_string(self.firmware.led_driver)}",
            "",
        ]
        for entry in self.pins:
            prefix = "LB_PIN_ROLE_" + entry.role.upper()
            lines.append(f"#define {prefix} {_c_string(entry.role)}")
        lines.append("")
        lines.append(f"#define LB_BOARD_PIN_COUNT {len(self.pins)}")
        lines.append("")
        lines.append("#define LB_BOARD_PINS_INIT \\")
        last = len(self.pins) - 1
        for index, entry in enumerate(self.pins):
            continuation = "" if index == last else " \\"
            item = (
                "{"
                f"LB_PIN_ROLE_{entry.role.upper()}"
                f", {_c_string(entry.label)}"
                f", {entry.gpio}"
                f", {_c_bool(entry.active_low)}"
                f", {_c_bool(entry.verified)}"
                f", {_c_string(entry.note)}"
                "}"
            )
            lines.append(f"    {item}{',' if index != last else ''}{continuation}")
        lines.append("")
        lines.append("#endif /* LB_BOARD_CONFIG_H */")
        return "\n".join(lines) + "\n"


def _c_string(text: str) -> str:
    """C 字符串字面量（转义反斜杠、引号、换行；非 ASCII 原样保留，编译器按 UTF-8 收）。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
    return f'"{escaped}"'


def _c_bool(value: bool) -> str:
    return "1" if value else "0"


# ---------------------------------------------------------------------------
# 校验（零依赖；与 config/board.schema.json 同口径）
# ---------------------------------------------------------------------------


def _problem(code: str, message: str, path: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def validation_diagnostics(document: Any) -> list[dict[str, str]]:
    """校验板级配置，返回问题清单（空 = 合格）。顺序即发现顺序，便于读。"""
    problems: list[dict[str, str]] = []
    if not isinstance(document, dict):
        return [_problem("LB-F001", f"顶层必须是 JSON 对象，实际 {type(document).__name__}")]

    for key in TOP_KEYS:
        if key not in document:
            problems.append(_problem("LB-F001", f"缺少顶层字段 {key}（必填）", key))
    if problems:
        return problems

    if not isinstance(document["schema_version"], str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+", str(document["schema_version"])
    ):
        problems.append(
            _problem("LB-F002", f"schema_version 必须形如 0.0.1，实际 {document['schema_version']!r}", "schema_version")
        )
    if document["board_id"] != SUPPORTED_BOARD_ID:
        problems.append(
            _problem(
                "LB-F002",
                f"board_id 只支持 {SUPPORTED_BOARD_ID!r}（本单只做 ESP32-C3 SuperMini），实际 {document['board_id']!r}",
                "board_id",
            )
        )
    if document["mcu"] != SUPPORTED_MCU:
        problems.append(
            _problem("LB-F002", f"mcu 只支持 {SUPPORTED_MCU!r}，实际 {document['mcu']!r}", "mcu")
        )
    for key in ("board_name", "config_status"):
        if not isinstance(document[key], str) or not document[key].strip():
            problems.append(_problem("LB-F002", f"{key} 必须是非空字符串", key))

    pins = document["pins"]
    if not isinstance(pins, dict) or not pins:
        problems.append(_problem("LB-F001", "pins 必须是至少含一个角色名的对象", "pins"))
        pins = {}
    seen_gpio: dict[int, str] = {}
    for role, entry in pins.items():
        path = f"pins.{role}"
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(role)):
            problems.append(_problem("LB-F002", f"角色名必须是 snake_case，实际 {role!r}", path))
        if not isinstance(entry, dict):
            problems.append(_problem("LB-F001", f"{path} 必须是对象", path))
            continue
        for key in PIN_KEYS:
            if key not in entry:
                problems.append(_problem("LB-F001", f"{path} 缺少字段 {key}", f"{path}.{key}"))
        if any(key not in entry for key in PIN_KEYS):
            continue
        if not isinstance(entry["label"], str) or not entry["label"].strip():
            problems.append(_problem("LB-F002", f"{path}.label 必须是非空字符串", f"{path}.label"))
        gpio = entry["gpio"]
        if not isinstance(gpio, int) or isinstance(gpio, bool):
            problems.append(_problem("LB-F002", f"{path}.gpio 必须是整数", f"{path}.gpio"))
        elif not GPIO_MIN <= gpio <= GPIO_MAX:
            problems.append(
                _problem("LB-F004", f"{path}.gpio={gpio} 超出 {GPIO_MIN}..{GPIO_MAX}", f"{path}.gpio")
            )
        else:
            if gpio in seen_gpio:
                problems.append(
                    _problem(
                        "LB-F004",
                        f"{path}.gpio={gpio} 与 pins.{seen_gpio[gpio]} 撞了同一个脚",
                        f"{path}.gpio",
                    )
                )
            else:
                seen_gpio[gpio] = str(role)
        if not isinstance(entry["active_low"], bool):
            problems.append(_problem("LB-F002", f"{path}.active_low 必须是布尔", f"{path}.active_low"))
        problems.extend(_verified_diagnostics(entry, path))

    serial = document["serial"]
    if not isinstance(serial, dict):
        problems.append(_problem("LB-F001", "serial 必须是对象", "serial"))
        serial = {}
    for key in SERIAL_KEYS:
        if key not in serial:
            problems.append(_problem("LB-F001", f"serial 缺少字段 {key}", f"serial.{key}"))
    if all(key in serial for key in SERIAL_KEYS):
        if not isinstance(serial["uart"], int) or not 0 <= serial["uart"] <= 2:
            problems.append(_problem("LB-F002", "serial.uart 必须是 0..2 的整数", "serial.uart"))
        for key in ("tx_gpio", "rx_gpio"):
            value = serial[key]
            if not isinstance(value, int) or isinstance(value, bool) or not GPIO_MIN <= value <= GPIO_MAX:
                problems.append(
                    _problem("LB-F004", f"serial.{key}={value!r} 不在 {GPIO_MIN}..{GPIO_MAX}", f"serial.{key}")
                )
        if serial["tx_gpio"] == serial["rx_gpio"]:
            problems.append(_problem("LB-F004", "serial.tx_gpio 与 serial.rx_gpio 不能是同一个脚", "serial"))
        if not isinstance(serial["baud"], int) or not 9600 <= serial["baud"] <= 921600:
            problems.append(_problem("LB-F002", "serial.baud 必须在 9600..921600", "serial.baud"))
        problems.extend(_verified_diagnostics(serial, "serial"))

    firmware = document["firmware"]
    if not isinstance(firmware, dict):
        problems.append(_problem("LB-F001", "firmware 必须是对象", "firmware"))
        firmware = {}
    for key in FIRMWARE_KEYS:
        if key not in firmware:
            problems.append(_problem("LB-F001", f"firmware 缺少字段 {key}", f"firmware.{key}"))
    if all(key in firmware for key in FIRMWARE_KEYS):
        if not isinstance(firmware["heartbeat_ms"], int) or not 100 <= firmware["heartbeat_ms"] <= 60000:
            problems.append(
                _problem("LB-F002", "firmware.heartbeat_ms 必须是 100..60000 的整数", "firmware.heartbeat_ms")
            )
        if not isinstance(firmware["heartbeat_ms_verified"], bool):
            problems.append(
                _problem("LB-F002", "firmware.heartbeat_ms_verified 必须是布尔", "firmware.heartbeat_ms_verified")
            )
        if not isinstance(firmware["heartbeat_note"], str) or not firmware["heartbeat_note"].strip():
            problems.append(
                _problem("LB-F002", "firmware.heartbeat_note 必须是非空字符串", "firmware.heartbeat_note")
            )
        if not isinstance(firmware["led_count"], int) or not 0 <= firmware["led_count"] <= 1024:
            problems.append(
                _problem("LB-F002", "firmware.led_count 必须是 0..1024 的整数", "firmware.led_count")
            )
        if firmware["led_driver"] not in ALLOWED_LED_DRIVERS:
            problems.append(
                _problem(
                    "LB-F002",
                    f"firmware.led_driver 只允许 {ALLOWED_LED_DRIVERS}（LB-034 只做占位驱动），"
                    f"实际 {firmware['led_driver']!r}",
                    "firmware.led_driver",
                )
            )
        if firmware["heartbeat_ms_verified"] is False and PENDING_MARK not in str(firmware["heartbeat_note"]):
            problems.append(
                _problem(
                    "LB-F003",
                    f"firmware.heartbeat_ms 未核对（heartbeat_ms_verified=false），"
                    f"heartbeat_note 必须含「{PENDING_MARK}」",
                    "firmware.heartbeat_note",
                )
            )
        if firmware["heartbeat_ms_verified"] is True and not str(firmware.get("verified_by", "")).strip():
            problems.append(
                _problem(
                    "LB-F003",
                    "heartbeat_ms_verified=true 时必须给 heartbeat_ms_verified_by（核对记录）",
                    "firmware.heartbeat_ms_verified",
                )
            )

    # 还有未核对的项时，顶层 config_status 必须说清楚
    pending = _pending_count(document)
    if pending and PENDING_MARK not in str(document.get("config_status", "")):
        problems.append(
            _problem(
                "LB-F003",
                f"配置里还有 {pending} 项未核对，config_status 必须含「{PENDING_MARK}」",
                "config_status",
            )
        )
    return problems


def _verified_diagnostics(entry: dict[str, Any], path: str) -> list[dict[str, str]]:
    """``verified`` / ``note`` / ``verified_by`` 三者的口径。"""
    problems: list[dict[str, str]] = []
    verified = entry.get("verified")
    if not isinstance(verified, bool):
        problems.append(_problem("LB-F002", f"{path}.verified 必须是布尔", f"{path}.verified"))
        return problems
    note = str(entry.get("note", ""))
    if not note.strip():
        problems.append(_problem("LB-F002", f"{path}.note 必须是非空字符串", f"{path}.note"))
        return problems
    if verified is False and PENDING_MARK not in note:
        problems.append(
            _problem("LB-F003", f"{path} 未核对（verified=false），note 必须标「{PENDING_MARK}」", f"{path}.note")
        )
    if verified is True and not str(entry.get("verified_by", "")).strip():
        problems.append(
            _problem(
                "LB-F003",
                f"{path} 声称已验证（verified=true），必须给 verified_by 记录（Issue / 测量方式 / 日期）",
                f"{path}.verified_by",
            )
        )
    return problems


def _pending_count(document: dict[str, Any]) -> int:
    count = 0
    pins = document.get("pins")
    if isinstance(pins, dict):
        for entry in pins.values():
            if isinstance(entry, dict) and entry.get("verified") is not True:
                count += 1
    serial = document.get("serial")
    if isinstance(serial, dict) and serial.get("verified") is not True:
        count += 1
    firmware = document.get("firmware")
    if isinstance(firmware, dict) and firmware.get("heartbeat_ms_verified") is not True:
        count += 1
    return count


# ---------------------------------------------------------------------------
# 「源码里搜不到裸 GPIO 数字」
# ---------------------------------------------------------------------------

#: 固件源码里一律不许出现的形状（不管哪一行）。
FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bGPIO\d+\b"),
    re.compile(r"\bGPIO_NUM_\d+\b"),
)

#: 判定「这行在说引脚」的关键词。
GPIO_LINE = re.compile(r"gpio", re.IGNORECASE)

#: 数字字面量（十进制 / 十六进制）。贴着标识符或连字符的数字不算：`LB-034`、`UART0`、
#: `%02X` 里的数字不是引脚号，报出来只会让人学会忽略告警。
NUMERIC_LITERAL = re.compile(r"(?<![\w-])(?:0[xX][0-9A-Fa-f]+|\d+)(?![\w-])")

#: 允许出现在「引脚行」上的数字：0 / 1 是电平与布尔，不是引脚号。
ALLOWED_PIN_LINE_NUMBERS = frozenset({"0", "1", "0x0", "0x00", "0X0", "0x1", "0x01", "0X1"})


#: 手写固件源码所在的两个目录（相对 ``firmware/``）：只有这里的东西算「源码」。
SOURCE_DIRS: tuple[str, ...] = ("main", "tools")


def firmware_source_files(root: str | Path | None = None) -> list[Path]:
    """固件源码：``firmware/main`` 与 ``firmware/tools`` 下入库的 ``.c`` / ``.h``。

    生成目录（``firmware/generated``）与 ESP-IDF 的构建目录（``firmware/build``）不算：
    前者本来就是配置的产物，后者里是 ESP-IDF 自己的 ``sdkconfig.h`` 之类。
    """
    if root is not None:
        candidate = Path(root)
        base = candidate / "firmware" if (candidate / "firmware").is_dir() else candidate
    else:
        base = repo_root() / "firmware"
    files: list[Path] = []
    for name in SOURCE_DIRS:
        directory = base / name
        if not directory.is_dir():
            continue
        files.extend(
            path for path in sorted(directory.rglob("*")) if path.is_file() and path.suffix in {".c", ".h"}
        )
    return files


def bare_gpio_violations(root: str | Path | None = None) -> list[dict[str, str]]:
    """扫固件源码里的裸 GPIO 数字，返回违规清单（空 = 干净）。

    两条规则：

    1. 任何一行都不许出现 ``GPIO<数字>`` / ``GPIO_NUM_<数字>``；
    2. 说引脚的行（含 ``gpio`` 字样）里不许出现除 0 / 1 之外的数字字面量——引脚号只能从
       生成的头文件里来，写在代码里就是硬编码。
    """
    base = Path(root) if root is not None else repo_root()
    violations: list[dict[str, str]] = []
    for path in firmware_source_files(base):
        relative = path.relative_to(base).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern in FORBIDDEN_PATTERNS:
                for match in pattern.finditer(line):
                    violations.append(
                        _problem(
                            "LB-F005",
                            f"出现了硬编码引脚 `{match.group(0)}`：引脚只能来自 "
                            f"config/board.esp32c3-supermini.json（生成的头文件）",
                            f"{relative}:{number}",
                        )
                    )
            if not GPIO_LINE.search(line):
                continue
            for match in NUMERIC_LITERAL.finditer(line):
                token = match.group(0)
                if token in ALLOWED_PIN_LINE_NUMBERS:
                    continue
                violations.append(
                    _problem(
                        "LB-F005",
                        f"引脚相关的一行里出现了数字字面量 `{token}`：引脚号只能来自生成的头文件",
                        f"{relative}:{number}",
                    )
                )
    return violations


def iter_config_pins(document: dict[str, Any]) -> Iterator[tuple[str, int]]:
    """``(角色名, gpio)`` 的平坦迭代（报告与测试用）。"""
    for role, entry in sorted(document.get("pins", {}).items()):
        if isinstance(entry, dict) and isinstance(entry.get("gpio"), int):
            yield role, int(entry["gpio"])


__all__ = [
    "ALLOWED_LED_DRIVERS",
    "BOARD_CONFIG_PATH",
    "BOARD_SCHEMA_PATH",
    "BOARD_SCHEMA_VERSION",
    "BoardConfig",
    "BoardConfigError",
    "ERROR_CODES",
    "FIRMWARE_KEYS",
    "FirmwareSpec",
    "PENDING_MARK",
    "PIN_KEYS",
    "PinSpec",
    "SERIAL_KEYS",
    "SOURCE_DIRS",
    "SUPPORTED_BOARD_ID",
    "SUPPORTED_MCU",
    "SerialSpec",
    "TOP_KEYS",
    "bare_gpio_violations",
    "firmware_source_files",
    "iter_config_pins",
    "repo_root",
    "validation_diagnostics",
]
