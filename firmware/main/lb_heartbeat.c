/* 串口心跳（`HEARTBEAT`，事件号取自生成的头文件）的**帧构造**，不含任何阻塞或计时逻辑。
 *
 * 帧形状必须与 LB-033 的模拟器逐字节一致：
 *   `src/liveblocks/hw/device.py::_maybe_heartbeat` →
 *   `event_frame("HEARTBEAT", seq, {"uptime_ms": …, "steps": …})`。
 * 逐字节比对测试见 `tests/test_firmware_parity.py`（真的从 pty 模拟器取帧来比）。
 */
#include "lb_heartbeat.h"

#include <string.h>

#include "lb_json.h"

int lb_heartbeat_build(lb_frame_t *frame, uint16_t seq, unsigned long steps, double uptime_ms)
{
    if (frame == NULL || seq == 0u) {
        return -1;
    }
    memset(frame, 0, sizeof *frame);
    size_t body_len = lb_json_heartbeat((char *)frame->payload, sizeof frame->payload, steps, uptime_ms);
    if (body_len == 0u) {
        return -1;
    }
    frame->kind = (uint8_t)LB_KIND_EVENT;
    frame->type = (uint8_t)LB_EVT_HEARTBEAT;
    frame->seq = seq;
    frame->payload_len = (uint16_t)body_len;
    return 0;
}
