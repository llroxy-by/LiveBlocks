/* 上电启动日志的声明。 */
#ifndef LB_BOOT_H
#define LB_BOOT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 把启动日志写进 `out`（多行文本，确定性）；返回写入的字节数，溢出返回 0。 */
size_t lb_boot_log_format(char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif /* LB_BOOT_H */
