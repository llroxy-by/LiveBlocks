/* 载荷（JSON 对象）的**规范编码**：键排序、无空格、非 ASCII 原样。
 *
 * 与 `src/liveblocks/hw/frames.py::encode_body` 逐字节对齐（`json.dumps(..., sort_keys=True,
 * separators=(",", ":"), ensure_ascii=False)`）。只有这条对上了，「心跳帧与 LB-033 模拟器
 * 逐字节一致」才可能成立，所以浮点毫秒的格式化也必须和 Python 的
 * `round(x, 3)` + `repr()` 形状一致（见 `lb_write_ms`）。
 *
 * 只用 C11 标准库，主机侧可用 gcc 直接编译。
 */
#include "lb_json.h"

#include <stdio.h>
#include <string.h>

void lb_writer_init(lb_writer_t *writer, char *data, size_t size)
{
    writer->data = data;
    writer->size = size;
    writer->used = 0;
    writer->overflow = 0;
    if (size > 0u) {
        data[0] = '\0';
    }
}

void lb_writer_bytes(lb_writer_t *writer, const char *text)
{
    if (writer->overflow) {
        return;
    }
    size_t len = strlen(text);
    if (writer->used + len + 1u > writer->size) {
        writer->overflow = 1;
        return;
    }
    memcpy(writer->data + writer->used, text, len);
    writer->used += len;
    writer->data[writer->used] = '\0';
}

/** JSON 字符串内容转义（与 Python `json.dumps` 的默认转义口径一致，非 ASCII 原样输出）。 */
void lb_writer_json_string(lb_writer_t *writer, const char *text)
{
    static const char *const hex = "0123456789abcdef";
    char escaped[8];
    lb_writer_bytes(writer, "\"");
    for (const unsigned char *p = (const unsigned char *)text; *p != 0u; p++) {
        switch (*p) {
        case '"':
            lb_writer_bytes(writer, "\\\"");
            break;
        case '\\':
            lb_writer_bytes(writer, "\\\\");
            break;
        case '\b':
            lb_writer_bytes(writer, "\\b");
            break;
        case '\f':
            lb_writer_bytes(writer, "\\f");
            break;
        case '\n':
            lb_writer_bytes(writer, "\\n");
            break;
        case '\r':
            lb_writer_bytes(writer, "\\r");
            break;
        case '\t':
            lb_writer_bytes(writer, "\\t");
            break;
        default:
            if (*p < 0x20u) {
                escaped[0] = '\\';
                escaped[1] = 'u';
                escaped[2] = '0';
                escaped[3] = '0';
                escaped[4] = hex[(*p >> 4) & 0x0Fu];
                escaped[5] = hex[*p & 0x0Fu];
                escaped[6] = '\0';
                lb_writer_bytes(writer, escaped);
            } else {
                escaped[0] = (char)*p;
                escaped[1] = '\0';
                lb_writer_bytes(writer, escaped);
            }
            break;
        }
    }
    lb_writer_bytes(writer, "\"");
}

void lb_writer_uint(lb_writer_t *writer, unsigned long value)
{
    char buffer[24];
    snprintf(buffer, sizeof buffer, "%lu", value);
    lb_writer_bytes(writer, buffer);
}

void lb_writer_ms(lb_writer_t *writer, double ms)
{
    char buffer[48];
    if (!(ms > 0.0)) { /* 运行时长不会是负数；NaN 也归零 */
        ms = 0.0;
    }
    snprintf(buffer, sizeof buffer, "%.3f", ms);
    /* 去掉小数部分末尾多余的 0，但至少保留一位小数：
     * 1234.000 -> "1234.0"、12.500 -> "12.5"、0.001 -> "0.001"。
     * 这与 Python `round(x, 3)` 之后 `repr()` 的形状一致（整数毫秒也带 ".0"）。 */
    char *dot = strchr(buffer, '.');
    if (dot != NULL) {
        char *end = buffer + strlen(buffer) - 1;
        while (end > dot + 1 && *end == '0') {
            *end = '\0';
            end--;
        }
    }
    lb_writer_bytes(writer, buffer);
}

size_t lb_writer_finish(lb_writer_t *writer)
{
    if (writer->overflow) {
        return 0;
    }
    return writer->used;
}

size_t lb_json_heartbeat(char *out, size_t out_size, unsigned long steps, double uptime_ms)
{
    /* 键排序：steps < uptime_ms（Python sort_keys=True 的结果） */
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);
    lb_writer_bytes(&writer, "{\"steps\":");
    lb_writer_uint(&writer, steps);
    lb_writer_bytes(&writer, ",\"uptime_ms\":");
    lb_writer_ms(&writer, uptime_ms);
    lb_writer_bytes(&writer, "}");
    return lb_writer_finish(&writer);
}

size_t lb_json_fault(char *out, size_t out_size, const char *code, const char *message)
{
    /* 键排序：code < message */
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);
    lb_writer_bytes(&writer, "{\"code\":");
    lb_writer_json_string(&writer, code);
    lb_writer_bytes(&writer, ",\"message\":");
    lb_writer_json_string(&writer, message);
    lb_writer_bytes(&writer, "}");
    return lb_writer_finish(&writer);
}
