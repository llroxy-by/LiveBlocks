/* 板级配置的 C 侧实现：只做「生成头文件 → 结构体」这层搬运，不含任何 GPIO 字面量。 */
#include "lb_board.h"

#include <string.h>

/* 引脚的数组初始化表由生成器给出：每行一个角色，顺序即配置文件里的键序（排序后）。 */
static const lb_pin_t lb_pins[] = {LB_BOARD_PINS_INIT};

static const lb_board_t lb_board = {
    .board_id = LB_BOARD_ID,
    .board_name = LB_BOARD_NAME,
    .mcu = LB_BOARD_MCU,
    .config_path = LB_BOARD_CONFIG_PATH,
    .config_status = LB_BOARD_CONFIG_STATUS,
    .config_sha256 = LB_BOARD_CONFIG_SHA256,
    .serial =
        {
            .uart = LB_BOARD_SERIAL_UART,
            .tx_gpio = LB_BOARD_SERIAL_TX_GPIO,
            .rx_gpio = LB_BOARD_SERIAL_RX_GPIO,
            .baud = LB_BOARD_SERIAL_BAUD,
            .verified = LB_BOARD_SERIAL_VERIFIED,
            .note = LB_BOARD_SERIAL_NOTE,
        },
    .heartbeat_ms = LB_BOARD_HEARTBEAT_MS,
    .heartbeat_ms_verified = LB_BOARD_HEARTBEAT_MS_VERIFIED,
    .heartbeat_note = LB_BOARD_HEARTBEAT_NOTE,
    .led_count = LB_BOARD_LED_COUNT,
    .led_driver = LB_BOARD_LED_DRIVER,
};

const lb_board_t *lb_board_get(void)
{
    return &lb_board;
}

size_t lb_board_pin_count(void)
{
    return sizeof lb_pins / sizeof lb_pins[0];
}

const lb_pin_t *lb_board_pin_at(size_t index)
{
    if (index >= lb_board_pin_count()) {
        return NULL;
    }
    return &lb_pins[index];
}

const lb_pin_t *lb_board_pin(const char *role)
{
    if (role == NULL) {
        return NULL;
    }
    for (size_t i = 0; i < lb_board_pin_count(); i++) {
        if (strcmp(lb_pins[i].role, role) == 0) {
            return &lb_pins[i];
        }
    }
    return NULL;
}
