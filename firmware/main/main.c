/* LB-034 固件骨架的入口（ESP-IDF 侧）。
 *
 * 上电做的事，按顺序：
 *   1. 打印启动日志（板子、配置来源、引脚、心跳周期、命令表）——`lb_boot.c`；
 *   2. 初始化灯效驱动**占位**并打一行「未驱动任何灯」——`lb_led.c`；
 *   3. 装 UART 驱动（串口参数来自板级配置）；
 *   4. 起两个任务：`protocol_task` 收帧 → 命令分发占位 → 回 FAULT；
 *      `heartbeat_task` 每 `heartbeat_ms` 上行一帧 `HEARTBEAT`。
 *
 * 这里没有任何 GPIO 数字、没有烧写动作、没有业务实现：本文件是骨架，不是能跑业务的固件。
 */
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_timer.h"

#include "lb_board.h"
#include "lb_boot.h"
#include "lb_dispatch.h"
#include "lb_frame.h"
#include "lb_heartbeat.h"
#include "lb_led.h"
#include "lb_uart.h"
#include "lb_version.h"

/** 读串口的一次等待（毫秒）：决定收帧延迟与任务调度粒度。 */
#define LB_PROTOCOL_READ_TIMEOUT_MS 100

/** 已经执行过的步数：骨架阶段恒为 0（不实现 STEP 业务），心跳里如实报 0。 */
static unsigned long s_steps_executed = 0;

static double lb_uptime_ms(void)
{
    return (double)esp_timer_get_time() / 1000.0;
}

/** 收帧 → 分发占位 → 回 FAULT。 */
static void protocol_task(void *argument)
{
    (void)argument;
    lb_frame_parser_t parser;
    lb_frame_parser_reset(&parser);
    uint8_t chunk[128];

    for (;;) {
        int got = lb_uart_read(chunk, sizeof chunk, LB_PROTOCOL_READ_TIMEOUT_MS);
        if (got < 0) {
            lb_uart_log("[fw] 串口读失败（LB-H001 串口断连），本骨架不重连，等待人工复位\n");
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }
        if (got == 0) {
            continue;
        }
        if (lb_frame_parser_push(&parser, chunk, (size_t)got) != 0) {
            lb_uart_log("[fw] 接收缓冲溢出，丢弃未成帧的字节并重新同步\n");
            lb_frame_parser_reset(&parser);
            continue;
        }
        for (;;) {
            lb_frame_t frame;
            lb_parser_result_t result = lb_frame_parser_next(&parser, &frame);
            if (result == LB_PARSER_EMPTY) {
                break;
            }
            if (result == LB_PARSER_ERROR) {
                char line[192];
                snprintf(line, sizeof line, "[fw] 丢弃坏帧：%s（累计丢字节 %u / 坏帧 %u）\n",
                         lb_decode_reason(parser.last_error), parser.skipped_bytes, parser.frame_errors);
                lb_uart_log(line);
                continue;
            }
            lb_frame_t reply;
            char line[192];
            int state = lb_dispatch_handle(&frame, line, sizeof line, &reply, 1u);
            if (state < 0) {
                lb_uart_log("[fw] 命令分发占位：参数不合法，丢弃这一帧\n");
                continue;
            }
            lb_uart_log(line);
            if (state == 1) {
                /* 序号由 lb_uart_send_frame 在发送锁里分配（这里传的值会被覆盖）。 */
                (void)lb_uart_send_frame(&reply);
            }
        }
    }
}

/** 每 heartbeat_ms 上行一帧 HEARTBEAT（周期来自板级配置，默认值待实测）。 */
static void heartbeat_task(void *argument)
{
    (void)argument;
    const lb_board_t *board = lb_board_get();
    TickType_t period = pdMS_TO_TICKS((uint32_t)board->heartbeat_ms);
    if (period == 0u) {
        period = 1;
    }
    for (;;) {
        vTaskDelay(period);
        lb_frame_t frame;
        if (lb_heartbeat_build(&frame, 1u, s_steps_executed, lb_uptime_ms()) != 0) {
            continue;
        }
        (void)lb_uart_send_frame(&frame);
    }
}

void app_main(void)
{
    char text[1024];

    if (lb_boot_log_format(text, sizeof text) > 0u) {
        lb_uart_log(text);
    } else {
        lb_uart_log("[fw] 启动日志生成失败（缓冲不够），继续启动\n");
    }

    if (lb_led_init_log(text, sizeof text) > 0u) {
        lb_uart_log(text);
    }
    if (lb_led_set_pattern_log(LB_LED_PATTERN_IDLE, text, sizeof text) > 0u) {
        lb_uart_log(text);
    }

    const lb_board_t *board = lb_board_get();
    if (lb_uart_init(&board->serial) != 0) {
        lb_uart_log("[fw] 串口初始化失败，协议与心跳都不工作（骨架到此为止）\n");
        return;
    }
    lb_uart_log("[fw] 串口已就绪：收帧 → 命令分发占位；心跳任务已启动\n");

    if (xTaskCreate(protocol_task, "lb_protocol", 4096, NULL, 5, NULL) != pdPASS) {
        lb_uart_log("[fw] 协议任务创建失败\n");
        return;
    }
    if (xTaskCreate(heartbeat_task, "lb_heartbeat", 3072, NULL, 4, NULL) != pdPASS) {
        lb_uart_log("[fw] 心跳任务创建失败\n");
        return;
    }
    lb_uart_log("[fw] 固件骨架启动完成（" LB_FIRMWARE_VERSION " / " LB_FIRMWARE_BUILD "）\n");
}
