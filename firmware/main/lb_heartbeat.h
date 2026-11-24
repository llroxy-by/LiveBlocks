/* 串口心跳的帧构造声明。 */
#ifndef LB_HEARTBEAT_H
#define LB_HEARTBEAT_H

#include "lb_frame.h"

#ifdef __cplusplus
extern "C" {
#endif

/** 造一帧 `HEARTBEAT`（`{"steps":N,"uptime_ms":X}`）；返回 0 = 成功，-1 = 参数错。 */
int lb_heartbeat_build(lb_frame_t *frame, uint16_t seq, unsigned long steps, double uptime_ms);

#ifdef __cplusplus
}
#endif

#endif /* LB_HEARTBEAT_H */
