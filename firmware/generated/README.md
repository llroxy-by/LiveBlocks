生成的头文件落在这里（lb_board_config.h / lb_protocol.h），**不入库**：
引脚与协议常量的唯一来源分别是 config/board.esp32c3-supermini.json 与 src/liveblocks/hw/。
生成命令：python -m liveblocks firmware headers --config config/board.esp32c3-supermini.json
