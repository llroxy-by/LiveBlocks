# 序界（LiveBlocks）硬件后端与串口协议 — 草案

> **状态：草案（draft），`hw_protocol_version` 0.0.1，未冻结。**
> 本文件描述 LB-033（LB-013 的协议侧）定下的东西：串口帧格式、命令与事件表、传输抽象、
> 错误事件模型，以及 `backend:"hw"` 到 §7.1 事件流的映射。
> 改帧格式 / 命令表 / 码表必须提升版本号，并在 `docs/DECISIONS.md` 记一条（见 D-024）。
>
> ## ★ 证据口径（先读这一段）
>
> **本文件里所有「跑通了」的结论，证据都来自 pty 上的纯 Python 设备模拟器，属于模拟器证据，
> 不是硬件证据。** `backend:"hw"` 只表示「这次运行走的是串口协议这条代码路径」，
> **不表示**真的有一块 ESP32-C3 SuperMini 在跑，也**不表示**灯效、屏幕被驱动过。
>
> 真实串口、真实设备的联调属 **LB-020**，本阶段一律记为「待人工验证」（见 §8）。
> 任何把模拟器输出当成真机结论的说法都是错的。

## 1. 范围与不做

**做了**：

- `backend:"hw"` 的事件流映射：与 sim 完全同一份 §7.1 形状，只把 `backend` 换成 `"hw"`；
- 串口帧格式：帧头、长度、CRC、序号、命令与事件类型；
- 传输抽象 `Transport`，以及 `PtyTransport`（pty 从端）、`SerialTransport`（真实串口）、
  `MemoryTransport`（进程内，供单测）三种实现；
- 断连、超时、重连、坏帧、序号乱序、设备故障的错误事件模型（`LB-H0xx`）；
- 一个纯 Python 的**设备模拟器**：占住 pty 主端，按本文件的协议跑完一次运行。

**不做**（与 Issue #31 一致）：

- 不做真实硬件联调与 bring-up（属 LB-020）；
- 不支持 ESP32-C3 以外的板子（设备自报 `board` 固定 `esp32c3-supermini`）；
- 不声称灯效或屏幕已驱动（固件里的灯效/屏幕是 LB-034 的占位）；
- 不烧写固件、不改 `docs/ROADMAP.md` 的范围与优先级。

## 2. 串口帧格式

一帧 = **帧头（9 字节）+ 载荷（≤ 512 字节）+ CRC（2 字节）**，全部小端。

| 字段 | 长度 | 说明 |
| --- | --- | --- |
| `MAGIC` | 2 B | `4C 42`（ASCII `LB`）——重同步锚点 |
| `VER` | 1 B | 线格式版本，当前 `0x01`（对应本文件的 0.0.1） |
| `KIND` | 1 B | `1` = 命令（主机→设备），`2` = 事件（设备→主机） |
| `TYPE` | 1 B | 命令 / 事件编号；命令 < 0x80，事件 ≥ 0x80 |
| `SEQ` | 2 B | 发送方在**本次链路**内的帧序号，从 1 开始、每帧 +1、`0xFFFF` 后回到 1 |
| `LEN` | 2 B | 载荷字节数，上限 512 |
| `PAYLOAD` | LEN B | UTF-8 JSON 对象（键排序、无空格的**规范写法**） |
| `CRC` | 2 B | CRC-16/CCITT-FALSE 校验值 |

### 2.1 CRC

- 算法：**CRC-16/CCITT-FALSE**，多项式 `0x1021`，初值 `0xFFFF`，不反射，xorout 0；
- 覆盖范围：`VER` 到 `PAYLOAD` 最后一个字节（**不含** `MAGIC`）；
- 标准校验值：`crc16_ccitt(b"123456789") == 0x29B1`（`liveblocks hw frames` 会打印这一行自检）；
- 现有仓库里 `crc16_ccitt` 只有一份实现（`src/liveblocks/hw/frames.py`），固件侧必须与它逐位一致。

### 2.2 载荷编码

载荷是 JSON 对象，按「键排序 + 紧凑分隔符 + 非 ASCII 原样」编码（`encode_body`）。
这条不是洁癖：**只有键排序才能让 C 与 Python 两端对同一对象编码出同样的字节**，
LB-034 的「心跳帧与模拟器逐字节一致」验收依赖它。

### 2.3 序号

- 每个方向各一条序号线，从 1 开始严格 +1（`0xFFFF` 之后回到 1，`0` 不用）；
- 收到「不是上一帧 + 1」的帧（跳号或重号）就是 **`LB-H005` 序号乱序**，按 §6 处理；
- **重连后重新握手，两端序号都回到 1**：新链路重新计数，避免把断线前的序号带进来。

### 2.4 坏帧与重同步

| 情形 | 解析器动作 | 上报 |
| --- | --- | --- |
| 数据不够一帧（**截断**） | 留在缓冲里等更多字节，不丢数据 | 不报错 |
| `MAGIC` 不对 | 丢 1 字节继续找 `LB` | `LB-H004`（坏帧） |
| `VER` / `KIND` / `TYPE` 不认识 | 丢整帧候选 | `LB-H004` |
| `LEN` 超过 512 | 丢整帧候选 | `LB-H004` |
| CRC 不符 | 丢整帧候选 | `LB-H004` |

### 2.5 示例帧

`EVT PONG seq=1 {"nonce":7,"uptime_ms":12.5}` 的十六进制（`liveblocks hw frames --hexdump`）：

```
4c 42 01 02 86 01 00 1c 00 7b 22 6e 6f 6e 63 65 22 3a 37 2c 22 75 70 74 69 6d 65 5f 6d 73 22 3a 31 32 2e 35 7d 64 ec
```

拆开看：`4c 42` = `LB`；`01` = 线格式版本；`02` = 事件；`86` = `PONG`；`01 00` = 序号 1；
`1c 00` = 载荷 28 字节；中间 28 字节是 JSON；最后 `64 ec` 是 CRC（小端 `0xEC64`）。

## 3. 命令表（主机 → 设备，`KIND=1`）

| 编号 | 名字 | 载荷必填字段 | 期望回应 | 说明 |
| --- | --- | --- | --- | --- |
| `0x01` | `HELLO` | `client` | `HELLO` | 握手：报客户端名，设备回型号 / 固件 / 协议版本；把本次链路序号重置为 1 |
| `0x02` | `BEGIN` | `run_id` `steps` `limits` `clock_ms_per_tick` | `BEGIN_ACK` | 开一次运行：设备清零世界状态、记下 limits 与步数 |
| `0x03` | `STEP` | `step_id` `op` `args` `cost_ticks` | `STEP_OK` / `STEP_ERROR` | 执行一步；**同一 `step_id` 幂等**（重发只回放上次结果） |
| `0x04` | `END` | `status` | `END_ACK` | 收尾：设备回报本次帧计数与步数 |
| `0x05` | `PING` | `nonce` | `PONG` | 链路活性探针（心跳的上行方向） |

## 4. 事件表（设备 → 主机，`KIND=2`）

| 编号 | 名字 | 载荷必填字段 | 说明 |
| --- | --- | --- | --- |
| `0x81` | `HELLO` | `board` `firmware` `protocol_version` | 握手回应；`board` 固定 `esp32c3-supermini` |
| `0x82` | `BEGIN_ACK` | `run_id` `steps` | 运行已开始（附清零后的世界状态） |
| `0x83` | `STEP_OK` | `step_id` `state_delta` `world` | 一步完成；`state_delta` 与 §7.1 `finish.detail.state_delta` **同形** |
| `0x84` | `STEP_ERROR` | `step_id` `code` `message` | 一步被设备拒绝（如手上没有物品却 `drop`），`code` 用 `LB-R0xx` |
| `0x85` | `END_ACK` | `status` | 收尾确认，附本次链路帧计数 |
| `0x86` | `PONG` | `nonce` | `PING` 的回应，附设备运行时长 |
| `0x87` | `HEARTBEAT` | `uptime_ms` `steps` | 设备主动上行心跳（模拟器默认关闭；周期待 LB-034 实测定） |
| `0x88` | `FAULT` | `code` `message` | 设备侧故障（未知命令 / 非命令帧 / 序号错等），`code` 用 `LB-H0xx` |

主机**必须容忍**未预期的 `HEARTBEAT`（忽略并计数），这是「设备可以主动说话」的唯一形式；
其它未预期事件按设备故障处理（`LB-H006`）。

## 5. 一次运行的时序与「谁判定什么」

```
主机                                    设备（模拟器 / 以后的固件）
  |-- HELLO ------------------------------>|
  |<-------------------------------- HELLO --|   握手，序号重置为 1
  |-- BEGIN(run_id, steps, limits) ------->|
  |<--------------------------- BEGIN_ACK --|   设备清零世界状态
  |-- STEP(s0, op, args) ----------------->|
  |<--------------------------- STEP_OK ----|   state_delta + world
  |         ...每一步重复...                 |
  |-- END(status) ------------------------>|
  |<---------------------------- END_ACK ---|
```

**判定在主机的三件事**（与 `docs/EXEC.md` §1 逐条相同，顺序也一样：中断 → 步数 → 语义 → 超时）：

1. 中断（步骤边界，`LB-R004`）；
2. `limits.max_steps`（`LB-R002`）；
3. 语义表里有没有这个 `op`（`LB-R001`）；
4. 下一步的虚拟耗时会不会超过 `limits.timeout_ms`（`LB-R003`）。

**执行在设备**：设备拿 `STEP` 跑 `apply_step`（与 sim 共用同一张语义表与同一个世界模型），
回报 `state_delta` / `world`；P0 语义检查失败（如 `LB-R005` 手上没有物品却 `drop`）由设备在
`STEP_ERROR` 里回，主机把它写成一条 `error` 事件（文案与 sim 逐字相同）。

这样分工的原因：`limits` 是 IR 的硬约束、时钟是虚拟的，主机说了算；而「一步到底把世界改成什么样」
是设备的事——主机不维护第二份世界状态（`ExecState.world` 直接来自设备的 `world` 回报）。

**虚拟时钟**：`at_ms` 与 sim 同一套换算（1 tick = 10 ms，见 `docs/EXEC.md` §2.1），
与真实串口耗时无关；`HwRun.wall_ms` 才是实测墙钟（只给人看，不进事件流）。

## 6. 错误事件模型（`LB-H0xx`）

链路类问题一律走 §7.1 的 `error` 事件 + `ExecResult.diagnostics`，不新造第二种上报通道。

| 码 | 标题 | 严重度 | 含义 |
| --- | --- | --- | --- |
| `LB-H001` | 串口断连 | error | 链路上读不到数据（EOF / `EIO`，模拟器拔线即此情形）；发 `error` 事件后尝试重连，重连不成则 `status=runtime_error` |
| `LB-H002` | 设备响应超时 | error | 设备在 `response_timeout_ms` 内没有回应（或握手超时）；`status=runtime_error` |
| `LB-H003` | 链路已恢复 | info | 断连后重连成功、握手完成；这是一条 `log` 事件，运行继续 |
| `LB-H004` | 坏帧 | error | CRC 不符、长度非法、帧头 / 版本 / 类型不认识；解析器丢帧后继续，调用方决定是否终止 |
| `LB-H005` | 序号乱序 | error | 对端序号不是「上一帧 + 1」（含重复帧）；`status=runtime_error` |
| `LB-H006` | 设备故障 | error | 设备回 `FAULT`，或回复与本条命令对不上（如收到无关事件）；`status=runtime_error` |

### 6.1 恢复语义

- **断连**（`LB-H001`）时先发一条 `error` 事件（`detail.recovered` 记录是否有重连机会），
  再按 `reconnect_attempts` 次数重连；重连成功后发一条 `LB-H003` 的 `log` 事件，
  然后**重发同一条 `step_id`**（设备幂等，不会重复作用）。
- 重连成功后运行继续，`ExecResult.status` 仍是 `ok`，但诊断里保留 `LB-H001`——
  「出过断连、已恢复」是事实，不隐藏。
- 重连失败 / 没有重连机会：`status=runtime_error`，诊断 `LB-H001`（+ 失败原因在事件 `detail.reason`）。
- 设备**保留内部状态**：模拟器的「拔线」只是关掉链路，世界状态还在（相当于板子还在跑，线掉了）；
  插回后再握手即可接着跑。真机复位后状态是否保留 = **待人工验证**（§8）。

### 6.2 不阻塞

- 每次读都有超时（`select` + 截止时间），等待回应有 `response_timeout_ms`；
- 拔线 → 读立刻返回 `EIO`/EOF，不会挂住；`liveblocks hw unplug-test` 实测整次运行在**毫秒级**结束
  （测试与 PR 证据里有具体数字）；
- 因此「设备掉线」不会让主机卡死：要么报 `LB-H001` 后重连，要么报 `LB-H001` 后结束。

## 7. `backend:"hw"` 与 §7.1 的映射，以及双后端一致性

字段与 sim 完全同形（**不加字段**）：`schema_version` / `kind` / `run_id` / `seq` / `at_ms` /
`step_id` / `phase` / `backend` / `detail`，其中 `backend` 固定 `"hw"`。

`detail` 的取值（与 sim 同一套）：

- `log`：`{message, steps, limits, backend, clock_ms_per_tick}`；
- `start`：`{op, args, cost_ticks, cost_ms}`；
- `finish`：`{state_delta, cost_ticks, cost_ms}`（`state_delta` 来自设备的 `STEP_OK`）；
- `error`：`{code, severity, message, …}`（`LB-R0xx` 与 sim 同形；`LB-H0xx` 另带
  `link` / `reason` / `recovered` 等定位字段）。

### 7.1 一致性（parity）口径

同一份 `Ir` 分别用 `sim` 与 `hw` 执行，比较（`liveblocks.hw.parity.compare_runs`，
验收测试 `tests/test_backend_parity.py`）：

| 比什么 | 怎么比 |
| --- | --- |
| 事件条数 / 顺序 | 数量相等，`seq` 都是 1..n |
| `phase` / `step_id` / `at_ms` | 逐条相等 |
| `detail` | 逐条相等；唯一例外是起步 `log` 的 `detail.backend`（sim / hw 各自的名字） |
| `ExecResult` | `status` / `steps_executed` / `duration_ms` / 诊断码逐项相等 |
| `ExecState` | `status` / `steps_executed` / `duration_ms` / `limits` / `world` / `events` / `last_seq` 相等 |
| 设备是否真的参与 | hw 侧 `link.frames_out > 0`（否则设备根本没跑，一致性无意义） |

## 8. 待人工验证（真实串口 / 真机）

以下每一条**都没有**实测证据，代理不得把它们写进 `docs/PROGRESS.md` 的「已验证」。
人按 `.github/ISSUE_TEMPLATE/hardware-test.md` 开 Issue，逐条验证：

| # | 待验证内容 | 怎么验（步骤） | 通过标准 |
| --- | --- | --- | --- |
| HW-1 | `SerialTransport` 能打开真实串口并按 8N1 / 115200 收发 | 接 ESP32-C3 SuperMini，跑 `python -m liveblocks hw run --ir fixtures/ir/golden/all-ops.json`（把传输换成 `serial:/dev/ttyUSB0`；当前 CLI 默认 pty，需固件侧接入后补这条入口） | 能握手、能跑完 8 步、事件流与 pty 结果一致（`compare_runs` 报告一致） |
| HW-2 | 真机断连（拔 USB）行为 | 跑到一半拔掉 USB，观察是否在秒级内出 `LB-H001` 且不阻塞；再插回看能否重连 | 有 `LB-H001`、进程不挂死；插回后要么接着跑完，要么明确报重连失败 |
| HW-3 | 真机复位后状态是否保留 | 跑到一半给板子复位（EN 键），看 `STEP` 是否重复作用 | 若设备会丢状态，必须在 `HELLO` 里带「已复位」标志并让主机重发 `BEGIN`（本阶段未实现，属 LB-034/LB-020） |
| HW-4 | 真实链路的误码与 CRC 命中 | 长线、低波特率、带干扰条件下跑 1000 步 | 坏帧能被 `LB-H004` 抓到，且不因此错位解析后续帧 |
| HW-5 | 心跳周期 | 定下 `HEARTBEAT` 周期并在真机上观察延迟抖动 | 待 LB-034 定值后回填本文件 |
| HW-6 | 灯效 / 屏幕是否被驱动 | —— | 本阶段**不支持**也不声称；驱动属 LB-034，实测属 LB-020 |

**当前证据口径**：`tests/test_hw_*.py` 与 `tests/test_backend_parity.py` 全部跑在 pty / 内存链路上，
是模拟器证据；`docs/PROGRESS.md` 里 LB-033 的「已验证」只覆盖协议层与模拟器，硬件项在「待硬件验证」。

## 9. 已知缺口（协议未定义 / 本单不做）

1. **§7.1 没有「可恢复错误」的位置**：`error` 事件在 `docs/EXEC.md` §1 里是终止信号；hw 后端把
   「断连后重连成功」也写成 `error` 事件（带 `LB-H001` + `detail.recovered=true`），消费者必须
   以 `ExecResult.status` 判断是否真的结束。这条已记入 `docs/DECISIONS.md` D-024，待 §7 定稿。
2. **没有重传 / 窗口**：本层是「一问一答」，没有滑窗、没有批量 ACK；吞吐不是本阶段目标。
3. **没有传输层加密 / 认证**：串口是本地链路，协议层不做鉴权。
4. **心跳周期未定**：帧格式已定，周期留待 LB-034 实测。
5. **`STEP` 的幂等只靠 `step_id`**：设备只记住**最后一条**；同一 `step_id` 隔了很多步再重发
   会当作新的一步（P0 的 IR 不会这么做，记在这里以免误用）。
6. **载荷是 JSON**：便于人读与跨语言逐字节对齐，但比二进制编码大；若真机带宽吃紧，将来可加
   `KIND=3` 的二进制载荷（需提升线格式版本）。
7. **只支持一块板子**：`board` 固定 `esp32c3-supermini`，其它板子的握手直接报不匹配（未做）。
8. **`BEGIN` 会清零世界状态**：设备侧没有「续跑」概念；跨运行持久化世界状态不在本单。

## 10. 实现与证据索引

| 内容 | 位置 |
| --- | --- |
| 帧编码 / 解码 / CRC / 序号 | `src/liveblocks/hw/frames.py` |
| 命令 / 事件表、`LB-H0xx`、§7.1 映射、文档核对 | `src/liveblocks/hw/protocol.py` |
| 传输抽象与三种实现 | `src/liveblocks/hw/transport.py` |
| 设备模拟器（pty、拔线、重连） | `src/liveblocks/hw/device.py` |
| 主机侧执行器（`backend:"hw"`） | `src/liveblocks/hw/executor.py` |
| 双后端一致性比较 | `src/liveblocks/hw/parity.py` |
| golden 夹具 | `fixtures/hw/`（见 `fixtures/README.md` §13） |
| 单测 | `tests/test_hw_frames.py`、`tests/test_hw_transport.py`、`tests/test_hw_device.py`、`tests/test_hw_exec.py`、`tests/test_hw_cli.py`、`tests/test_hw_fixtures.py`、`tests/test_hw_docs.py` |
| 验收测试（同 IR 双后端） | `tests/test_backend_parity.py` |

常用命令（都只依赖 Python 标准库）：

```bash
python -m liveblocks hw frames --hexdump                      # 打印帧格式、命令/事件表与示例帧
python -m liveblocks hw run --ir fixtures/ir/golden/all-ops.json
python -m liveblocks hw parity --ir fixtures/ir/golden/all-ops.json
python -m liveblocks hw unplug-test --ir fixtures/ir/golden/all-ops.json --unplug-after-steps 2
```

`python -m liveblocks hw unplug-test` 的两行结论分别对应本单的两条验收：拔线产生明确断连错误事件
且不阻塞（①），插回后可重连继续（②）。
