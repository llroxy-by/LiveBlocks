/* 板级配置的 C 侧视图。
 *
 * **引脚数字只从生成的头文件 `lb_board_config.h` 进来**（来源
 * `config/board.esp32c3-supermini.json`），本文件与其它源码里一个裸 GPIO 数字都没有：
 * 生成命令 `python -m liveblocks firmware headers --config config/board.esp32c3-supermini.json`。
 * 直接扫源码找 GPIO 数字的检查见 `python -m liveblocks firmware scan`（测试
 * `tests/test_firmware_board.py::test_no_bare_gpio_numbers_in_firmware_sources`）。
 */
#ifndef LB_BOARD_H
#define LB_BOARD_H

#include <stddef.h>

#include "lb_board_config.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 一个命名引脚（角色名、给人看的标签、GPIO、极性、是否已实测、待实测说明）。 */
typedef struct {
    const char *role;
    const char *label;
    int gpio;
    int active_low;
    int verified;
    const char *note;
} lb_pin_t;

/** 串口配置。 */
typedef struct {
    int uart;
    int tx_gpio;
    int rx_gpio;
    int baud;
    int verified;
    const char *note;
} lb_serial_cfg_t;

/** 一块板子的全部配置。 */
typedef struct {
    const char *board_id;
    const char *board_name;
    const char *mcu;
    const char *config_path;
    const char *config_status;
    const char *config_sha256;
    lb_serial_cfg_t serial;
    int heartbeat_ms;
    int heartbeat_ms_verified;
    const char *heartbeat_note;
    int led_count;
    const char *led_driver;
} lb_board_t;

/** 只读的板级配置（静态存储期，永不释放）。 */
const lb_board_t *lb_board_get(void);

/** 按角色名取引脚；未知角色返回 NULL。 */
const lb_pin_t *lb_board_pin(const char *role);

/** 命名引脚个数。 */
size_t lb_board_pin_count(void);

/** 按下标取引脚；越界返回 NULL。 */
const lb_pin_t *lb_board_pin_at(size_t index);

#ifdef __cplusplus
}
#endif

#endif /* LB_BOARD_H */
