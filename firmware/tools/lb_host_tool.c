/* 主机侧（Linux / gcc）驱动**同一份固件源码**的小工具。
 *
 * 存在的理由：LB-034 的验收里有两条必须在主机上判：
 *   ① 「心跳帧与 LB-033 模拟器逐字节一致」——要能拿到固件编码器的真实输出；
 *   ② 「零硬编码 GPIO / 启动日志 / 分发占位」——要能真跑一遍看行为，而不是读注释。
 * 所以本工具把 `firmware/main/` 里的**可移植**源码（不含 main.c / lb_uart.c 这两个只认
 * ESP-IDF 的文件）编译成一个命令行程序；它在真机上没有对应入口，也不做任何烧写。
 *
 * 用法：
 *   lb_host_tool heartbeat --seq N --uptime-ms X --steps N   打印一帧 HEARTBEAT 的十六进制
 *   lb_host_tool boot-log                                   打印启动日志原文
 *   lb_host_tool pins                                       打印引脚表（来自生成的头文件）
 *   lb_host_tool dispatch --hex "4c 42 …"                   解析 + 分发占位，逐条打印结果
 *   lb_host_tool crc-selfcheck                              打印 CRC 自检（应为 0x29B1）
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lb_board.h"
#include "lb_boot.h"
#include "lb_dispatch.h"
#include "lb_frame.h"
#include "lb_heartbeat.h"
#include "lb_json.h"
#include "lb_led.h"

static void print_hex(const uint8_t *data, size_t length)
{
    for (size_t i = 0; i < length; i++) {
        printf("%02x", data[i]);
    }
    printf("\n");
}

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

static int parse_hex(const char *text, uint8_t *out, size_t out_size, size_t *out_len)
{
    size_t used = 0;
    int high = -1;
    for (const char *p = text; *p != '\0'; p++) {
        int value = hex_value(*p);
        if (value < 0) {
            if (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == ',' || *p == 'x') {
                continue;
            }
            return -1;
        }
        if (high < 0) {
            high = value;
        } else {
            if (used >= out_size) {
                return -1;
            }
            out[used++] = (uint8_t)((high << 4) | value);
            high = -1;
        }
    }
    if (high >= 0) {
        return -1;
    }
    *out_len = used;
    return 0;
}

static int cmd_crc_selfcheck(void)
{
    static const uint8_t probe[] = {'1', '2', '3', '4', '5', '6', '7', '8', '9'};
    uint16_t crc = lb_crc16_ccitt(probe, sizeof probe);
    printf("crc16_ccitt(\"123456789\") = 0x%04X\n", (unsigned)crc);
    printf("期望 0x29B1：%s\n", crc == 0x29B1u ? "一致" : "不一致（实现有缺陷）");
    return crc == 0x29B1u ? 0 : 1;
}

static int cmd_heartbeat(int argc, char **argv)
{
    unsigned long seq = 1;
    unsigned long steps = 0;
    const char *uptime_text = "0";
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--seq") == 0 && i + 1 < argc) {
            seq = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--steps") == 0 && i + 1 < argc) {
            steps = strtoul(argv[++i], NULL, 10);
        } else if (strcmp(argv[i], "--uptime-ms") == 0 && i + 1 < argc) {
            uptime_text = argv[++i];
        } else {
            fprintf(stderr, "未知参数：%s\n", argv[i]);
            return 2;
        }
    }
    if (seq == 0 || seq > LB_MAX_SEQ) {
        fprintf(stderr, "seq 必须在 1..%u\n", (unsigned)LB_MAX_SEQ);
        return 2;
    }
    /* 毫秒值按十进制文本收（模拟器侧给的也是十进制文本），避免测试自己造精度。 */
    double uptime_ms = strtod(uptime_text, NULL);
    lb_frame_t frame;
    if (lb_heartbeat_build(&frame, (uint16_t)seq, steps, uptime_ms) != 0) {
        fprintf(stderr, "构造心跳帧失败\n");
        return 1;
    }
    uint8_t buffer[LB_MAX_FRAME_SIZE];
    size_t length = lb_frame_encode(&frame, buffer, sizeof buffer);
    if (length == 0u) {
        fprintf(stderr, "编码心跳帧失败\n");
        return 1;
    }
    printf("body=%s\n", (const char *)frame.payload);
    print_hex(buffer, length);
    return 0;
}

static int cmd_boot_log(void)
{
    char text[1024];
    size_t length = lb_boot_log_format(text, sizeof text);
    if (length == 0u) {
        fprintf(stderr, "启动日志生成失败\n");
        return 1;
    }
    fputs(text, stdout);
    return 0;
}

static int cmd_pins(void)
{
    const lb_board_t *board = lb_board_get();
    printf("board=%s mcu=%s config=%s\n", board->board_id, board->mcu, board->config_path);
    printf("config_status=%s\n", board->config_status);
    for (size_t i = 0; i < lb_board_pin_count(); i++) {
        const lb_pin_t *pin = lb_board_pin_at(i);
        if (pin == NULL) {
            continue;
        }
        printf("%s gpio=%d active_low=%d verified=%d label=%s\n", pin->role, pin->gpio, pin->active_low,
               pin->verified, pin->label);
    }
    printf("serial uart=%d tx=%d rx=%d baud=%d verified=%d\n", board->serial.uart, board->serial.tx_gpio,
           board->serial.rx_gpio, board->serial.baud, board->serial.verified);
    printf("heartbeat_ms=%d verified=%d led_count=%d led_driver=%s\n", board->heartbeat_ms,
           board->heartbeat_ms_verified, board->led_count, board->led_driver);
    return 0;
}

static int cmd_dispatch(int argc, char **argv)
{
    const char *hex = NULL;
    for (int i = 0; i < argc; i++) {
        if (strcmp(argv[i], "--hex") == 0 && i + 1 < argc) {
            hex = argv[++i];
        } else {
            fprintf(stderr, "未知参数：%s\n", argv[i]);
            return 2;
        }
    }
    if (hex == NULL) {
        fprintf(stderr, "需要 --hex \"<字节>\"\n");
        return 2;
    }
    static uint8_t input[8192];
    size_t input_len = 0;
    if (parse_hex(hex, input, sizeof input, &input_len) != 0) {
        fprintf(stderr, "十六进制解析失败\n");
        return 2;
    }
    lb_frame_parser_t parser;
    lb_frame_parser_reset(&parser);
    if (lb_frame_parser_push(&parser, input, input_len) != 0) {
        fprintf(stderr, "输入太长\n");
        return 2;
    }
    unsigned frames = 0;
    uint16_t out_seq = 1;
    for (;;) {
        lb_frame_t frame;
        lb_parser_result_t result = lb_frame_parser_next(&parser, &frame);
        if (result == LB_PARSER_EMPTY) {
            break;
        }
        if (result == LB_PARSER_ERROR) {
            printf("error: %s\n", lb_decode_reason(parser.last_error));
            continue;
        }
        frames += 1;
        lb_frame_t reply;
        char line[192];
        int state = lb_dispatch_handle(&frame, line, sizeof line, &reply, out_seq);
        if (state < 0) {
            printf("error: dispatch_failed\n");
            continue;
        }
        printf("log: %s\n", line);
        if (state == 1) {
            uint8_t buffer[LB_MAX_FRAME_SIZE];
            size_t length = lb_frame_encode(&reply, buffer, sizeof buffer);
            printf("reply: ");
            print_hex(buffer, length);
            out_seq = lb_seq_next(out_seq);
        }
    }
    printf("summary: frames=%u errors=%u skipped_bytes=%u\n", frames, parser.frame_errors,
           parser.skipped_bytes);
    return 0;
}

static int usage(void)
{
    fputs("用法：lb_host_tool <heartbeat|boot-log|pins|dispatch|crc-selfcheck> [参数]\n", stderr);
    return 2;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        return usage();
    }
    const char *command = argv[1];
    if (strcmp(command, "heartbeat") == 0) {
        return cmd_heartbeat(argc - 2, argv + 2);
    }
    if (strcmp(command, "boot-log") == 0) {
        return cmd_boot_log();
    }
    if (strcmp(command, "pins") == 0) {
        return cmd_pins();
    }
    if (strcmp(command, "dispatch") == 0) {
        return cmd_dispatch(argc - 2, argv + 2);
    }
    if (strcmp(command, "crc-selfcheck") == 0) {
        return cmd_crc_selfcheck();
    }
    return usage();
}
