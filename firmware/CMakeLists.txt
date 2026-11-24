# LB-034 固件骨架（ESP32-C3 SuperMini）的 ESP-IDF 工程定义。
#
# 构建：bash scripts/build_firmware.sh（**只编译、不烧写**）
# 烧写：人来做，步骤见 docs/FIRMWARE.md §5。
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

project(lb_firmware)
