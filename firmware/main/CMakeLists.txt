# 固件主组件：可移植的协议/骨架代码 + 仅 ESP-IDF 的胶水（main.c / lb_uart.c）。
#
# 生成的头文件由 `bash scripts/build_firmware.sh` 调用
# `python -m liveblocks firmware headers` 产出到 firmware/generated/：
#   - lb_protocol.h      线格式常量与命令/事件号（源：src/liveblocks/hw/{frames,protocol}.py）
#   - lb_board_config.h  引脚与串口参数（源：config/board.esp32c3-supermini.json）
# 这两个文件**不入库**：引脚与协议常量的唯一来源是上面那两个文件。
if(NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/../generated/lb_board_config.h"
   OR NOT EXISTS "${CMAKE_CURRENT_LIST_DIR}/../generated/lb_protocol.h")
    message(FATAL_ERROR
        "缺少生成的头文件（firmware/generated/）。请用 `bash scripts/build_firmware.sh` 构建，"
        "它会先从 config/board.esp32c3-supermini.json 与 src/liveblocks/hw/ 生成头文件。")
endif()

idf_component_register(
    SRCS
        "main.c"
        "lb_board.c"
        "lb_boot.c"
        "lb_dispatch.c"
        "lb_frame.c"
        "lb_heartbeat.c"
        "lb_json.c"
        "lb_led.c"
        "lb_uart.c"
    INCLUDE_DIRS
        "."
        "../generated"
)
