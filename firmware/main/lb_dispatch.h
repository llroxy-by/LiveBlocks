/* 命令分发占位的声明。命令表由生成的头文件给出（与 `src/liveblocks/hw/protocol.py` 一致）。 */
#ifndef LB_DISPATCH_H
#define LB_DISPATCH_H

#include <stddef.h>
#include <stdint.h>

#include "lb_frame.h"
#include "lb_protocol.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 一条命令（编号 + 名字），由生成器从 `hw.protocol.COMMANDS` 给出。 */
typedef struct {
    uint8_t code;
    const char *name;
} lb_command_t;

/** 一次分发判定的结果。 */
typedef struct {
    unsigned code;
    int known;
    const char *name;
} lb_dispatch_decision_t;

size_t lb_command_count(void);
const lb_command_t *lb_command_at(size_t index);
/** 按命令号查表；未知返回 NULL。 */
const lb_command_t *lb_command_lookup(uint8_t type);

/** 只做「认不认识」的判定（纯函数，便于主机侧单测）。 */
lb_dispatch_decision_t lb_dispatch_decide(uint8_t type);

/** 一行分发日志（确定性文本，无时间戳，便于逐字节比对）。 */
size_t lb_dispatch_log_line(const lb_dispatch_decision_t *decision, char *out, size_t out_size);

/** 占位回帧的载荷（`{"code":"LB-H006","message":"…"}`）。 */
size_t lb_dispatch_fault_body(char *out, size_t out_size, const lb_dispatch_decision_t *decision);

/** 处理一帧：写日志行；返回 1 = 生成了 `reply`，0 = 只记日志，-1 = 参数错。 */
int lb_dispatch_handle(const lb_frame_t *frame, char *log_line, size_t log_size, lb_frame_t *reply,
                       uint16_t reply_seq);

#ifdef __cplusplus
}
#endif

#endif /* LB_DISPATCH_H */
