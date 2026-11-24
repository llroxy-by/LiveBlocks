/* 命令分发**占位**：认出命令号、打一行日志、回一条 FAULT 明说「本骨架没实现业务」。
 *
 * 为什么占位也回 FAULT：主机侧（`liveblocks hw`）对「设备回了什么」是有判据的，
 * 静默丢弃会让日志看起来像丢帧。回一条 `LB-H006`（设备故障）能让人一眼看出
 * 「帧到了、CRC 对、命令号认识、但业务没实现」——这是骨架阶段该给出的诚实信号，
 * 而不是假装跑通。真实实现属后续固件任务（见 `docs/FIRMWARE.md` §8 已知限制）。
 */
#include "lb_dispatch.h"

#include <stdio.h>
#include <string.h>

#include "lb_json.h"

static const lb_command_t lb_commands[] = {LB_COMMAND_TABLE_INIT};

size_t lb_command_count(void)
{
    return sizeof lb_commands / sizeof lb_commands[0];
}

const lb_command_t *lb_command_at(size_t index)
{
    if (index >= lb_command_count()) {
        return NULL;
    }
    return &lb_commands[index];
}

const lb_command_t *lb_command_lookup(uint8_t type)
{
    for (size_t i = 0; i < lb_command_count(); i++) {
        if (lb_commands[i].code == type) {
            return &lb_commands[i];
        }
    }
    return NULL;
}

lb_dispatch_decision_t lb_dispatch_decide(uint8_t type)
{
    lb_dispatch_decision_t decision;
    decision.code = type;
    const lb_command_t *command = lb_command_lookup(type);
    if (command != NULL) {
        decision.known = 1;
        decision.name = command->name;
    } else {
        decision.known = 0;
        decision.name = "UNKNOWN";
    }
    return decision;
}

size_t lb_dispatch_log_line(const lb_dispatch_decision_t *decision, char *out, size_t out_size)
{
    lb_writer_t writer;
    lb_writer_init(&writer, out, out_size);
    if (decision->known) {
        char code_text[8];
        snprintf(code_text, sizeof code_text, "0x%02X", (unsigned)decision->code);
        lb_writer_bytes(&writer, "[fw] 命令分发占位：收到命令 ");
        lb_writer_bytes(&writer, decision->name);
        lb_writer_bytes(&writer, "(");
        lb_writer_bytes(&writer, code_text);
        lb_writer_bytes(&writer, ")，本骨架未实现业务，回 FAULT ");
        lb_writer_bytes(&writer, LB_DIAG_DEVICE_FAULT);
    } else {
        char code_text[8];
        snprintf(code_text, sizeof code_text, "0x%02X", (unsigned)decision->code);
        lb_writer_bytes(&writer, "[fw] 命令分发占位：未知命令号 ");
        lb_writer_bytes(&writer, code_text);
        lb_writer_bytes(&writer, "，回 FAULT ");
        lb_writer_bytes(&writer, LB_DIAG_DEVICE_FAULT);
    }
    return lb_writer_finish(&writer);
}

size_t lb_dispatch_fault_body(char *out, size_t out_size, const lb_dispatch_decision_t *decision)
{
    char message[96];
    if (decision->known) {
        snprintf(message, sizeof message, "命令分发占位：%s 未实现业务", decision->name);
    } else {
        snprintf(message, sizeof message, "命令分发占位：未知命令 0x%02X", (unsigned)decision->code);
    }
    return lb_json_fault(out, out_size, LB_DIAG_DEVICE_FAULT, message);
}

int lb_dispatch_handle(const lb_frame_t *frame, char *log_line, size_t log_size, lb_frame_t *reply,
                       uint16_t reply_seq)
{
    if (frame == NULL || log_line == NULL || log_size == 0u) {
        return -1;
    }
    lb_dispatch_decision_t decision = lb_dispatch_decide(frame->type);

    if (frame->kind != (uint8_t)LB_KIND_COMMAND) {
        lb_writer_t writer;
        lb_writer_init(&writer, log_line, log_size);
        char code_text[8];
        snprintf(code_text, sizeof code_text, "0x%02X", (unsigned)frame->type);
        lb_writer_bytes(&writer, "[fw] 命令分发占位：收到非命令帧（kind=");
        char kind_text[8];
        snprintf(kind_text, sizeof kind_text, "%u", (unsigned)frame->kind);
        lb_writer_bytes(&writer, kind_text);
        lb_writer_bytes(&writer, "，type=");
        lb_writer_bytes(&writer, code_text);
        lb_writer_bytes(&writer, "），忽略");
        lb_writer_finish(&writer);
        return 0;
    }

    if (lb_dispatch_log_line(&decision, log_line, log_size) == 0u) {
        return -1;
    }
    if (reply == NULL) {
        return 0;
    }
    size_t body_len = lb_dispatch_fault_body((char *)reply->payload, sizeof reply->payload, &decision);
    if (body_len == 0u) {
        return -1;
    }
    reply->kind = (uint8_t)LB_KIND_EVENT;
    reply->type = (uint8_t)LB_EVT_FAULT;
    reply->seq = reply_seq;
    reply->payload_len = (uint16_t)body_len;
    return 1;
}
