#!/usr/bin/env bash
# LB-034 固件骨架的构建脚本：**只编译、绝不烧写**。
#
# 步骤：
#   1. 从 `config/board.esp32c3-supermini.json` 与 `src/liveblocks/hw/` 生成 C 头文件
#      （引脚与协议常量因此只有一个来源，见 docs/FIRMWARE.md §2）；
#   2. 找一份 ESP-IDF（$LB_IDF_PATH → $IDF_PATH → ~/esp/idf）并 source export.sh；
#   3. `idf.py set-target esp32c3`（只在没有 sdkconfig 时做）→ `idf.py build`；
#   4. 打印产物路径、大小与 sha256（`docs/PROGRESS.md` 的「编译产物」就抄这一行）。
#
# 本脚本不含、也不接受任何烧写动作：真烧写由人按 docs/FIRMWARE.md §5 执行。
#
# 用法：
#   bash scripts/build_firmware.sh                    # 默认配置、默认 build 目录
#   bash scripts/build_firmware.sh --clean            # 先清 build 目录（从零编译）
#   bash scripts/build_firmware.sh --build-dir /tmp/x # 换构建目录
#   PYTHON=.venv/bin/python bash scripts/build_firmware.sh
#
# 退出码：0 = 编译成功；2 = 有人想让它烧写（拒绝执行）；3 = 找不到 ESP-IDF；其余为构建失败。
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
CONFIG="config/board.esp32c3-supermini.json"
GENERATED_DIR="firmware/generated"
BUILD_DIR="firmware/build"
CLEAN=0

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    flash|monitor|erase-flash|upload|write-flash)
      echo "拒绝：本脚本只编译、不烧写。烧写步骤见 docs/FIRMWARE.md §5。" >&2
      exit 2
      ;;
    --config)
      CONFIG="${2:?--config 需要一个路径}"
      shift 2
      ;;
    --build-dir)
      BUILD_DIR="${2:?--build-dir 需要一个路径}"
      shift 2
      ;;
    --clean)
      CLEAN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数：$1（见 --help）" >&2
      exit 2
      ;;
  esac
done

# 构建目录统一成绝对路径（脚本中途会 cd 到 firmware/）
case "$BUILD_DIR" in
  /*) ;;
  *) BUILD_DIR="$PWD/$BUILD_DIR" ;;
esac
# `--clean` 会 rm -rf 这个目录：绝对路径 + 长度校验，避免手滑删掉别的目录
if [ "${#BUILD_DIR}" -lt 6 ] || [ "$BUILD_DIR" = "/" ]; then
  echo "构建目录不安全（$BUILD_DIR），拒绝清理" >&2
  exit 2
fi

echo "==> 1/3 生成 C 头文件（唯一来源：$CONFIG + src/liveblocks/hw/）"
PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m liveblocks firmware \
  headers --config "$CONFIG" --out "$GENERATED_DIR"

IDF_DIR="${LB_IDF_PATH:-${IDF_PATH:-$HOME/esp/idf}}"
if [ ! -f "$IDF_DIR/export.sh" ]; then
  echo "找不到 ESP-IDF：$IDF_DIR（用 LB_IDF_PATH 指定，或先装好 ESP-IDF v5.x）" >&2
  exit 3
fi
echo "==> 2/3 使用 ESP-IDF：$IDF_DIR"
# shellcheck disable=SC1090,SC1091
. "$IDF_DIR/export.sh" >/dev/null

for tool in cmake ninja; do
  if ! command -v "$tool" >/dev/null; then
    echo "找不到 $tool：idf.py 依赖它（Ubuntu: apt install cmake ninja-build；无 root 时可用 python -m venv 里 pip install cmake ninja）" >&2
    exit 3
  fi
done

if [ "$CLEAN" = "1" ]; then
  echo "==> 清理构建目录：$BUILD_DIR"
  rm -rf "$BUILD_DIR"
fi

cd firmware

if [ ! -f sdkconfig ]; then
  echo "==> 设置目标芯片：esp32c3（只写 sdkconfig，不动设备）"
  idf.py -B "$BUILD_DIR" set-target esp32c3
fi

echo "==> 3/3 编译（不烧写）"
idf.py -B "$BUILD_DIR" build

APP_BIN="$BUILD_DIR/lb_firmware.bin"
if [ ! -f "$APP_BIN" ]; then
  echo "编译结束但找不到产物 $APP_BIN" >&2
  exit 1
fi

echo
echo "编译完成（只编译、未烧写）"
echo "命令：bash scripts/build_firmware.sh"
echo "产物：$(cd "$(dirname "$APP_BIN")" && pwd)/lb_firmware.bin（$(wc -c <"$APP_BIN") 字节，sha256 $(sha256sum "$APP_BIN" | cut -d' ' -f1)）"
echo "烧写属人工步骤，见 docs/FIRMWARE.md §5（本脚本不会替你烧）"
