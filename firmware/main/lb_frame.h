/* 串口帧格式的 C 侧声明（与 `src/liveblocks/hw/frames.py` 一一对应）。
 *
 * 线格式常量（MAGIC / VER / KIND / 上限）来自**生成的头文件** `lb_protocol.h`，
 * 生成命令：`python -m liveblocks firmware headers --config config/board.esp32c3-supermini.json`
 * 生成源：`src/liveblocks/hw/{frames,protocol}.py`（唯一权威）。
 */
#ifndef LB_FRAME_H
#define LB_FRAME_H

#include <stddef.h>
#include <stdint.h>

#include "lb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 帧头字节数（含 MAGIC）。 */
#define LB_HEADER_SIZE 9
/** CRC 字节数。 */
#define LB_CRC_SIZE 2
/** 一帧的最大字节数。 */
#define LB_MAX_FRAME_SIZE (LB_HEADER_SIZE + LB_MAX_PAYLOAD + LB_CRC_SIZE)

/** 一帧（`kind` / `type` / `seq` + 已编码的载荷）。 */
typedef struct {
    uint8_t kind;
    uint8_t type;
    uint16_t seq;
    uint16_t payload_len;
    uint8_t payload[LB_MAX_PAYLOAD];
} lb_frame_t;

/** 解码结果（`reason` 字符串与 Python 侧 `FrameError.reason` 同名）。 */
typedef enum {
    LB_DECODE_OK = 0,
    LB_DECODE_TRUNCATED,
    LB_DECODE_BAD_MAGIC,
    LB_DECODE_BAD_VERSION,
    LB_DECODE_BAD_KIND,
    LB_DECODE_BAD_TYPE,
    LB_DECODE_LENGTH,
    LB_DECODE_CRC
} lb_decode_status_t;

/** 增量解析器的一次取帧结果。 */
typedef enum {
    LB_PARSER_EMPTY = 0,
    LB_PARSER_FRAME,
    LB_PARSER_ERROR
} lb_parser_result_t;

/** CRC-16/CCITT-FALSE（poly 0x1021、init 0xFFFF、不反射、xorout 0）。 */
uint16_t lb_crc16_ccitt(const uint8_t *data, size_t len);

/** 序号递增：0xFFFF 之后回到 1（0 不用）。 */
uint16_t lb_seq_next(uint16_t seq);

/** 编码一帧；返回写入的字节数，参数不合法或缓冲不够时返回 0。 */
size_t lb_frame_encode(const lb_frame_t *frame, uint8_t *out, size_t out_size);

/** 从缓冲区开头解一帧；`consumed` 返回吃掉的字节数（`BAD_MAGIC` 时为 0）。 */
lb_decode_status_t lb_frame_decode(const uint8_t *buf, size_t have, lb_frame_t *frame, size_t *consumed);

/** 解码结果的机器可读名字（与 Python 侧同名词）。 */
const char *lb_decode_reason(lb_decode_status_t status);

/** 增量解析器：任意切片喂入，坏帧丢帧后继续（重同步）。 */
typedef struct {
    uint8_t buffer[LB_MAX_FRAME_SIZE];
    size_t used;
    unsigned skipped_bytes;
    unsigned frame_errors;
    lb_decode_status_t last_error;
} lb_frame_parser_t;

void lb_frame_parser_reset(lb_frame_parser_t *parser);
/** 追加字节；缓冲不够返回 -1。 */
int lb_frame_parser_push(lb_frame_parser_t *parser, const uint8_t *chunk, size_t len);
/** 取下一帧：`LB_PARSER_FRAME` 有帧、`LB_PARSER_EMPTY` 需要更多字节、`LB_PARSER_ERROR` 丢了一个坏帧。 */
lb_parser_result_t lb_frame_parser_next(lb_frame_parser_t *parser, lb_frame_t *frame);

#ifdef __cplusplus
}
#endif

#endif /* LB_FRAME_H */
