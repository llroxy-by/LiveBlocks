"""构建脚本与生成头文件：只编译不烧写、生成物不入库、真能从零编出固件。

这一层能给的证据分三档，别混：

1. **脚本口径**（永远能跑）：脚本里没有任何烧写调用，传 `flash` 会拒绝执行；
2. **生成物一致**（永远能跑）：生成的头文件里的常量与 `liveblocks.hw` / 板级配置逐值相等；
3. **真实编译**（有 ESP-IDF 时才跑）：`bash scripts/build_firmware.sh` 从零编出
   `lb_firmware.bin`。没有工具链时**跳过并说明原因**，绝不伪造编译输出。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from liveblocks.firmware import (
    GENERATED_DIR,
    BOARD_CONFIG_PATH,
    HOST_SOURCES,
    find_idf,
    idf_toolchain_installed,
    repo_root,
)
from liveblocks.firmware.headers import (
    BOARD_HEADER_NAME,
    PROTOCOL_HEADER_NAME,
    render_protocol_header,
    write_generated_headers,
)

REPO_ROOT = repo_root()
SCRIPT = REPO_ROOT / "scripts" / "build_firmware.sh"
FIRMWARE = REPO_ROOT / "firmware"


# ---------------------------------------------------------------------------
# 1. 脚本口径：只编译、不烧写
# ---------------------------------------------------------------------------


def test_build_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file(), "缺 scripts/build_firmware.sh"
    assert os.access(SCRIPT, os.X_OK), "构建脚本应当可执行"


def test_build_script_only_ever_calls_build_and_set_target() -> None:
    """脚本里每一行 `idf.py` 调用只能是 set-target 或 build——出现 flash / monitor 就该挂。"""
    calls = [
        line.strip()
        for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if "idf.py" in line and not line.strip().startswith("#")
    ]
    assert calls, "脚本里应该有 idf.py 调用"
    for call in calls:
        assert "set-target" in call or re.search(r"\bbuild\b", call), f"意外的 idf.py 调用：{call}"
        for forbidden in ("flash", "monitor", "erase", "write_flash", "esptool"):
            assert forbidden not in call, f"构建脚本里出现了烧写相关调用：{call}"


def test_build_script_mentions_it_does_not_flash() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "只编译" in text and "不烧写" in text
    assert "拒绝：本脚本只编译、不烧写" in text


def test_build_script_refuses_flash_subcommand() -> None:
    """真跑一次拒绝路径：传 flash 必须退出码 2，且不碰 ESP-IDF。"""
    result = subprocess.run(
        ["bash", str(SCRIPT), "flash"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 2, f"退出码应为 2，实际 {result.returncode}"
    assert "只编译" in result.stderr and "不烧写" in result.stderr


def test_build_script_refuses_an_unsafe_build_dir() -> None:
    result = subprocess.run(
        ["bash", str(SCRIPT), "--clean", "--build-dir", "/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert "不安全" in result.stderr


# ---------------------------------------------------------------------------
# 2. 生成物：与两个唯一来源逐值一致，且不入库
# ---------------------------------------------------------------------------


def test_protocol_header_matches_the_hw_layer(tmp_path: Path) -> None:
    from liveblocks.hw import frames as hw_frames
    from liveblocks.hw import protocol as hw_protocol

    write_generated_headers(tmp_path)
    text = (tmp_path / PROTOCOL_HEADER_NAME).read_text(encoding="utf-8")

    assert f"#define LB_MAGIC_0 0x{bytes(hw_frames.MAGIC)[0]:02X}" in text
    assert f"#define LB_WIRE_VERSION {hw_frames.WIRE_VERSION}" in text
    assert f"#define LB_MAX_PAYLOAD {hw_frames.MAX_PAYLOAD}" in text
    assert f"#define LB_MAX_SEQ {hw_frames.MAX_SEQ}" in text
    assert (
        f"#define LB_EVT_HEARTBEAT 0x{hw_protocol.EVENTS_BY_NAME['HEARTBEAT'].code:02X}" in text
    )
    for spec in hw_protocol.COMMANDS:
        assert f"#define LB_CMD_{spec.name} 0x{spec.code:02X}" in text
        assert f'{{0x{spec.code:02X}, "{spec.name}"}}' in text
    for spec in hw_protocol.EVENTS:
        assert f"#define LB_EVT_{spec.name} 0x{spec.code:02X}" in text


def test_board_header_matches_the_config(tmp_path: Path) -> None:
    from liveblocks.firmware import BoardConfig

    config = BoardConfig.load(REPO_ROOT / BOARD_CONFIG_PATH)
    write_generated_headers(tmp_path, config_path=REPO_ROOT / BOARD_CONFIG_PATH)
    text = (tmp_path / BOARD_HEADER_NAME).read_text(encoding="utf-8")
    for entry in config.pins:
        assert f"#define LB_PIN_ROLE_{entry.role.upper()} \"{entry.role}\"" in text
        assert f", {entry.gpio}, " in text
    assert f"#define LB_BOARD_SERIAL_BAUD {config.serial.baud}" in text
    assert f"#define LB_BOARD_HEARTBEAT_MS {config.firmware.heartbeat_ms}" in text


def test_generated_headers_are_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    write_generated_headers(first)
    write_generated_headers(second)
    for name in (PROTOCOL_HEADER_NAME, BOARD_HEADER_NAME):
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert render_protocol_header() == render_protocol_header()


def test_generated_headers_are_not_committed() -> None:
    """生成的头文件必须被 git 忽略：引脚数字的唯一来源只能是那份配置 JSON。"""
    if shutil.which("git") is None:
        pytest.skip("没有 git，跳过「生成物不入库」的核对")
    target = GENERATED_DIR / PROTOCOL_HEADER_NAME
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(target)], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, f"{target} 没有被 .gitignore 忽略"


def test_component_lists_every_source_we_compile_on_the_host() -> None:
    """ESP-IDF 组件必须编译同一批可移植源码（主机侧编的与板子上编的不能是两套代码）。"""
    text = (FIRMWARE / "main" / "CMakeLists.txt").read_text(encoding="utf-8")
    for relative in HOST_SOURCES:
        source = Path(relative).name
        if source == "lb_host_tool.c":
            continue
        assert f'"{source}"' in text, f"{source} 没进 ESP-IDF 组件"
    for idf_only in ("main.c", "lb_uart.c"):
        assert f'"{idf_only}"' in text
    assert '"../generated"' in text
    assert "FATAL_ERROR" in text, "缺生成头文件时应当直接报错，而不是编出一个没配置的固件"


def test_firmware_sources_do_not_touch_ota_or_partitions() -> None:
    """骨架不许碰 OTA / 分区 / NVS 擦写（本单明确不做）。"""
    forbidden = ("esp_ota_", "esp_partition_", "nvs_flash_", "esp_https_ota")
    for path in sorted((FIRMWARE / "main").glob("*")):
        if path.suffix not in {".c", ".h"}:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} 里出现了 {token}（超出本单范围）"


# ---------------------------------------------------------------------------
# 3. 真实编译（有工具链才跑；没有就跳过并说明原因）
# ---------------------------------------------------------------------------


def _idf_readiness() -> tuple[bool, str]:
    if os.environ.get("LB_SKIP_IDF_BUILD"):
        return False, "LB_SKIP_IDF_BUILD 已设置，跳过真实编译（本地迭代时用）"
    idf = find_idf()
    if idf is None:
        return False, "未安装 ESP-IDF（IDF_PATH 或 ~/esp/idf 都没有），跳过真实编译"
    if not idf_toolchain_installed(idf):
        return False, "ESP-IDF 工具链未装好（~/.espressif/tools 里没有 riscv32*），跳过真实编译"
    for tool in ("cmake", "ninja"):
        if shutil.which(tool) is None:
            return False, f"PATH 里没有 {tool}（idf.py 依赖），跳过真实编译"
    return True, ""


@pytest.fixture(scope="session")
def idf_build(tmp_path_factory: pytest.TempPathFactory):
    ready, reason = _idf_readiness()
    if not ready:
        pytest.skip(reason)
    out = tmp_path_factory.mktemp("firmware-idf")
    build_dir = out / "build"
    result = subprocess.run(
        ["bash", str(SCRIPT), "--build-dir", str(build_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=2700,
    )
    return result, build_dir


def test_real_idf_build_produces_a_firmware_binary(idf_build) -> None:
    """源码从零编译通过（CI 里没有工具链时这条 skip，证据看 docs/PROGRESS.md）。"""
    result, build_dir = idf_build
    assert result.returncode == 0, f"编译失败：\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
    assert "编译完成（只编译、未烧写）" in result.stdout
    artifact = build_dir / "lb_firmware.bin"
    assert artifact.is_file(), f"没有产物：{artifact}"
    assert artifact.stat().st_size > 100_000
    assert "sha256" in result.stdout
    # 只编译不烧写：输出里不该出现烧写动作
    assert "Writing at 0x" not in result.stdout
    assert "esptool" not in result.stdout.lower() or "只编译" in result.stdout
