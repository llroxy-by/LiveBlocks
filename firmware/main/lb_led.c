/* 灯效驱动**占位**。
 *
 * LB-034 只做「接口 + 配置接线」，不驱动任何真实灯：本文件不调用任何 GPIO / RMT / LED
 * 外设接口，只把「拿到的引脚是什么、当前想要哪种灯效、但还没有驱动」打成一行日志。
 * 真实 WS2812 类驱动属后续固件任务（`docs/FIRMWARE.md` §8 已知限制）。
 *
 * 引脚来自角色名（`LB_PIN_ROLE_*`）→ `lb_board_pin()` → 生成头文件 → 板级配置 JSON，
 * 因此这里没有任何 GPIO 字面量。
 */
#include "lb_led.h"

#include <stdio.h>

#include "lb_board.h"
#include "lb_json.h"

const char *lb_led_pattern_name(lb_led_pattern_t pattern)
{
    switch (pattern) {
    case LB_LED_PATTERN_IDLE:
        return "idle";
    case LB_LED_PATTERN_RUNNING:
        return "running";
    case LB_LED_PATTERN_ERROR:
        return "error";
    default:
        return "unknown";
    }
}

size_t lb_led_init_log(char *out, size_t out_size)
{
    const lb_board_t *board = lb_board_get();
    const lb_pin_t *status_led = lb_board_pin(LB_PIN_ROLE_STATUS_LED);
    const lb_pin_t *strip = lb_board_pin(LB_PIN_ROLE_LED_STRIP_DATA);
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);
    char buffer[256];
    snprintf(buffer, sizeof buffer,
             "[fw] 灯效驱动占位：driver=%s led_count=%d status_led=GPIO%d strip_data=GPIO%d（%s）——"
             "只记录配置，未驱动任何灯\n",
             board->led_driver, board->led_count, status_led != NULL ? status_led->gpio : -1,
             strip != NULL ? strip->gpio : -1,
             (status_led != NULL && status_led->verified != 0) ? "已实测" : "待实测");
    lb_writer_bytes(&writer, buffer);
    return lb_writer_finish(&writer);
}

size_t lb_led_set_pattern_log(lb_led_pattern_t pattern, char *out, size_t out_size)
{
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);
    char buffer[160];
    snprintf(buffer, sizeof buffer, "[fw] 灯效驱动占位：请求灯效 %s（占位实现，无实际输出）\n",
             lb_led_pattern_name(pattern));
    lb_writer_bytes(&writer, buffer);
    return lb_writer_finish(&writer);
}
