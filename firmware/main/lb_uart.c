/* 串口胶水的 ESP-IDF 实现。 */
#include "lb_uart.h"

#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "lb_frame.h"

#define LB_UART_RX_BUFFER 1024
#define LB_UART_TX_BUFFER 1024

static SemaphoreHandle_t s_tx_lock;
static uart_port_t s_port;
static uint16_t s_out_seq = 1;

int lb_uart_init(const lb_serial_cfg_t *serial)
{
    if (serial == NULL) {
        return -1;
    }
    s_port = (uart_port_t)serial->uart;
    uart_config_t config = {
        .baud_rate = serial->baud,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    if (uart_driver_install(s_port, LB_UART_RX_BUFFER, LB_UART_TX_BUFFER, 0, NULL, 0) != ESP_OK) {
        return -1;
    }
    if (uart_param_config(s_port, &config) != ESP_OK) {
        return -1;
    }
    if (uart_set_pin(s_port, serial->tx_gpio, serial->rx_gpio, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) !=
        ESP_OK) {
        return -1;
    }
    s_tx_lock = xSemaphoreCreateMutex();
    if (s_tx_lock == NULL) {
        return -1;
    }
    s_out_seq = 1;
    return 0;
}

int lb_uart_read(uint8_t *data, size_t max_len, unsigned timeout_ms)
{
    int got = uart_read_bytes(s_port, data, (uint32_t)max_len, pdMS_TO_TICKS(timeout_ms));
    if (got < 0) {
        return -1;
    }
    return got;
}

int lb_uart_send_frame(lb_frame_t *frame)
{
    if (frame == NULL || s_tx_lock == NULL) {
        return 0;
    }
    if (xSemaphoreTake(s_tx_lock, portMAX_DELAY) != pdTRUE) {
        return 0;
    }
    /* 序号与写出在同一个锁里完成：这样「取号顺序」就是「上线顺序」，
     * 主机侧的序号校验不会因为两个任务抢号而报 LB-H005。 */
    frame->seq = s_out_seq;
    uint8_t buffer[LB_MAX_FRAME_SIZE];
    size_t length = lb_frame_encode(frame, buffer, sizeof buffer);
    int written = 0;
    if (length > 0u) {
        written = uart_write_bytes(s_port, (const char *)buffer, length);
        if (written == (int)length) {
            s_out_seq = lb_seq_next(s_out_seq);
        } else {
            written = 0;
        }
    }
    xSemaphoreGive(s_tx_lock);
    return written;
}

void lb_uart_log(const char *text)
{
    if (text == NULL) {
        return;
    }
    printf("%s", text);
    if (text[0] != '\0' && text[strlen(text) - 1u] != '\n') {
        printf("\n");
    }
    fflush(stdout);
}
