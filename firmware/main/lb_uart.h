/* 串口胶水（ESP-IDF 侧）：配置 UART、读写字节、按本次链路分配序号发送帧。
 *
 * 这是整份固件里唯一依赖 ESP-IDF 的「发送/接收」实现；帧的编解码全在可移植的
 * `lb_frame.c` 里（主机侧 gcc 也能编译，见 `firmware/tools/lb_host_tool.c`）。
 *
 * 已知限制（`docs/FIRMWARE.md` §8）：日志与协议帧共用 UART0。主机侧（`liveblocks hw`）
 * 靠 `LB` MAGIC 逐字节重同步，所以日志行不会让帧解析器跑偏，但会让日志片段混进坏帧计数。
 * 真机上把日志与协议分成两个口（或改成 USB-JTAG 控制台）是后续任务。
 */
#ifndef LB_UART_H
#define LB_UART_H

#include <stddef.h>
#include <stdint.h>

#include "lb_board.h"
#include "lb_frame.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 按板级配置装 UART 驱动；返回 0 = 成功，-1 = 失败。 */
int lb_uart_init(const lb_serial_cfg_t *serial);

/** 读字节：返回读到的字节数，0 = 超时，-1 = 错。 */
int lb_uart_read(uint8_t *data, size_t max_len, unsigned timeout_ms);

/** 发一帧：内部加锁、分配本次链路序号（覆盖 frame->seq）、编码后写出。
 *  返回写出的字节数；0 = 失败。 */
int lb_uart_send_frame(lb_frame_t *frame);

/** 一行日志（走 stdout / printf，不和帧共用发送锁）。 */
void lb_uart_log(const char *text);

#ifdef __cplusplus
}
#endif

#endif /* LB_UART_H */
