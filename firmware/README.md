# firmware/ — LB-034 ESP32-C3 SuperMini 固件骨架

**这是骨架，不是能跑业务的固件**：它上电打启动日志、周期性发串口心跳、认命令号并回
`LB-H006`（业务未实现）、把灯效请求记进日志。引脚与协议常量都不在这里写死。

人读的完整说明在 [`docs/FIRMWARE.md`](../docs/FIRMWARE.md)（构建、烧写、待人工验证清单）。

```bash
# 只编译、不烧写（会先生成 lb_board_config.h / lb_protocol.h 到 firmware/generated/）
bash scripts/build_firmware.sh          # macOS/Linux 上用 PYTHON=... 指定解释器

# 烧写由人做（代理不烧）
cd firmware && idf.py -p /dev/ttyACM0 -b 460800 flash monitor
```

| 路径 | 作用 |
| --- | --- |
| `CMakeLists.txt` / `sdkconfig.defaults` | ESP-IDF 工程定义（目标 `esp32c3`，控制台 UART0 / 115200） |
| `main/main.c` | `app_main`：启动日志 → 灯效占位 → UART → 协议任务 + 心跳任务 |
| `main/lb_frame.c` | 帧编解码 / CRC-16-CCITT-FALSE / 增量解析与重同步 |
| `main/lb_json.c` | 载荷的规范编码（键排序、浮点毫秒对齐 Python 的 `round(x,3)`） |
| `main/lb_heartbeat.c` | `HEARTBEAT` 帧构造（与 LB-033 模拟器逐字节一致） |
| `main/lb_dispatch.c` | 命令分发占位（`HELLO`/`BEGIN`/`STEP`/`END`/`PING` → `LB-H006`） |
| `main/lb_led.c` | 灯效驱动占位（只记日志，不调用任何 GPIO/RMT） |
| `main/lb_boot.c` | 确定性启动日志 |
| `main/lb_board.c` / `lb_board.h` | 板级配置的 C 视图（数字只从生成头文件来） |
| `main/lb_uart.c` | 串口胶水（唯一依赖 ESP-IDF 的收发实现） |
| `tools/lb_host_tool.c` | 主机侧驱动**同一份源码**的小工具（测试用，不烧写） |
| `generated/` | 生成的头文件（**不入库**；见 `generated/README.md`） |

只有 `main/main.c` 与 `main/lb_uart.c` 依赖 ESP-IDF；其余源码只用 C11 标准库，
所以主机 gcc 能直接编译它们（`tests/test_firmware_frames.py` 就是这么拿到真实字节的）。
