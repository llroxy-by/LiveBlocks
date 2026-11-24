# 序界（LiveBlocks）端到端回归与运行记录 — 草案

> **状态：草案（draft）v0.0.1（`e2e_version`），未冻结，随时可能变更。**
> 本文件约定 LB-009 的实现口径：六阶段链路、§8 运行记录的**生产**、黄金基准、失败摘要、
> 固定时间上界与 `LB-E0xx` 码表。文档由仓库所有者维护，代理只按 Issue 实现并补缺口表；
> 与 `docs/PROTOCOL.md` 冲突时以协议为准（本文件只做加法式补充，见协议 §10 规则 1）。

## 0. 一句话与命令

一条命令跑「生成 → 识别 → 排列 → 解析 → IR → 模拟」全链路，把 §8 运行记录写成
JSONL（首行 `RunHeader`，随后逐层对象与事件）：

```bash
liveblocks e2e run --scene all --out runs/e2e
```

全场景回归（记录形状校验 + 黄金基准比对 + 破坏注入自检 + 固定时间上界）：

```bash
liveblocks e2e regression --dir fixtures/e2e --out runs/e2e
```

两条命令的退出码都只有两种含义：**0 = 全部合格**，**非 0 = 有失败项且已打印失败摘要**。
`runs/e2e` 不入库——`RunHeader.timings_ms` 是记录时实测的墙钟耗时，每跑一次都不一样。

## 1. 六阶段链路

| 阶段 | 标签 | 输入 | 记录里的证据 |
| --- | --- | --- | --- |
| `synth` | 生成（synth） | 场景预设 + 固定种子 | `RunHeader.frame` + `RunHeader.generation` + `timings_ms.synth` |
| `vision` | 识别（vision） | 生成出来的 PNG（从磁盘读） | `MarkerCandidateList` + `CardCandidateList` |
| `layout` | 排列（layout） | `CardCandidateList` | `Layout` |
| `parser` | 解析（parser） | `Layout` | `Ast` |
| `ir` | IR（ir） | `Ast` | `Ir` |
| `exec` | 模拟执行（exec） | `Ir` | `ExecEvent`* + `ExecResult` |

- 每一层的产物都是**该层自己的** `to_dict()`，回归层不重写、不修补字段；层内自校验
  （§3–§7 的 `validate_*`）仍由各层负责。
- **生成层没有 §8 的 `kind`**：协议 §1 里 S1 是 `Frame`（采集侧对象），本层不新增行类型，
  免得 LB-008 的调试视图把陌生的 `kind` 当成坏记录（那会报 `LB-U002`）。生成层的证据因此
  全部放在 `RunHeader` 里：`frame`（帧引用）+ 加法式的 `generation` 块 + `timings_ms.synth`。
- 生成是**真生成**：同种子产出的 PNG 与真值 JSON 与 LB-002 入库的 `fixtures/scenes/*` 逐字节相同
  （回归报告的验收对照里显示「生成层复现 LB-002 入库 fixtures n/n」）。

## 2. 运行记录（§8 落盘）

### 2.1 行序与命名

一次运行一个文件 `<out>/<场景名>.jsonl`，生成出来的图像与真值放在 `<out>/scenes/`：

1. 第 1 行 `RunHeader`；
2. `MarkerCandidateList`；3. `CardCandidateList`；4. `Layout`；5. `Ast`；6. `Ir`；
7. `ExecEvent`（0..n 行，`seq` 严格递增）；
8. `ExecResult`（必须是最后一行）。

`RunHeader.frame.image_ref` 写**相对记录文件所在目录**的路径（`scenes/<场景名>.png`），
所以整目录搬走后仍然指得到图；LB-008 的视图按「记录目录 → 当前目录 → 仓库根」依次尝试解析。

### 2.2 `RunHeader`（§8 未定义的部分按加法式补）

必填字段：`schema_version`、`kind`、`run_id`、`backend`、`scene`、`frame`、`generation`、
`timings_ms`、`created_with`。

| 字段 | 含义 |
| --- | --- |
| `run_id` | 运行标识，形如 `run-e2e-<场景名>` |
| `backend` | 恒为 `sim`（本单不接真实摄像头，事件一律 `backend:"sim"`） |
| `scene` | 场景预设名（如 `normal`） |
| `frame` | 帧引用：`frame_id` / `image_ref` / `width` / `height` / `source` |
| `generation` | 生成层证据：`stage`、`seed`、`image_ref`、`image_sha256`、`ground_truth_ref`、`ground_truth_sha256`、`cards_expected`（破坏注入时多一个 `corrupt`） |
| `timings_ms` | 六个阶段各自的实测墙钟毫秒（`synth`/`vision`/`layout`/`parser`/`ir`/`exec`） |
| `created_with` | `name` / `package_version` / `e2e_version` / `layer_sources` |

`timings_ms` 只是**记录**，不是性能结论；黄金基准不含它（见 §3）。

### 2.3 记录形状的硬条件

回归对**自己产出的记录**逐条核对，任一条不满足即失败：

- 首行必须是 `RunHeader`，且 §2.2 的必填字段齐全；
- 五种层对象（`CardCandidateList`、`Layout`、`Ast`、`Ir`、`ExecResult`）一个都不能缺；
- `kind` 必须按 §2.1 的顺序出现，层对象不允许重复（`ExecEvent` 是事件流，可以多行）；
- `ExecEvent.seq` 严格递增，`ExecResult` 必须在最后一行；
- `RunHeader.frame.image_ref` 必须解析得到真文件。

这些硬条件的失败都报 `LB-E0xx`（见 §6），并带上**阶段**与**行号**。

## 3. 黄金基准 `fixtures/e2e/baseline.json`

入库的基准只记**跨机器可比**的东西：

- 每个场景每一层的**指纹**：`sha256(canonical JSON(该层文档, 键排序) + 换行)`，
  以及该层的 `kind` 序列与行数；
- 生成层的 `image_sha256` 与 `ground_truth_sha256`；
- 每层的关键计数（标记/卡片/行/语句/步/事件/状态）；
- 固定的时间上界（`time_bound_ms` / `record_bound_ms`）。

fingerprint / canonical JSON 的口径：键排序、无多余空白、UTF-8；空集也有确定值。
**不记** `RunHeader.timings_ms` 与 `wall_ms`。因此基准文件本身是确定性的：
用当前代码重跑 `--update-baseline` 应当与入库文件**逐字节相同**（测试会断言）。

基准文件只由命令生成，**不要手改**：

```bash
liveblocks e2e regression --dir fixtures/e2e --update-baseline
```

## 4. 失败摘要与层级定位

回归失败时打印 `失败摘要`，含三件事：

1. **最早失败层**：把全部问题按链路顺序排序，取最上游的一条所在的阶段——破坏注入发生在那一层，
   下游的差异都是它的后果，所以点名最早的一层信息量最大；
2. 逐条问题（`LB-E0xx` + 阶段 + 场景 + 行号）；
3. 复现命令（把本轮用到的 `--dir` / `--out` / `--scene` / `--corrupt` 原样拼回去）。

## 5. 固定时间上界

- 整份回归：`time_bound_ms` = 60000 ms（60 秒）；
- 单条记录（一个场景跑完六阶段）：`record_bound_ms` = 20000 ms（20 秒）。

超了报 `LB-E011` 并判回归失败。实测值只写在报告里当证据，**不是性能结论**，也不入库：
不同机器、不同 Python 版本差别很大（当前开发机整份回归约 9–11 秒，瓶颈是生成层的纯 Python 光栅化）。

## 6. `LB-E0xx` 码表

| 码 | 含义 | 触发方式 |
| --- | --- | --- |
| `LB-E001` | 首行不是 RunHeader | 记录为空，或第一条不是 `RunHeader` |
| `LB-E002` | 记录缺层（某阶段没有对应的 kind） | 删掉 `Layout` / `Ast` / `Ir` / `ExecResult` 等 |
| `LB-E003` | kind 顺序不对或层对象重复 | 把 `Ast` 写到 `Layout` 前面，或同一层对象出现两次 |
| `LB-E004` | RunHeader 缺必填字段或不合法 | 删掉 `run_id` / `generation` / `timings_ms.<层>` |
| `LB-E005` | 出现 §8 与 §1 都没有的 kind | 记录里混进不认识的 `kind` |
| `LB-E006` | 层文档缺必填字段 | 删掉 `Layout.rows` / `CardCandidateList.cards` 等（`--corrupt <层>` 就是干这个） |
| `LB-E007` | 事件流不合法（seq 不严格递增或 ExecResult 不在最后） | 打乱 `seq`，或在 `ExecResult` 后再加一行 |
| `LB-E008` | 记录里的行不是合法 JSON 对象 | 截断某行，或行内容不是对象 |
| `LB-E009` | RunHeader.frame.image_ref 指向的图像找不到 | 图像没落盘 / 路径写错 |
| `LB-E010` | 与黄金基准不一致 | 任一层的指纹、计数、行数与基准不同 |
| `LB-E011` | 超出固定时间上界 | 整份回归 > 60 s，或单条记录 > 20 s |
| `LB-E012` | 破坏注入没有被回归抓到（回归是假通过） | 比对环节失效时会暴露出来 |
| `LB-E013` | 黄金基准缺这个场景 | 新增场景但没 `--update-baseline` |

## 7. 破坏注入（自检与手工复现）

`--corrupt` 只用于**证明回归不是假通过**，不是产品功能。可注入的目标是确定性的：

- `scene-image`：把生成出来的图像整幅抹成中灰（128）——模拟「入库图像被改坏」，
  识别因此 0 个标记，但链路不崩；最早失败层是 `synth`（生成）；
- `vision` / `layout` / `parser` / `ir` / `exec`：删掉该阶段第一个层对象的一个必填字段
  （`CardCandidateList.cards` / `Layout.rows` / `Ast.root` / `Ir.program` / `ExecEvent.seq`），
  JSON 仍然合法但记录形状不合法；最早失败层就是被破坏的那一层。

`liveblocks e2e regression` 默认**自己做一次** `scene-image` 自检：要求比对必须失败、且失败能
定位到层；抓不到就报 `LB-E012`。`--no-self-check` 只用于本地快速迭代——此时「人为破坏一个夹具时
回归明确失败并指出层级」这条验收会显示未通过、命令以退出码 1 结束（默认不需要这个开关）。

## 8. 与 LB-008 的关系

- 运行记录的**生产**在 `src/liveblocks/e2e/`，`fixtures/runs/` 的样例记录仍由 LB-008 的
  `scripts/build_run_records.py` 拼（那是**夹具**，服务于调试视图的自检），本单不改它；
- LB-009 产出的记录可以直接交给 LB-008 的视图打开，**不依赖** `scripts/build_run_records.py`：

  ```bash
  liveblocks e2e run --scene clipped --out runs/e2e
  liveblocks ui view --record runs/e2e/clipped.jsonl
  ```

## 9. 已知限制与协议缺口

1. **§8 没有定义 `RunHeader` 的必填字段**：本层按加法式补 `frame` / `generation` /
   `timings_ms` / `created_with`，其中 `generation` 是本层首创（生成层没有 `kind`）。
2. **§8 没有「生成层」的位置**：`synth` 的产物（PNG + 真值 JSON）不是协议对象，
   只能挂在 `RunHeader` 上；如果将来协议给 S1 之外的生成层定义 `kind`，这里要跟着改。
3. **§8 没有分层耗时的容器**：`timings_ms` 是 U（LB-008）与本层共用的加法式字段。
4. **§8 没有「记录不合格」的错误模型**：`LB-E0xx` 是本层新增区间，未进 `docs/PROTOCOL.md` §9。
5. **不加时间戳**：记录里没有「什么时候跑的」字段，只有实测耗时——为了让基准与记录可复现。
   需要时间信息时由外部（CI 日志 / PR）提供。
6. **不成对落盘**：一次 `e2e run` 只写记录与 `scenes/`，不写日志文件；失败摘要只在标准输出
   （需要留档时用 `--json` 或重定向）。
7. **只跑合成图**：不接真实摄像头、不做标定、不碰固件（ROADMAP 的 LB-010+）；记录里的
   `backend` 恒为 `sim`，模拟通过 ≠ 硬件验证。
8. **单并发**：回归在进程内串行跑场景，没有并行与隔离；`--corrupt` 是「全局同一种破坏」，
   不支持按场景分别注入。
9. **基准只覆盖 6 个入库场景**：新增场景预设必须先 `--update-baseline`，否则报 `LB-E013`。
10. **不做性能回归**：时间上界只是「别挂死」的上限，不是性能基线；`timings_ms` 不入基准。

## 10. 命令

```bash
# 一条命令跑全链路（默认全部场景），记录落到 runs/e2e
liveblocks e2e run --scene all --out runs/e2e

# 只跑指定场景
liveblocks e2e run --scene normal,clipped --out runs/e2e

# 全场景回归（形状 + 基准 + 破坏自检 + 时间上界），退出码 0 = 全过
liveblocks e2e regression --dir fixtures/e2e --out runs/e2e

# 重写黄金基准（改了上游层或基准口径时才用；PR 里要说明 diff）
liveblocks e2e regression --dir fixtures/e2e --update-baseline

# 手工复现「破坏一个 fixture → 回归点名到层」
liveblocks e2e regression --dir fixtures/e2e --scene normal --corrupt layout

# 把记录交给调试视图看
liveblocks ui view --record runs/e2e/normal.jsonl
```
