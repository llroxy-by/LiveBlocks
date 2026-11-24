/* 上电启动日志：把「我是谁、拿的哪份配置、引脚是什么、心跳多快」一次性打出来。
 *
 * 文本是**确定性**的（无时间戳、无地址、无随机量），所以主机侧测试可以逐字节比对
 * （`tests/test_firmware_frames.py` 与 `firmware/tools/lb_host_tool.c boot-log`）。
 * 里面出现的每个引脚数字都来自 `lb_board`（即生成头文件 → `config/board.esp32c3-supermini.json`）。
 */
#include "lb_boot.h"

#include <stdarg.h>
#include <stdio.h>

#include "lb_board.h"
#include "lb_dispatch.h"
#include "lb_json.h"
#include "lb_version.h"

/** snprintf 到临时缓冲再写入（保持 lb_writer_t 的越界安全）。 */
static void boot_printf(lb_writer_t *writer, const char *format, ...)
{
    char buffer[256];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof buffer, format, args);
    va_end(args);
    lb_writer_bytes(writer, buffer);
}

static const char *verified_text(int verified)
{
    return verified != 0 ? "已实测" : "待实测";
}

static void append_pin_line(lb_writer_t *writer, const lb_board_t *board)
{
    lb_writer_bytes(writer, "[fw] 引脚（来自 ");
    lb_writer_bytes(writer, board->config_path);
    lb_writer_bytes(writer, "）：");
    for (size_t i = 0; i < lb_board_pin_count(); i++) {
        const lb_pin_t *pin = lb_board_pin_at(i);
        if (pin == NULL) {
            continue;
        }
        if (i > 0u) {
            lb_writer_bytes(writer, "、");
        }
        boot_printf(writer, "%s=GPIO%d(%s%s)", pin->role, pin->gpio,
                    pin->active_low != 0 ? "低有效/" : "", verified_text(pin->verified));
    }
    lb_writer_bytes(writer, "\n");
}

static void append_command_line(lb_writer_t *writer)
{
    lb_writer_bytes(writer, "[fw] 命令分发占位已就绪（业务未实现）：");
    for (size_t i = 0; i < lb_command_count(); i++) {
        const lb_command_t *command = lb_command_at(i);
        if (command == NULL) {
            continue;
        }
        if (i > 0u) {
            lb_writer_bytes(writer, " ");
        }
        boot_printf(writer, "%s(0x%02X)", command->name, (unsigned)command->code);
    }
    lb_writer_bytes(writer, "\n");
}

size_t lb_boot_log_format(char *out, size_t out_size)
{
    const lb_board_t *board = lb_board_get();
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);

    boot_printf(&writer, "[fw] 序界 LiveBlocks 固件骨架 fw=%s（%s）\n", LB_FIRMWARE_VERSION,
                LB_FIRMWARE_BUILD);
    boot_printf(&writer, "[fw] 协议：串口帧 %02X %02X / 线格式 %d / 载荷上限 %d B / 心跳事件 0x%02X\n",
                (unsigned)LB_MAGIC_0, (unsigned)LB_MAGIC_1, (int)LB_WIRE_VERSION, (int)LB_MAX_PAYLOAD,
                (unsigned)LB_EVT_HEARTBEAT);
    boot_printf(&writer, "[fw] 板子：%s（%s）board_id=%s\n", board->board_name, board->mcu,
                board->board_id);
    boot_printf(&writer, "[fw] 配置状态：%s\n", board->config_status);
    append_pin_line(&writer, board);
    boot_printf(&writer, "[fw] 串口：UART%d tx=GPIO%d rx=GPIO%d @%d bps（%s）\n", board->serial.uart,
                board->serial.tx_gpio, board->serial.rx_gpio, board->serial.baud,
                verified_text(board->serial.verified));
    boot_printf(&writer, "[fw] 心跳：每 %d ms 上行一帧 HEARTBEAT（周期%s）\n", board->heartbeat_ms,
                verified_text(board->heartbeat_ms_verified));
    append_command_line(&writer);
    boot_printf(&writer, "[fw] 灯效驱动占位已就绪：led_count=%d driver=%s（未驱动任何灯）\n",
                board->led_count, board->led_driver);
    return lb_writer_finish(&writer);
}
