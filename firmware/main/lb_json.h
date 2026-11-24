/* 载荷（JSON 对象）规范编码的声明。实现口径见 `lb_json.c` 顶部注释。 */
#ifndef LB_JSON_H
#define LB_JSON_H

#include <stddef.h>

#include "lb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 定长缓冲写入器：溢出只置标志，不越界。 */
typedef struct {
    char *data;
    size_t size;
    size_t used;
    int overflow;
} lb_writer_t;

void lb_writer_init(lb_writer_t *writer, char *data, size_t size);
void lb_writer_bytes(lb_writer_t *writer, const char *text);
void lb_writer_json_string(lb_writer_t *writer, const char *text);
void lb_writer_uint(lb_writer_t *writer, unsigned long value);
/** 毫秒值：与 Python `round(x, 3)` + `repr()` 形状一致。 */
void lb_writer_ms(lb_writer_t *writer, double ms);
/** 返回已写字节数；溢出返回 0。 */
size_t lb_writer_finish(lb_writer_t *writer);

/** `{"steps":N,"uptime_ms":X}` */
size_t lb_json_heartbeat(char *out, size_t out_size, unsigned long steps, double uptime_ms);
/** `{"code":"…","message":"…"}` */
size_t lb_json_fault(char *out, size_t out_size, const char *code, const char *message);

#ifdef __cplusplus
}
#endif

#endif /* LB_JSON_H */
