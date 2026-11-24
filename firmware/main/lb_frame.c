/* 串口帧编解码（`docs/HW.md` §2，线格式版本 0x01）。
 *
 * 本文件**不含任何板级常量**：线格式常量与命令 / 事件号全部来自生成的头文件
 * `lb_protocol.h`（由 `python -m liveblocks firmware headers` 从
 * `src/liveblocks/hw/{frames,protocol}.py` 生成），引脚全部来自 `lb_board.h`。
 * 因此 C 与 Python 两端对同一对象的编码是逐字节一致的 —— LB-034 的心跳比对验收依赖这条。
 *
 * 本文件只用 C11 标准库，不引用任何 ESP-IDF 头文件，主机侧可直接用 gcc 编译（见
 * `firmware/tools/lb_host_tool.c`）。
 */
#include "lb_frame.h"

#include <string.h>

uint16_t lb_crc16_ccitt(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    if (data == NULL) {
        return crc;
    }
    for (size_t i = 0; i < len; i++) {
        crc = (uint16_t)(crc ^ (uint16_t)((uint16_t)data[i] << 8));
        for (int bit = 0; bit < 8; bit++) {
            if ((crc & 0x8000u) != 0u) {
                crc = (uint16_t)((uint16_t)(crc << 1) ^ (uint16_t)0x1021);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}

uint16_t lb_seq_next(uint16_t seq)
{
    unsigned next = (unsigned)seq + 1u;
    return (uint16_t)(next > (unsigned)LB_MAX_SEQ ? 1u : next);
}

size_t lb_frame_encode(const lb_frame_t *frame, uint8_t *out, size_t out_size)
{
    if (frame == NULL || out == NULL) {
        return 0;
    }
    if (frame->seq == 0u || frame->payload_len > (uint16_t)LB_MAX_PAYLOAD) {
        return 0;
    }
    size_t total = (size_t)LB_HEADER_SIZE + (size_t)frame->payload_len + (size_t)LB_CRC_SIZE;
    if (out_size < total) {
        return 0;
    }
    out[0] = (uint8_t)LB_MAGIC_0;
    out[1] = (uint8_t)LB_MAGIC_1;
    out[2] = (uint8_t)LB_WIRE_VERSION;
    out[3] = frame->kind;
    out[4] = frame->type;
    out[5] = (uint8_t)(frame->seq & 0xFFu);
    out[6] = (uint8_t)((frame->seq >> 8) & 0xFFu);
    out[7] = (uint8_t)(frame->payload_len & 0xFFu);
    out[8] = (uint8_t)((frame->payload_len >> 8) & 0xFFu);
    if (frame->payload_len > 0u) {
        memcpy(out + LB_HEADER_SIZE, frame->payload, frame->payload_len);
    }
    /* CRC 覆盖 VER..PAYLOAD（不含 MAGIC），与 src/liveblocks/hw/frames.py 一致。 */
    uint16_t crc = lb_crc16_ccitt(out + 2, (size_t)(LB_HEADER_SIZE - 2) + (size_t)frame->payload_len);
    out[LB_HEADER_SIZE + frame->payload_len] = (uint8_t)(crc & 0xFFu);
    out[LB_HEADER_SIZE + frame->payload_len + 1u] = (uint8_t)((crc >> 8) & 0xFFu);
    return total;
}

const char *lb_decode_reason(lb_decode_status_t status)
{
    switch (status) {
    case LB_DECODE_OK:
        return "ok";
    case LB_DECODE_TRUNCATED:
        return "truncated";
    case LB_DECODE_BAD_MAGIC:
        return "bad_magic";
    case LB_DECODE_BAD_VERSION:
        return "bad_version";
    case LB_DECODE_BAD_KIND:
        return "bad_kind";
    case LB_DECODE_BAD_TYPE:
        return "bad_type";
    case LB_DECODE_LENGTH:
        return "length";
    case LB_DECODE_CRC:
        return "crc";
    default:
        return "unknown";
    }
}

lb_decode_status_t lb_frame_decode(const uint8_t *buf, size_t have, lb_frame_t *frame, size_t *consumed)
{
    if (consumed != NULL) {
        *consumed = 0;
    }
    if (buf == NULL) {
        return LB_DECODE_TRUNCATED;
    }
    if (have < (size_t)LB_MAGIC_SIZE) {
        return LB_DECODE_TRUNCATED;
    }
    if (buf[0] != (uint8_t)LB_MAGIC_0 || buf[1] != (uint8_t)LB_MAGIC_1) {
        return LB_DECODE_BAD_MAGIC;
    }
    if (have < (size_t)LB_HEADER_SIZE) {
        return LB_DECODE_TRUNCATED;
    }
    uint8_t version = buf[2];
    if (version != (uint8_t)LB_WIRE_VERSION) {
        return LB_DECODE_BAD_VERSION;
    }
    uint8_t kind = buf[3];
    if (kind != (uint8_t)LB_KIND_COMMAND && kind != (uint8_t)LB_KIND_EVENT) {
        return LB_DECODE_BAD_KIND;
    }
    uint8_t type = buf[4];
    if ((kind == (uint8_t)LB_KIND_COMMAND && type >= 0x80u) ||
        (kind == (uint8_t)LB_KIND_EVENT && type < 0x80u)) {
        return LB_DECODE_BAD_TYPE;
    }
    uint16_t seq = (uint16_t)((uint16_t)buf[5] | (uint16_t)((uint16_t)buf[6] << 8));
    uint16_t length = (uint16_t)((uint16_t)buf[7] | (uint16_t)((uint16_t)buf[8] << 8));
    if (length > (uint16_t)LB_MAX_PAYLOAD) {
        return LB_DECODE_LENGTH;
    }
    size_t total = (size_t)LB_HEADER_SIZE + (size_t)length + (size_t)LB_CRC_SIZE;
    if (have < total) {
        return LB_DECODE_TRUNCATED;
    }
    uint16_t expected = lb_crc16_ccitt(buf + 2, (size_t)(LB_HEADER_SIZE - 2) + (size_t)length);
    uint16_t actual = (uint16_t)((uint16_t)buf[LB_HEADER_SIZE + length] |
                                 (uint16_t)((uint16_t)buf[LB_HEADER_SIZE + length + 1u] << 8));
    if (expected != actual) {
        return LB_DECODE_CRC;
    }
    if (frame != NULL) {
        frame->kind = kind;
        frame->type = type;
        frame->seq = seq;
        frame->payload_len = length;
        if (length > 0u) {
            memcpy(frame->payload, buf + LB_HEADER_SIZE, length);
        }
    }
    if (consumed != NULL) {
        *consumed = total;
    }
    return LB_DECODE_OK;
}

void lb_frame_parser_reset(lb_frame_parser_t *parser)
{
    if (parser == NULL) {
        return;
    }
    parser->used = 0;
    parser->skipped_bytes = 0;
    parser->frame_errors = 0;
    parser->last_error = LB_DECODE_OK;
}

int lb_frame_parser_push(lb_frame_parser_t *parser, const uint8_t *chunk, size_t len)
{
    if (parser == NULL) {
        return -1;
    }
    if (len == 0u) {
        return 0;
    }
    if (chunk == NULL || parser->used + len > (size_t)LB_MAX_FRAME_SIZE) {
        return -1;
    }
    memcpy(parser->buffer + parser->used, chunk, len);
    parser->used += len;
    return 0;
}

lb_parser_result_t lb_frame_parser_next(lb_frame_parser_t *parser, lb_frame_t *frame)
{
    if (parser == NULL || parser->used == 0u) {
        return LB_PARSER_EMPTY;
    }
    size_t consumed = 0;
    lb_decode_status_t status = lb_frame_decode(parser->buffer, parser->used, frame, &consumed);
    if (status == LB_DECODE_OK) {
        memmove(parser->buffer, parser->buffer + consumed, parser->used - consumed);
        parser->used -= consumed;
        return LB_PARSER_FRAME;
    }
    if (status == LB_DECODE_TRUNCATED) {
        return LB_PARSER_EMPTY;
    }
    parser->last_error = status;
    if (status == LB_DECODE_BAD_MAGIC || status == LB_DECODE_BAD_VERSION || status == LB_DECODE_BAD_KIND ||
        status == LB_DECODE_BAD_TYPE) {
        /* 只丢 1 字节，继续找下一个 MAGIC。
         *
         * 为什么连「帧头字段不认识」也只丢 1 字节：一旦 MAGIC 是假的（串口上混着日志文本，
         * 例如日志里出现 `LB-H006` 这种字样），帧头里的 LEN 就完全不可信，按它丢整帧候选会
         * 把后面真正的帧一起吃掉。逐字节重同步最多多扫几个字节，但不会吞数据。
         * 这里比 Python 侧（`hw/frames.py` 按 LEN 丢整帧候选）更保守，差异记在
         * `docs/FIRMWARE.md` §8；`tests/test_firmware_frames.py` 覆盖「日志文本里带 LB-H006」。 */
        memmove(parser->buffer, parser->buffer + 1, parser->used - 1u);
        parser->used -= 1u;
        parser->skipped_bytes += 1u;
        return LB_PARSER_ERROR;
    }
    /* 帧头认识但内容不可信：按帧头里的 LEN 丢掉整个候选帧，避免错位解析。 */
    size_t drop = (size_t)LB_HEADER_SIZE + (size_t)LB_CRC_SIZE;
    if (parser->used >= (size_t)LB_HEADER_SIZE) {
        size_t declared = (size_t)parser->buffer[7] | ((size_t)parser->buffer[8] << 8);
        drop = (size_t)LB_HEADER_SIZE + declared + (size_t)LB_CRC_SIZE;
    }
    if (drop > parser->used) {
        drop = parser->used;
    }
    memmove(parser->buffer, parser->buffer + drop, parser->used - drop);
    parser->used -= drop;
    parser->frame_errors += 1u;
    return LB_PARSER_ERROR;
}
