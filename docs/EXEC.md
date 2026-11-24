# 序界（LiveBlocks）S6 模拟执行层 — 草案

> **状态：草案（draft）`exec_version` 0.0.1，未冻结。**
> 本文件是 LB-007 的**模拟执行器规范**：它说明「`docs/PROTOCOL.md` §6 的 `Ir` 怎么被执行成 §7 的
> `ExecEvent[]` + `ExecResult`」。
> 机器可读的同一份定义在 `src/liveblocks/runtime/semantics.py`（执行语义表、虚拟时钟换算、`LB-R0xx`
> 码表），`src/liveblocks/runtime/protocol.py` 是 §7 文档的数据模型与校验器。两边必须一致——
> `tests/test_exec_docs.py` 与 `liveblocks exec report` 会逐项核对（语义表的每一行、诊断码与标题、
> 时钟换算、枚举取值、世界状态字段），任何一边改了另一边没跟上就会失败。
> 语义或判定顺序改动必须提升 `exec_version` 并记进 `docs/DECISIONS.md`。

## 0. 范围与依据

- **依据**：`docs/ROADMAP.md` LB-007「IR → `ExecEvent[]` + `ExecResult`，虚拟时钟，步数/超时限制，
  中断」；`docs/DECISIONS.md` D-018（本层的取舍与协议缺口清单）。
- **输入**：§6 的 `Ir`（LB-006 的产物；`program.steps` + `program.limits` 都已显式）。
- **输出**（三份，全部可逐字节复现）：
  1. `ExecEvent[]`（§7.1）—— 事件流，`seq` 从 1 严格递增，`at_ms` 是**虚拟**时钟读数；
  2. `ExecResult`（§7.2）—— 运行结论（`status` / `steps_executed` / `duration_ms` / 诊断）；
  3. `ExecState`—— 本层**加法式**新增的可读状态文档（§7 没有「最终状态」对象），把 `limits`、
     世界状态与事件计数收在一起，供人直接读。
- **不做**：不接硬件（`backend.hw` 属于 LB-013）、不做界面（LB-008）、不做运行记录落盘（§8 的
  `RunHeader` 与 JSONL 打包属于 LB-009）。本层产出的 JSONL 只是「事件流」，不是完整运行记录。
- **不是硬件证据**：所有事件的 `backend` 都是 `sim`。模拟通过 ≠ 真机能跑，这条写死在
  `runtime.sim.validate_run()` 里（出现非 `sim` 事件即判实现缺陷）。

## 1. 判定顺序（一次运行的控制流）

```
事件 1：log（run started，带 limits / steps / clock_ms_per_tick）
for 每一步 s（按 Ir.program.steps 顺序）：
    1. 中断？   → status=aborted，事件 error(LB-R004)，结束
    2. 步数够？  steps_executed ≥ max_steps → status=limit_exceeded，error(LB-R002)，结束
    3. 语义在？  op 不在语义表 / 参数取不到 → status=runtime_error，error(LB-R001)，结束
    4. 会超时？  elapsed_ms + cost_ms > timeout_ms → status=timeout，error(LB-R003)，结束
                   （时钟停在 timeout_ms）
    否则：事件 start → 作用到世界状态 → 事件 finish（带 state_delta）
          elapsed_ms += cost_ms；steps_executed += 1
全部步骤跑完 → status=ok
```

四条判定**都在执行某一步之前**发生，因此被拒绝的那一步不会产生 `start`/`finish`，也不会改世界状态。
顺序是「中断 → 步数 → 语义 → 超时」：中断是调用方意图，优先级最高；步数是硬上限，先于语义检查；
语义检查先于时间检查，这样「未知 `op`」不会被误报成超时。

## 2. 虚拟时钟与执行语义

### 2.1 时钟

- 虚拟时钟从 `0` 开始，**只被步骤推进**：每执行完一步 += `cost_ms`。不读墙钟、不睡真实时间。
- 换算：**10 ms/tick**（`CLOCK_MS_PER_TICK = 10.0`，虚拟毫秒：1 tick 走 10 ms）。`tick` / `cell` /
  `quarter_turn` 仍然只是标签（`docs/IR.md` §9 缺口 5，Q5 未定），本层只给这一条内部换算。
- 因此所有 `at_ms` 都是 10 的整数倍，浮点可精确表示，可以逐字节比对。
- `wait 100000` 只把时钟往前拨 1 000 000 ms，真实耗时仍是毫秒级——这就是「死循环样例不会挂死」
  的依据（`tests/test_exec_sim.py` 用墙钟断言这一点）。

### 2.2 世界状态（最小网格模型）

| 字段 | 含义 | 初值 |
| --- | --- | --- |
| `pos` | 网格位置 `{x, y}`，单位 `cell`；`y` **向上**为正（网格坐标，不是协议 §0 的图像像素） | `{x: 0, y: 0}` |
| `facing` | 朝向，取值 `north` / `east` / `south` / `west`（逆时针排列） | `north` |
| `holding` | 手上的物品数量（`pick` +1、`drop` −1） | `0` |
| `jumps` | 跳过的次数（`jump` +1） | `0` |

这套状态只为两件事：让 `finish` 事件的 `detail.state_delta` 有内容，让最终状态 JSON 可读。
它是 **P0 的占位语义**，不是产品定义（Q5 定稿后可能整体改写）。

### 2.3 op 语义表（卡面 → 虚拟耗时 + 状态影响）

| `op` | 虚拟时钟耗时（tick） | 状态影响 |
| --- | --- | --- |
| `move-forward` | 2 | `pos += 朝向 × distance` |
| `move-back` | 2 | `pos -= 朝向 × distance` |
| `turn-left` | 1 | `facing 逆时针转 angle × 90°` |
| `turn-right` | 1 | `facing 顺时针转 angle × 90°` |
| `jump` | 1 | `jumps += 1` |
| `pick` | 1 | `holding += 1` |
| `drop` | 1 | `holding -= 1` |
| `wait` | `duration`（来自 `args.duration`） | `无（只推进虚拟时钟）` |

- 耗时取「最小可解释值」：移动比原地转向贵（2 tick vs 1 tick），`wait` 就是它自己写的 `duration`；
  没有物理依据，改它必须提升 `exec_version` 并重生成 golden。
- `drop` 在 `holding == 0` 时报 `LB-R005`（`runtime_error`）：这是 P0 的**语义检查**，不是崩溃；
  `Ir` 本身合法（LB-006 只管 `op` / 元数 / 单位）。
- `move-forward` / `move-back` 的 `distance` 与 `turn-*` 的 `angle` 都来自 §6 的 `args`，非负整数
  （方向写在 `op` 里，LB-006 的约定）。

## 3. 事件（§7.1 落地）

```json
{ "schema_version": "0.0.1", "kind": "ExecEvent", "run_id": "run-0007", "seq": 3, "at_ms": 12.4,
  "step_id": "s0", "phase": "finish", "backend": "sim", "detail": { "state_delta": { "pos": 1 } } }
```

- `phase` 取值：`start` / `finish` / `error` / `log` / `feedback`。本层产出前三者加 `log`；
  `feedback` 属于反馈通道（LB-008/LB-013），本层不产出。
- `backend` 取值：`sim` / `hw`。本层**只产出 `sim`**。
- `seq` 从 1 开始、每次 +1、**严格递增**；`at_ms` 单调不减（虚拟时钟）。
- `step_id` 为 `""` 表示事件不绑定具体步骤：目前只有起步的 `log` 事件这样写。
- 一次运行的事件序列固定为：`log` → 每步 `start` + `finish`（或中途一条 `error` 收尾）。
  因此 `n` 步成功运行有 `1 + 2n` 条事件；被限制/中断/出错截断的运行事件数更少。
- `detail` 的内容（本层约定，全部是对象）：
  - `log`：`{message, steps, limits, backend, clock_ms_per_tick}`；
  - `start`：`{op, args, cost_ticks, cost_ms}`；
  - `finish`：`{state_delta, cost_ticks, cost_ms}`；
  - `error`：`{code, severity, message, …}`（各码附带相关数值，如 `max_steps` / `timeout_ms`）。

## 4. `limits`（来自 IR 的硬约束）

- `max_steps` / `timeout_ms` **只读** `Ir.program.limits`（LB-006 已让它们显式出现，缺省 1000 / 5000）；
  本层不设第二个上限，也不改它们的值。三处回显：起步 `log` 事件的 `detail.limits`、`ExecResult` 的
  截断位置、`ExecState.limits`。
- **步数**：`steps_executed` 达到 `max_steps` 且还有步骤没跑 → `limit_exceeded`（`LB-R002`）。
  恰好跑完 `max_steps` 步且程序正好结束 → `ok`（不是超限）。
- **超时**：下一步的虚拟耗时会让总耗时**超过** `timeout_ms` → `timeout`（`LB-R003`），时钟停在
  `timeout_ms`。总耗时**恰好等于** `timeout_ms` 且程序跑完 → `ok`（`fixtures/exec` 的
  `timeout-exact` 夹具把这条边界钉死）。

## 5. 中断（`aborted`）

两条入口，都**只在步骤边界生效**（不会打断半步、不会留下半个 `state_delta`）：

1. `SimExecutor.request_abort()` —— 线程安全（内部是 `threading.Event`），可以从另一个线程调用；
2. `should_abort(step_index, elapsed_ms) -> bool` 回调 —— 给「跑到第 N 步」「虚拟时钟到 N ms」这类
   确定性中断用；CLI 的 `--abort-at-step N` / `--abort-at-ms N` 就是它。

中断后的产物：`status = "aborted"`，一条 `LB-R004`（severity `info`）诊断 + 一条 `error` 事件，
`steps_executed` = 中断前已完成的步数，虚拟时钟停在中断时刻。

## 6. 诊断码（`LB-R0xx`）

| 诊断码 | 标题 | 严重度 | 何时报 | `ExecResult.status` |
| --- | --- | --- | --- | --- |
| `LB-R001` | op 没有执行语义 | error | 步骤的 `op` 不在执行语义表里，或参数取不到非负整数（防御路径：合法 `Ir` 走不到，但下游不该崩或挂死） | `runtime_error` |
| `LB-R002` | 步数上限 | warning | 已执行步数达到 `limits.max_steps` 而程序还没跑完 | `limit_exceeded` |
| `LB-R003` | 超时 | warning | 下一步的虚拟耗时会让总耗时超过 `limits.timeout_ms` | `timeout` |
| `LB-R004` | 被中断 | info | 中断标志或 `should_abort` 回调在步骤边界返回真 | `aborted` |
| `LB-R005` | 手上没有物品 | error | `drop` 时 `holding` 为 0（P0 网格模型的语义检查） | `runtime_error` |

- `LB-R002` / `LB-R003` 沿用 §9 示例的语义，其余三个按 `LB-R0xx` 区间自增。
- 一次运行最多一条诊断（`ok` 时没有诊断）：运行在第一次触发条件处就结束，不做级联。
- 诊断对象沿用 §9 的统一形状 `{code, severity, message}`；`ExecResult.diagnostics` 只放 `LB-R0xx`，
  不复制上游 `LB-I0xx` / `LB-P0xx`（各层诊断留在各层对象里）。

## 7. 结果与最终状态

### 7.1 `ExecResult`（§7.2，原样）

```json
{ "schema_version": "0.0.1", "kind": "ExecResult", "run_id": "run-0007", "status": "ok",
  "steps_executed": 1, "duration_ms": 12.4, "backend": "sim", "diagnostics": [] }
```

`status` 取值：`ok` / `parse_error` / `runtime_error` / `timeout` / `aborted` / `limit_exceeded`。
本层产出其中五个；`parse_error` 走不到（输入已经是 `Ir`，解析错误在 LB-005/LB-006 就报了，见 §10）。

### 7.2 `ExecState`（本层加法式新增，可读的最终状态）

```json
{
  "schema_version": "0.0.1",
  "kind": "ExecState",
  "exec_version": "0.0.1",
  "run_id": "run-all-ops",
  "backend": "sim",
  "status": "ok",
  "steps_executed": 8,
  "duration_ms": 130.0,
  "limits": { "max_steps": 1000, "timeout_ms": 5000 },
  "world": { "pos": { "x": 2, "y": 0 }, "facing": "east", "holding": 0, "jumps": 1 },
  "events": 17,
  "last_seq": 17
}
```

- 这份文档就是验收里的「可读的状态输出（最终状态 JSON）」：`status` + 世界状态 + 实际用到的
  `limits` + 事件条数，一个文件读完。
- 它是**新 `kind`**（协议 §1 的对象表里没有），属于加法式扩展；协议是否收录由仓库所有者决定（§10）。

## 8. 确定性

同一份 `Ir` + 同一 `run_id` + 同一中断条件跑两次，事件流与最终状态**逐字节一致**：

1. 事件只由「步骤顺序 + 语义表 + limits」决定，没有时间戳、随机量、哈希序；
2. `at_ms` 是虚拟时钟读数（10 ms 整数倍），不是墙钟；
3. 序列化只走 `runtime.protocol.canonical_text()` / `events_jsonl()`（固定缩进或紧凑分隔符 +
   结尾换行，不排序键，非 ASCII 原样）；
4. 实测墙钟耗时（`SimRun.wall_ms`）只进报告，**不进**任何文档。

## 9. 命令

```bash
# 跑一份 Ir：stdout 打印摘要 + 最终状态 JSON（ExecState）
liveblocks exec run --ir fixtures/ir/golden/all-ops.json

# 事件流落 JSONL（§8 风格）、结果与状态各存一份
liveblocks exec run --ir fixtures/ir/golden/all-ops.json \
  --events-out /tmp/events.jsonl --result-out /tmp/result.json --state-out /tmp/state.json

# 确定性中断：执行到第 3 步之前停下（status=aborted，LB-R004）
liveblocks exec run --ir fixtures/exec/ir/abort.json --abort-at-step 3

# 对全部执行夹具真跑一遍：逐条结果 + 七条验收对照 + 实测耗时（退出码 0 = 全过）
liveblocks exec report --dir fixtures/exec
```

`exec run` 的退出码：`status == "ok"` → 0，其它状态 → 1（限制/中断/运行错误都是**如实结果**，不是
崩溃；报告里按清单期望值核对）。

## 10. 明确不做 / 已知缺口

- 不接硬件：没有 `backend.hw` 路径，也没有任何设备协议（LB-013）。
- 不做界面、不做回放（LB-008）、不做运行记录打包与 `RunHeader`（LB-009）。
- **协议缺口（只报告，未改 `docs/PROTOCOL.md`；完整清单见 `docs/DECISIONS.md` D-018）**：
  1. §7 没说一步占多少虚拟时间，也没说 `tick` ↔ 毫秒的换算；本实现定为 10 ms/tick；
  2. §7 没说 `limits` 的判定顺序、边界（恰好用完算不算超）、以及超时是墙钟还是虚拟时钟；
     本实现按虚拟时钟、先步数后超时、恰好等于上限算 `ok`；
  3. §7.2 的 `parse_error` 对执行器没有意义（输入已是 `Ir`），本层永不产出——建议协议注明
     「`parse_error` 由上游生产者使用」；
  4. §7 没有「最终状态」对象；本层新增 `ExecState`（新 `kind`），是否并入协议待定；
  5. §7.1 的 `step_id` 没给「不绑定步骤」的写法；本层用空字符串表示（只有起步 `log` 事件）；
  6. §9 只给了 `LB-R002` / `LB-R003` 两个例子，`LB-R001` / `LB-R004` / `LB-R005` 由本实现自增；
  7. §7 没说 `detail` 里该放什么；本层按 `phase` 各给一组固定字段（§3），建议并入协议；
  8. §7.3 的 `FeedbackCommand`（S7）本层不产出：反馈通道属于 LB-008/LB-013。
