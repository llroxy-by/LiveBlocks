# 序界（LiveBlocks）ESP32-C3 SuperMini 固件骨架 — 草案

> **状态：草案（draft），`firmware_version` 0.0.1，未冻结。** 本文对应 `docs/ROADMAP.md` 的
> **LB-034**（Issue #34）：上电启动日志、串口心跳、命令分发占位、灯效驱动占位；引脚全部来自
> `config/board.esp32c3-supermini.json`，**代码里没有裸 GPIO 数字**；构建脚本与烧写说明。
>
> ## ★ 证据口径（先读这一段）
>
> 本文敢写「已验证」的只有两类：
>
> 1. **离线静态证据**：板级配置过 schema 校验、固件源码里搜不到裸 GPIO 数字、生成的头文件与
>    配置逐值一致（`tests/test_firmware_board.py`）；
> 2. **真实编译证据**：ESP-IDF v5.3.2 + riscv32-esp-elf-gcc 13.2.0 **从零编译**出
>    `lb_firmware.bin`（命令、产物大小与 sha256 见 §3，同一条记录抄在 `docs/PROGRESS.md`）；
> 3. **模拟器证据**：心跳帧与 LB-033 的 pty 设备模拟器**逐字节一致**
>    （`tests/test_firmware_parity.py`）。这是**模拟器证据，不是硬件证据**。
>
> **烧写、串口日志、复位自恢复一律是「待人工验证」**（§7），代理没有板子，不得代填，也不得
> 把这三条写成通过。本文件里没有任何一条硬件实测结论。

## 1. 范围与不做

**做了**（本单范围）：

- 最小固件的**骨架**：`firmware/` 下的 ESP-IDF 工程（`app_main` 起两个任务）；
- 上电启动日志：板子型号、配置来源、引脚、串口参数、心跳周期、命令表、灯效占位状态；
- 串口心跳：周期性上行 `HEARTBEAT`（事件 `0x87`）帧，帧格式与 LB-033 的模拟器逐字节一致；
- 命令分发**占位**：认出 5 条命令的编号与名字，打一行日志，回一条 `LB-H006` FAULT
  （明说「本骨架未实现业务」，而不是静默丢弃）；
- 灯效驱动**占位**：只记录配置里的引脚与期望灯效，**不驱动任何灯**；
- 全部引脚 / 串口参数 / 心跳周期来自 `config/board.esp32c3-supermini.json`，默认值一律标
  「待实测」；C 源码里**零硬编码 GPIO 数字**（`LB-F005` 扫描保证）；
- 构建脚本 `scripts/build_firmware.sh`（**只编译、不烧写**）与本文的烧写说明。

**不做**（与 Issue #34 的「不做」一致）：

- 不做真实烧写与联调（属 LB-020，人来做）；
- 不做 OTA；
- 不做外壳与供电设计；
- 不支持 ESP32-C3 以外的板子；
- 不实现 `STEP` 的业务语义（不驱动任何执行器、不维护世界状态）；
- 不实现真实灯效驱动（WS2812 / RMT 时序一律没写）；
- 不改 `docs/ROADMAP.md` 的范围与优先级，不改仓库 / CI / superagent 设置。

## 2. 板级配置与「零硬编码 GPIO」

引脚只有一个来源：

```bash
python -m liveblocks firmware check     # 校验配置（与 config/board.schema.json 同口径）+ 列待实测项
python -m liveblocks firmware pins      # 打印引脚表（含「待实测」标记与说明）
```

`config/board.esp32c3-supermini.json` 的结构（`schema_version` 0.0.1，schema 见
`config/board.schema.json`）：

| 段 | 内容 |
| --- | --- |
| `board_id` / `board_name` / `mcu` | 固定 `esp32c3-supermini` / `ESP32-C3 SuperMini` / `esp32c3` |
| `config_status` | 整份配置的验证状态（未核对完就必须含「待实测」） |
| `pins` | 命名引脚表：`status_led`、`led_strip_data`、`user_button`（每个含 `label` / `gpio` / `active_low` / `verified` / `note`） |
| `serial` | `uart` / `tx_gpio` / `rx_gpio` / `baud` / `verified` / `note` |
| `firmware` | `heartbeat_ms` / `heartbeat_ms_verified` / `heartbeat_note` / `led_count` / `led_driver` |

**「待实测」的硬规则**（`src/liveblocks/firmware/board.py` 的校验器，零依赖；装得上官方
`jsonschema` 时测试会拿它当独立实现交叉核对）：

- `verified=false` 的项，`note` 里必须出现「待实测」；
- 只要还有一项 `verified=false`，顶层 `config_status` 就必须含「待实测」；
- 反过来，`verified=true` 必须给出 `verified_by`（Issue 链接 / 测量方式 / 日期）——
  不许「一句话就宣布核对过了」。

**引脚是怎么进 C 代码的**：构建时先跑生成器，把配置写成头文件，C 代码只认宏：

```bash
python -m liveblocks firmware headers --config config/board.esp32c3-supermini.json --out firmware/generated
```

生成两个头文件，**都不入库**（`.gitignore` 里挡住；见 `firmware/generated/README.md`）：

| 生成物 | 来源（唯一权威） | 内容 |
| --- | --- | --- |
| `firmware/generated/lb_board_config.h` | `config/board.esp32c3-supermini.json` | 板子标识、引脚数组、串口参数、心跳周期、「待实测」说明 |
| `firmware/generated/lb_protocol.h` | `src/liveblocks/hw/{frames,protocol}.py` | MAGIC / 线格式版本 / 命令与事件号 / `LB-H0xx` 码 / 命令表 |

**「零硬编码 GPIO」怎么证明**：

```bash
python -m liveblocks firmware scan      # 零违规才返回 0
```

扫描规则（`LB-F005`，实现在 `src/liveblocks/firmware/board.py::bare_gpio_violations`）：

1. `firmware/` 下入库的 `.c` / `.h` 里不许出现 `GPIO<数字>` / `GPIO_NUM_<数字>`；
2. 说引脚的行（含 `gpio` 字样）里，不许出现除 `0` / `1`（电平与布尔）之外的数字字面量；
   贴着标识符或连字符的数字不算（`LB-034`、`UART0`、`%02X` 不是引脚号，报了只会让人学会忽略告警）。

`tests/test_firmware_board.py` 对这个扫描做了正反两向验证：注入 `gpio_set_level(8, 1)` 必须报，
`tx=GPIO%d` 这种格式化字符串不许报。

## 3. 构建：只编译、不烧写

前置：ESP-IDF v5.x（实测 v5.3.2）+ `cmake` + `ninja` + Python 3.11+。
本仓库的运行环境实测为：Ubuntu、riscv32-esp-elf-gcc 13.2.0（ESP-IDF 自带）、cmake 4.4.5、ninja 1.13.2。

一条命令（**只编译**）：

```bash
bash scripts/build_firmware.sh            # 加 --clean 先清构建目录（从零编译）
```

脚本做的事（`docs/FIRMWARE.md` 这一节的机器可核对版本是脚本自己）：

1. `python -m liveblocks firmware headers` 生成两个头文件；
2. 找 ESP-IDF（`$LB_IDF_PATH` → `$IDF_PATH` → `~/esp/idf`），`source export.sh`；
3. `idf.py set-target esp32c3`（只在没有 `sdkconfig` 时）；
4. `idf.py build`，最后打印产物路径、大小与 sha256。

脚本**不含也拒绝**任何烧写动作：传 `flash` / `monitor` / `erase-flash` 会直接退出码 2 并提示
「本脚本只编译、不烧写」。`tests/test_firmware_build.py` 会核对这一点（并真跑一次拒绝路径）。

**实测编译记录（本机真跑，命令与产物原样抄自 `docs/PROGRESS.md`）**：

```
bash scripts/build_firmware.sh --clean
ESP-IDF v5.3.2；riscv32-esp-elf-gcc (esp-13.2.0_20240530)；目标 esp32c3
Project build complete（950 个构建目标从零编，编译器告警 0 条）
产物：firmware/build/lb_firmware.bin（215264 字节，sha256 1ceba447ebeb059df37755a6908612c59a5b52c32d1ce536dc727b46e55928b9）
```

**这个 sha256 只对应那一次构建**：ESP-IDF 会把构建时间等写进 app 描述符，同样的源码重编
（本单真的重编过一次）哈希就变了、大小仍是 215264 字节。所以它证明的是「这一份源码编出了这个
文件」，**不是**「构建可复现」——字节级可复现不在本单验收里，也没声称过。

**CI 现状（如实说）**：本单的授权范围**排除 CI 配置变更**，所以 `.github/workflows/ci.yml`
一个字节都没改，固件编译目前**不在 CI 里跑**——上面那份证据是本机真跑的结果。
脚本本身是「只编译、不烧写」的，接进 CI 只需要加一个跑 `bash scripts/build_firmware.sh` 的任务
（是否接、用哪个镜像，需人授权，见 §9 的待办）。

## 4. 帧格式与串口心跳

帧格式完全沿用 LB-033 定的线格式（`docs/HW.md` §2）：帧头 9 B（`4C 42` + VER + KIND + TYPE +
SEQ(2) + LEN(2)）+ JSON 载荷（≤512 B，键排序、无空格）+ CRC-16/CCITT-FALSE（2 B，覆盖
VER..PAYLOAD）。C 侧没有手抄这些常量，全部来自生成的 `lb_protocol.h`。

心跳是事件 `0x87`，载荷 `{"steps":N,"uptime_ms":X}`（键排序后 `steps` 在前）。
例帧（seq=1, steps=3, uptime_ms=12.5）：

```
4c 42 01 02 87 01 00 1c 00 7b 22 73 74 65 70 73 22 3a 33 2c 22 75 70 74 69 6d 65 5f 6d 73 22 3a 31 32 2e 35 7d ed 37
```

拆开看：`4c 42`=`LB`、`01`=线格式版本、`02`=事件、`87`=`HEARTBEAT`、`01 00`=序号 1、
`1c 00`=载荷 28 字节、载荷是 `{"steps":3,"uptime_ms":12.5}`、末两字节 `ed 37`=CRC（小端 `0x37ED`）。
这串字节由固件编码器真算出来（`firmware/tools/lb_host_tool.c heartbeat`），
`tests/test_firmware_docs.py` 会把文档里的这一行和固件编码器的输出逐字节比对。

细节：

- **周期**来自配置 `firmware.heartbeat_ms`（当前默认 1000 ms，标「待实测」）；真实周期的抖动
  属人工实测（`docs/HW.md` §8 HW-5）。
- **`uptime_ms`** 是上电以来的毫秒数（`esp_timer_get_time()/1000.0`），文本形状对齐 Python 的
  `round(x, 3)` + `repr()`：`12.5`、`60000.0`、`0.001` 都按这个形状输出。
- **`steps`** 恒为 0：骨架不执行 `STEP`，心跳里如实报 0，不编造步数。
- 模拟器默认不发心跳（`heartbeat_ms=None`），本固件默认发；两边**帧格式**一致，节奏各自配置。

## 5. 烧写与串口日志（人来做）

> 下面每一步都是**人工步骤**。代理没有板子，也没有执行过其中任何一条——这是「待人工验证」。

```bash
# 0) 装好 ESP-IDF 并进入环境（与构建相同）
. <你的 ESP-IDF>/export.sh

# 1) 编译（只编译）
bash scripts/build_firmware.sh

# 2) 烧写 + 开串口监视器（★ 这一步是人做的，代理不做）
cd firmware
idf.py -p /dev/ttyACM0 -b 460800 flash monitor      # USB-CDC 口一般是 /dev/ttyACM0
# 用 USB-TTL 接 UART0 时口名多为 /dev/ttyUSB0
```

不想烧整个工程时（只烧应用分区）：

```bash
cd firmware
idf.py -p /dev/ttyACM0 app-flash monitor
```

退出监视器：`Ctrl-]`。

**串口上应当看到什么**（本骨架的确定性启动日志，来自 `firmware/main/lb_boot.c`）：

```
[fw] 序界 LiveBlocks 固件骨架 fw=0.0.1（lb-034-skeleton）
[fw] 协议：串口帧 4C 42 / 线格式 1 / 载荷上限 512 B / 心跳事件 0x87
[fw] 板子：ESP32-C3 SuperMini（esp32c3）board_id=esp32c3-supermini
[fw] 配置状态：待实测：本文件里的引脚、串口与心跳参数都是设计默认值，没有在任何实物上核对过
[fw] 引脚（来自 config/board.esp32c3-supermini.json）：led_strip_data=GPIO3(待实测)、status_led=GPIO8(低有效/待实测)、user_button=GPIO9(低有效/待实测)
[fw] 串口：UART0 tx=GPIO21 rx=GPIO20 @115200 bps（待实测）
[fw] 心跳：每 1000 ms 上行一帧 HEARTBEAT（周期待实测）
[fw] 命令分发占位已就绪（业务未实现）：HELLO(0x01) BEGIN(0x02) STEP(0x03) END(0x04) PING(0x05)
[fw] 灯效驱动占位已就绪：led_count=1 driver=placeholder（未驱动任何灯）
[fw] 串口已就绪：收帧 → 命令分发占位；心跳任务已启动
[fw] 固件骨架启动完成（0.0.1 / lb-034-skeleton）
```

心跳帧是二进制，监视器里会显示成乱码——这是正常的：用主机侧的解析器看才干净
（`python -m liveblocks hw` 这类入口目前只接 pty 模拟器，接真实串口属 LB-020）。

## 6. 命令分发占位与灯效占位

**命令分发**（`firmware/main/lb_dispatch.c`）：认出 `HELLO` / `BEGIN` / `STEP` / `END` / `PING`
五条命令（编号与名字来自生成的 `lb_protocol.h`，与 `hw.protocol.COMMANDS` 逐条一致），
每条都：

1. 打一行确定性日志：`[fw] 命令分发占位：收到命令 HELLO(0x01)，本骨架未实现业务，回 FAULT LB-H006`；
2. 回一条 `FAULT`（事件 `0x88`，载荷 `{"code":"LB-H006","message":"命令分发占位：HELLO 未实现业务"}`）。

为什么「占位也回 FAULT」：主机侧有判据，静默丢弃看起来和丢帧一样。回 `LB-H006` 能让人一眼区分
「帧到了、CRC 对、命令号认识，但业务没实现」与「帧根本没到」。未知命令号回
`LB-H006`+`未知命令 0xNN`；收到非命令帧只记日志、不回帧。

坏帧处理（`firmware/main/lb_frame.c` 的解析器）与 LB-033 §2.4 同口径：截断等更多字节、坏 MAGIC
逐字节重同步、CRC 错或长度非法丢整帧候选并记一条错误。

**灯效占位**（`firmware/main/lb_led.c`）：不调用任何 GPIO / RMT / LED 接口，只把
「driver=placeholder、led_count、status_led 与 strip_data 的 GPIO」打成日志，并明说
「未驱动任何灯」。真实灯效驱动不在本单范围。

## 7. 待人工验证

以下每一条**都没有**实测证据，代理不得把它们迁到「已验证」。人按
`.github/ISSUE_TEMPLATE/hardware-test.md` 开 Issue，逐条验证：

| # | 待验证内容 | 怎么验 | 通过标准 |
| --- | --- | --- | --- |
| F-1 | **烧写**能不能成功 | 接 ESP32-C3 SuperMini，`bash scripts/build_firmware.sh` 后 `cd firmware && idf.py -p /dev/ttyACM0 flash` | 烧写过程无报错、退出码 0；`idf.py -p <口> monitor` 能看到 §5 的启动日志 |
| F-2 | **串口日志**与心跳是否真在跑 | 烧写后开监视器（115200 / 460800 与烧写一致），等 ≥10 s | 能看到完整启动日志；每 ≈`heartbeat_ms` 出现一帧 `0x87` 心跳（按 §4 的帧格式解出 `steps` 与 `uptime_ms`） |
| F-3 | **复位自恢复** | 跑到一半按 EN（复位）或断电重上电 | 复位后重新打印启动日志、心跳从新链路序号 1 重新开始、能够重新收命令；把现象（含是否保留状态）如实记进 Issue |
| F-4 | 引脚是否真是这几个（`status_led` / `user_button` / UART 焊盘） | 按 §2 的表逐项：用万用表 / 示波器 / 点灯确认 GPIO 编号与极性 | 与 `config/board.esp32c3-supermini.json` 一致；**不一致就必须改配置并写 `verified_by`**，不许直接改 C 代码 |
| F-5 | 心跳周期与抖动（对应 `docs/HW.md` §8 HW-5） | 串口抓 ≥100 帧心跳，算间隔均值 / 抖动 | 给出实测均值与抖动，回填 `docs/HW.md` HW-5 与配置里的 `heartbeat_ms_verified` |

F-1 / F-2 / F-3 就是 Issue #34 的「烧写、串口日志、复位自恢复由人实测」，
它们的结论在拿到实测记录前一律停在「待人工验证」。

## 8. 已知限制

1. **日志与协议帧共用 UART0**：`printf` 的启动日志与帧走同一个口，主机侧靠 `LB` MAGIC 逐字节
   重同步（测试里覆盖了「日志文本混进去」的情形），但日志片段会计入坏帧/丢字节统计。真机上
   把日志改到 USB-JTAG 控制台或第二个串口是后续任务。
2. **不重连、不持久化**：串口读失败只打日志并等待人工复位；复位后世界状态一概不保留
   （本骨架本来也没有世界状态）。
3. **`STEP` 没实现**：`steps` 恒 0，`BEGIN` / `STEP` / `END` 只作为占位被识别。
4. **灯效没驱动**：只有日志，没有任何 GPIO / RMT 输出；`firmware.led_driver` 只允许
   `placeholder`，写成别的值配置校验会报 `LB-F002`。
5. **浮点毫秒的对齐边界**：`uptime_ms` 按 `round(x,3)` + `repr()` 形状输出，因此
   `1e16` 以上或 `1e-4` 以下的值不会与 Python 的指数写法对得上（运行时不可能出现）；
   这是「逐字节一致」验收的已知边界，记在这里以免误用。
6. **只支持一块板子**：`board_id` 与 `mcu` 被校验器钉死为 `esp32c3-supermini` / `esp32c3`。
7. **生成的头文件不入库**：任何直接 `idf.py build` 的尝试都会因为缺 `lb_protocol.h` 而失败，
   并提示用 `scripts/build_firmware.sh`（这是有意的：引脚与协议常量只能有一个来源）。
8. **固件编译还没进 CI**：本单排除 CI 配置变更，编译证据来自本机真跑（§3）。
9. **C 侧的重同步比 Python 侧保守**：帧头字段（VER / KIND / TYPE）不认识时，`lb_frame.c`
   只丢 1 字节继续找 MAGIC，而 `hw/frames.py` 按帧头里的 LEN 丢整帧候选。
   原因是同一个 UART 上还跑着日志文本：日志里出现 `LB-H006` 这种字样时，假 MAGIC 后面的 LEN
   完全不可信，按它丢整帧候选会把后面真正的帧一起吃掉（写测试时真踩到过）。
   代价是多扫几个字节；CRC 错 / 长度非法仍然丢整帧候选（两类口径见 `docs/HW.md` §2.4）。
10. **日志前缀是 `[fw]` 而不是 `[LB]`**：`[LB]` 里的 ASCII `LB` 正好是帧 MAGIC，
    日志与帧共用 UART0 时会制造假帧头。测试里既覆盖了「日志里带 `LB-H006` 时不吞帧」，
    也断言启动日志每一行都以 `[fw]` 开头。

## 9. 错误码与证据索引

| 码 | 标题 | 含义 |
| --- | --- | --- |
| `LB-F001` | 板级配置缺字段 | 顶层 / `pins.*` / `serial` / `firmware` 少必填字段 |
| `LB-F002` | 板级配置字段取值不合法 | 类型、枚举、范围不对（含 `board_id` / `mcu` / `led_driver` 只允许固定值） |
| `LB-F003` | 默认值未标「待实测」，或声称已验证却没有核对记录 | `verified=false` 却没说「待实测」，或 `verified=true` 却没有 `verified_by` |
| `LB-F004` | 引脚冲突或越界 | 两个角色撞同一个 GPIO、GPIO 超出 0..48、TX 与 RX 相同 |
| `LB-F005` | 固件源码里出现裸 GPIO 数字 | `firmware/` 的 `.c` / `.h` 里出现 `GPIO8` / `gpio = 21` 之类 |

| 内容 | 位置 |
| --- | --- |
| 板级配置（引脚唯一来源） | `config/board.esp32c3-supermini.json` |
| 板级配置 schema | `config/board.schema.json` |
| 配置校验 / 头文件生成 / 裸 GPIO 扫描 | `src/liveblocks/firmware/{board,headers,hosttool,docs}.py` |
| 固件入口与任务 | `firmware/main/main.c` |
| 帧编解码 / CRC / 重同步 | `firmware/main/lb_frame.c` |
| 心跳帧构造 | `firmware/main/lb_heartbeat.c` |
| 载荷规范编码（键排序 / 浮点毫秒） | `firmware/main/lb_json.c` |
| 命令分发占位 | `firmware/main/lb_dispatch.c` |
| 灯效驱动占位 | `firmware/main/lb_led.c` |
| 启动日志 | `firmware/main/lb_boot.c` |
| 板级配置的 C 视图 | `firmware/main/lb_board.c` |
| 串口胶水（仅 ESP-IDF） | `firmware/main/lb_uart.c` |
| 主机侧驱动同一份源码的小工具 | `firmware/tools/lb_host_tool.c` |
| 构建脚本（只编译、不烧写） | `scripts/build_firmware.sh` |
| 单测 | `tests/test_firmware_{board,frames,parity,build,docs}.py` |

常用命令（都不烧写、不碰硬件）：

```bash
python -m liveblocks firmware check      # 配置校验 + 待实测项
python -m liveblocks firmware pins       # 引脚表
python -m liveblocks firmware headers    # 生成头文件（不入库）
python -m liveblocks firmware scan       # 裸 GPIO 数字扫描
bash scripts/build_firmware.sh           # 只编译（产物在 firmware/build/）
bash scripts/check.sh                    # 全量检查（含本单的测试）
```

**待办（需要人授权 / 实测）**：① 是否把固件编译接入 CI（本单排除 CI 配置变更）；
② F-1…F-5 的人工实测结论回填本文件与 `docs/PROGRESS.md`；
③ 心跳周期与抖动定值后回填 `docs/HW.md` HW-5 与配置的 `heartbeat_ms_verified`；
④ 把真实串口入口接进 `liveblocks hw`（属 LB-020）。
