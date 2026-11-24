# 序界（LiveBlocks）调试界面（S7b `ui.debug`）— 草案

> **状态：草案（draft），`ui_version` 0.0.1，未冻结。**
> 本文件描述 LB-008 的一屏调试视图：它**消费** `docs/PROTOCOL.md` §8 的运行记录（JSONL），
> **不产出**任何业务数据。协议本身的缺口在 §9 列出，只报告、不改 `docs/PROTOCOL.md`。
> 首阶段界面只服务开发者，不做产品级 UI、不对最终用户发布（LB-008 的「不做」）。

## 0. 范围与边界

- **只读**：视图不写回运行记录、不改 `Layout` / `Ast` / `Ir` / 事件流，也不联网。
- **一屏**：五个面板（帧叠加、排列、AST、IR、事件流 + 分层耗时）在同一页里同时呈现。
- **从记录驱动**：输入就是 §8 的 JSONL 文件；界面不重新跑识别/排列/语法/规范化/执行，
  只把记录里已有的层按回放位置呈现出来。这样「看到的现象」与「落盘的证据」是同一份数据。
- **容错**：缺失或损坏的字段给出**可见提示**（`LB-U0xx`）而不是崩溃；缺的层显示占位。
- **确定性**：同一记录 + 同一回放位置渲染两次，HTML / 文本 / 叠加图逐字节相等。
- 不做：产品级 UI、发布给最终用户、编辑数据、真实摄像头预览（LB-020 之后）、
  运行记录的**生产**（`RunHeader` 打包与全链路落盘是 LB-009 的范围，本层只定义消费侧的容忍度）。

## 1. 一屏五层

五个面板与它们的记录来源（面板名与标题是界面原样的字符串，机器核对见
`liveblocks.ui.docs.ui_document_gaps`）：

| 面板 | 标题 | 记录里的来源 | 展示要点 |
| --- | --- | --- | --- |
| `vision` | ① 帧叠加（S2 识别） | `RunHeader.frame` 引用的帧图像 + `MarkerCandidateList`（§3.1）+ `CardCandidateList`（§3.2） | 原帧上叠标记框（`corners_px`）、卡片中心（`center_px`）与排列槽位框；右上给出候选数量与状态分布 |
| `layout` | ② 排列（S3） | `Layout`（§4） | 逐行列出槽位：`slot_index` / `card_face` / `candidate_id` / 画面坐标 / 置信度 |
| `ast` | ③ AST（S4） | `Ast`（§5） | 树状展示节点类型、`span`、`name` / `value` 与 `origin`（指回卡片） |
| `ir` | ④ IR（S5） | `Ir`（§6） | `limits` 与逐步的 `step_id` / `op` / `args` / `source`，当前步高亮 |
| `exec` | ⑤ 事件流（S6）+ 分层耗时 | `ExecEvent`*（§7.1）+ `ExecResult`（§7.2）+ `RunHeader.timings_ms` | 事件表（`seq` / `at_ms` / `phase` / `step_id` / `detail`）、回放位置的世界状态、收尾结论、五层耗时条形图 |

分层耗时由 `RunHeader.timings_ms` 提供（本层的**加法式**字段，§8 没有它，见 §9）。
五个键固定如下（记录里缺哪个，界面上那一行就显示「记录未提供」并报 `LB-U009`）：

| 键 | 标签 | 含义 |
| --- | --- | --- |
| `vision` | 识别（vision） | S2 识别 + 帧读取的实测耗时 |
| `layout` | 排列（layout） | S3 排列的实测耗时 |
| `parser` | 语法（parser） | S4 解析的实测耗时 |
| `ir` | 规范化（ir） | S5 规范化的实测耗时 |
| `exec` | 执行（exec） | S6 模拟执行的实测耗时 |

> 耗时是**记录时实测的真实墙钟毫秒**（`scripts/build_run_records.py` 逐层计时后写进记录），
> 界面只**回显**它，不是本屏的渲染耗时，也不是性能结论。

## 2. 运行记录（§8 JSONL）在界面侧的要求

第一行必须是 `RunHeader`，其后按产出顺序写各层对象。界面**只用**下面这些字段：

| 字段 | 位置 | 缺失时 |
| --- | --- | --- |
| `run_id` | `RunHeader` | 报 `LB-U003`，标题显示 `?` |
| `backend` | `RunHeader` | 报 `LB-U003`（warning），按 `unknown` 显示 |
| `frame` | `RunHeader` | 报 `LB-U007`：叠加面板显示占位图，其余面板照常 |
| `frame.image_ref` | `RunHeader.frame` | 同上；相对路径依次按「记录所在目录 → 当前目录 → 仓库根」解析 |
| `timings_ms` | `RunHeader` | 报 `LB-U009`：耗时一栏显示「记录未提供」 |
| `kind` | 每一行 | 报 `LB-U002`：该行忽略，其余行继续 |

允许的 `kind` 与顺序：`RunHeader` → `MarkerCandidateList` → `CardCandidateList` → `Layout` →
`Ast` → `Ir` → `ExecEvent`* → `ExecResult`。`MarkerCandidateList` 可以没有（它只是 S2a 的中间产物，
此时叠加图只画卡片候选，记一条 info）。

解析规则（`liveblocks.ui.record.parse_record_file`）：

1. 任何一行不是合法 JSON → `LB-U001`，**整行丢弃**，其余行继续；
2. 任何一层缺字段 → `LB-U003`，缺的字段按「画不出来」降级（跳过那一条，不猜值）；
3. 层重复或乱序 → `LB-U005`，渲染按出现顺序，重复的层只保留第一份；
4. 事件 `seq` 不是严格递增 / `at_ms` 回退 → `LB-U008`，仍按原值展示（不排序、不修补）；
5. 记录为空或首行不是 `RunHeader` → `LB-U006`，视图仍然渲染（全空面板 + 提示）。

## 3. 回放与单步

回放**只从记录重算**，不重跑业务层：

- 时间线的第 `0` 帧是「还没处理任何事件」，第 `k` 帧是「处理完前 `k` 条 `ExecEvent`」；
- 每帧的世界状态由事件 `detail.state_delta`（§7.1 的加法式字段）依次折叠得到，折叠规则固定：
  `pos.x` / `pos.y` / `facing` / `holding` / `jumps` 各自「后者覆盖前者」，不插值、不推断；
- 每帧还记「当前是哪一步 `step_id`」「累计虚拟耗时 `at_ms`」「哪些步已经走完」；
- `--step N` 越界时夹紧到 `[0, 事件条数]`，不报错；
- 没有 `ExecEvent` 时时间线只有 1 帧（初始世界）+ 收尾结论，界面给出提示。

确定性判据（第二条验收）：同一记录解析两次、渲染两次，`html` / `text` / `overlay` 三种产物
与时间线指纹逐字节相等。渲染里不含时间戳、不含随机序、不含浮点格式化差异（耗时统一用
`liveblocks.ui.protocol.format_ms`）。

## 4. 问题码（`LB-U0xx`）

界面层自己的码（§9 没有 UI 区间，本层加法式补充；形状沿用 §9 的诊断对象，另带记录行号与面板名）：

| 码 | 含义 | 严重性 |
| --- | --- | --- |
| `LB-U001` | 记录某行不是合法 JSON | error |
| `LB-U002` | 行里的 kind 缺失或不是已知层 | error |
| `LB-U003` | 层对象缺少必填字段或字段类型不符 | error / warning |
| `LB-U004` | §8 要求的层没有出现在记录里 | error |
| `LB-U005` | 层顺序错乱或同一层重复出现 | warning |
| `LB-U006` | 记录为空或首行不是 RunHeader | error |
| `LB-U007` | 引用的帧图像读不出来或尺寸不符 | error / warning |
| `LB-U008` | 事件流 seq / 时间不单调 | warning |
| `LB-U009` | 分层耗时缺失或非法 | warning |

各层自己的诊断（`LB-V0xx` / `LB-L0xx` / `LB-P0xx` / `LB-I0xx` / `LB-R0xx`）**原样展示**，
界面不改它们的语义、也不吞掉它们。

## 5. 命令与产物

```bash
# 文本形态（默认打印到标准输出），以及单步到第 3 帧
liveblocks ui view --record fixtures/runs/sample-overlap.jsonl
liveblocks ui view --record fixtures/runs/sample-overlap.jsonl --step 3 --text

# HTML 形态（自包含单文件，可直接用浏览器打开；截图证据就是从它渲染出来的）
liveblocks ui view --record fixtures/runs/sample-overlap.jsonl --html-out /tmp/lb-008-view.html

# 只导出「帧叠加」面板的 PNG（仓库自带编码器，字节可复现）
liveblocks ui view --record fixtures/runs/sample-overlap.jsonl --overlay-out /tmp/lb-008-overlay.png

# 回放：打印时间线、两次回放一致性、分段实测耗时
liveblocks ui replay --record fixtures/runs/sample-overlap.jsonl

# 对 fixtures/runs 跑一遍：逐夹具结论 + 四条验收对照 + 截图证据核对
liveblocks ui report --dir fixtures/runs
```

样例与损坏记录都在 `fixtures/runs/`（清单见 `fixtures/runs/index.json`），重新生成用
`scripts/build_run_records.py`（逐层真跑并实测耗时，见 `fixtures/README.md` §8）。

## 6. 验收对照（Issue #20）

| 验收 | 本实现怎么证明 |
| --- | --- |
| 打开样例运行记录可看到五层数据同时呈现 | `ui report` 逐夹具核对 `view.complete_layers` 含五层，且 HTML 里五个 `data-layer` 面板都在 |
| 回放同一记录两次结果一致 | `ui report` 每份夹具回放两次，比对 HTML / 文本 / 叠加图哈希与时间线指纹 |
| 对缺失/损坏字段给出可见提示而非崩溃 | `fixtures/runs/corrupt/*.jsonl` 必须渲染成功，且 HTML 顶部出现 `LB-U0xx` 提示条 |
| 附截图作为证据 | `docs/evidence/lb-008-*.png` 由 `scripts/screenshot_view.py` 用 WebKitGTK + Xvfb 渲染 `ui view` 产出的 HTML 得到；`ui report` 核对其尺寸与「非空白」指标 |

截图是**真渲染**得到的，不是 mock-up：`scripts/screenshot_view.py` 只做两件事——调用
`liveblocks.ui.render` 生成 HTML、用浏览器引擎把这份 HTML 画出来再存 PNG。如果环境里没有
可用的渲染引擎，脚本会以非零码退出并说明原因，而不是伪造一张图。

## 7. 已知限制

1. **界面不重跑业务层**：记录里没有的层（例如没写 `MarkerCandidateList`）就不会被补出来，
   这是「只读」的代价，也是「看到的就是落盘的证据」的好处；
2. **世界状态是折叠出来的**：只按 `state_delta` 叠加，`finish` 事件缺少增量时会在面板上标注
   「世界状态不完整」，不猜；
3. **叠加图用点阵字体标签**：标签只有 ASCII（`data/bitmap_font.json` 的字形集），中文只出现在
   HTML/文本形态里；
4. **耗时是记录时的实测值**：不同机器上重新生成记录会得到不同的 `timings_ms`，因此样例记录的
   哈希只能对同一份入库记录成立；
5. **截图不是像素级回归测试**：`ui report` 只量化「非空白、有层次、尺寸正常」，不比对像素；
   要像素级比对需要固定的渲染引擎版本，本阶段不做；
6. **没有交互式前端**：单步是命令行 `--step N`，没有按钮、没有 JS（HTML 里没有 `<script>`），
   这样「一屏」在任何浏览器里都能离线打开，也便于截图；
7. **`backend: "hw"` 会被标注但不改变结论**：界面只能显示硬件后端写进记录的事件，模拟通过
   不等于硬件验证。

## 8. 协议缺口（只报告，未改 `docs/PROTOCOL.md`）

1. §8 只说「首行 `RunHeader`」，没有定义 `RunHeader` 的必填字段（`run_id` / `backend` / 输入帧引用
   各自怎么放、字段名是什么）；
2. §8 的「输入帧引用」没有字段名与解析规则（本实现用 `frame.image_ref`，相对路径三级解析）；
3. §8 没有**分层耗时**的槽位（本实现加法式加 `RunHeader.timings_ms`，并声明五个键）；
4. §8 没说「一层缺了」怎么办、坏行怎么报（本实现用 `LB-U0xx` 区间，形状沿用 §9）；
5. §9 没有界面（S7b）的码区间；
6. §9 的诊断对象没有「记录行号」与「所属面板」字段（本实现把它放在界面层的 `RecordIssue` 里，
   不进各层文档）；
7. §7.1 没有规定「事件的 `state_delta` 缺失」时消费者怎么表现（本实现折叠已知增量并标注）；
8. §8 没有规定「同一 `kind` 出现两次」的语义（本实现保留第一份并报 `LB-U005`）；
9. §8 没有规定记录里的帧图像路径是相对记录文件、仓库根还是绝对路径。

以上缺口只影响消费侧的表现，**不影响**任何一层的业务语义；若协议为它们补字段，
本实现按 §10 的规则 1 保持向后兼容（缺字段时降级为提示）。
