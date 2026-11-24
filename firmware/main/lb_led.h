/* 灯效驱动占位的声明。 */
#ifndef LB_LED_H
#define LB_LED_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** 占位实现能表达的三种灯效请求（真实驱动属后续任务）。 */
typedef enum {
    LB_LED_PATTERN_IDLE = 0,
    LB_LED_PATTERN_RUNNING,
    LB_LED_PATTERN_ERROR
} lb_led_pattern_t;

const char *lb_led_pattern_name(lb_led_pattern_t pattern);

/** 初始化日志：打印配置里取到的引脚并明说「未驱动」。 */
size_t lb_led_init_log(char *out, size_t out_size);

/** 设置灯效的日志：只记录请求，不产生任何实际输出。 */
size_t lb_led_set_pattern_log(lb_led_pattern_t pattern, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif

#endif /* LB_LED_H */
