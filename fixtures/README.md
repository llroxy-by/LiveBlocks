# fixtures/

测试夹具目录。

## 1. `skeleton.json`（LB-001）

一条占位夹具，用于验证「fixtures 目录被测试真实读取」这条链路，不含业务数据。

## 2. `scenes/`（LB-002 合成卡片场景）

合成卡片图像 + 真值 JSON，由 `liveblocks synth` 生成，**全部入库**。覆盖 Issue #7 要求的
5 类样例，外加一个多卡多行场景：

| 场景 | 覆盖类别 | 卡片 | 说明 |
| --- | --- | --- | --- |
| `normal` | 正常 | 1 | 单卡正对相机、无遮挡、光照均匀 |
| `slight-rotation` | 轻微旋转 | 3 | 一排三卡，面内旋转约 ±5°，带少量俯仰 |
| `overlap` | 重叠 | 3 | 卡间距小于卡宽（互相压盖）+ 一个纸片状遮挡物 |
| `clipped` | 裁切 | 2 | 一张卡越出左边界、一张卡被下边界切断 |
| `low-light` | 低光照 | 2 | 增益 0.32、噪声 σ=9、暗角 0.42 |
| `multi-row` | 多卡多行 | 6 | 2 行 × 3 列，俯仰 7°，位姿由种子抖动 |

每个场景两个文件：`<场景>.png`（640×480、8bit 灰度）与 `<场景>.json`（真值）。
`index.json` 是清单，记录种子与每张图的 `pixel_sha256`。

### 2.1 重新生成（固定种子，字节可复现）

```bash
python -m liveblocks synth generate --scene all --out fixtures/scenes
```

默认种子是 `20261123`（`liveblocks.cli.DEFAULT_SEED`），改种子必须同时提交重新生成的
图像与真值。

### 2.2 校验入库文件

```bash
python -m liveblocks synth verify --dir fixtures/scenes
```

逐个场景用「真值里记录的种子」重新生成，比对 PNG 字节、解码后的像素与真值文本，全部
一致时退出码为 0。`tests/test_synth_reproducibility.py` 覆盖同一件事。

### 2.3 真值 JSON 字段

| 字段 | 含义 |
| --- | --- |
| `scene_id` / `seed` / `generator` | 场景名、随机种子、生成器版本与所用字典/版式版本 |
| `image` | 文件相对路径、尺寸、`pixel_sha256`（解码后像素的哈希，与 PNG 压缩实现无关） |
| `camera` | 针孔内参 `fx/fy/cx/cy`（合成帧无畸变，畸变留给 LB-010） |
| `lighting` / `jitter` | 光照参数与位姿抖动幅度（种子驱动） |
| `cards[]` | 每张卡：`candidate_id`、`card_id`、`card_face`、`card_type`、`marker_id`、`pose`（`x/y/z_mm`、`yaw/pitch/roll_deg`）、`center_px`、`corners_px`、`marker_corners_px`、`in_plane_angle_deg`、`status`、`visible_fraction`、`clipped`、`clipped_corners`、`occluded_by`、`covered_by_cards` |
| `occluders[]` | 遮挡物 id 与图像坐标四边形 |

角点顺序固定为**左上、右上、右下、左下**，且按卡片/标记自身坐标系解释（不是「图像上方」），
与 `docs/PROTOCOL.md` §3.1 的 `corners_px` 语义一致。位姿是**抖动之后**的实际位姿，
因此 `pose` 与 `corners_px` 永远描述「实际画出来的这张卡」。

`status` 取值与 `docs/PROTOCOL.md` §3.2 的 `CardCandidateList.status` 对齐：
`ok` | `clipped` | `occluded`（`unknown_card` / `ambiguous` 属于识别层结论，生成器不会产出）。

## 4. `layouts/`（LB-004 卡片候选夹具）

排列层（LB-004）的输入是协议 §3.2 的 `CardCandidateList`，因此夹具不是图像而是**候选列表 JSON**。
四个文件覆盖 Issue #11 的验收点：

| 夹具 | 覆盖 | 来源 |
| --- | --- | --- |
| `multi-row-cards.json` | 两行样例被正确分成两行（6 张卡 → 2 行 × 3 槽位） | **真跑出来的**：`liveblocks recognize detect --image fixtures/scenes/multi-row.png --card-layout card-40x40` 的输出原样入库 |
| `overlap-cards.json` | 身份未知的候选被排列层排除并留痕（`LB-L003`） | 同上，`fixtures/scenes/overlap.png` 的识别输出（含 1 个 `occluded` 候选） |
| `spacing-ambiguity.json` | 行内间距过小 → `LB-L001` | **手写**：单排 4 张卡，其中一张与前一张只差 38 px（典型卡距 79 px） |
| `duplicates.json` | 重复候选去重 → `LB-L002` | **手写**：同一张卡（`C01`）被检出两次，圆心相距 3.6 px，置信度 0.92 / 0.61 |

- 两个「真跑」夹具是**上游行为的快照**：`tests/test_layout_fixtures.py::test_recognition_snapshots_match_fresh_recognition`
  会用当前识别代码重跑并逐字段比对，识别变了会立刻失败；要更新就重跑上面的命令覆盖文件。
- 两个手写夹具是 §3.4「夹具只由 `liveblocks synth` 生成」的**唯一例外**：需要复现的是「识别层可能
  吐出的歧义/重复候选」，而 LB-002 的场景预设不产生这两种形态（`overlap` 场景虽然卡间距小，但识别
  只给出 1 个可用候选）。它们都是 §3.2 的合法文档，`tests/test_layout_fixtures.py` 会先用
  `validate_card_candidate_list` 校验，再断言诊断码与槽位。
- 校验命令：

  ```bash
  # 跑全部候选夹具，打印行/槽位/诊断与验收对照（退出码 0 = 四条验收全过）
  liveblocks layout report --dir fixtures/layouts
  ```

## 5. `ast/`（LB-005 语法夹具）

语法层（LB-005）的输入是协议 §4 的 `Layout`，因此夹具是**Layout JSON**（不是图像，也不是卡片
候选），一旁配一份清单 `ast/index.json` 说明每个夹具「期望合法还是非法、几条语句、哪些诊断码」。
解析器只吃 §4 文档，不含任何夹具元数据——期望值放清单里，夹具本身仍是 100% 合规的 §4 对象。

| 夹具 | 期望 | 语句 | 诊断 | 来源 |
| --- | --- | --- | --- | --- |
| `valid-basic.json` | 合法 | 3 | 无 | **手写**：三条语句，参数 1 / 2 / 0 个各一条 |
| `valid-empty.json` | 合法 | 0 | 无 | **手写**：没有任何行 → 空程序 |
| `from-overlap-cards.json` | 合法 | 1 | 无 | **真跑出来的**：`liveblocks layout arrange --candidates fixtures/layouts/overlap-cards.json` 的输出原样入库 |
| `from-multi-row-cards.json` | 非法 | 0 | `LB-P002`、`LB-P004` | 同上，输入换成 `multi-row-cards.json`：`jump` 占了参数位、`repeat` 占了语句位 |
| `invalid-number-first.json` | 非法 | 1 | `LB-P001` | **手写**：第 2 行以数字卡开头（缺命令卡），第 1 行照常解析 |
| `invalid-control-flow.json` | 非法 | 1 | `LB-P004`×2 | **手写**：`repeat` / `end` 作为语句头 |
| `invalid-operator-arg.json` | 非法 | 1 | `LB-P005` | **手写**：`operator-add` 占参数位（P0 不做表达式） |
| `invalid-empty-slot.json` | 非法 | 1 | `LB-P003`×2 | **手写**：空卡面与字典里没有的卡面 `hologram` |

- 两个 `from-*.json` 是**上游快照**：`tests/test_parser_fixtures.py::test_pipeline_fixtures_match_fresh_arrangement`
  会用当前排列层重跑并逐字段比对，LB-004 变了会立刻失败；要更新就重跑上面的 `layout arrange` 覆盖文件。
- 手写的 6 个是同类例外（`fixtures/layouts` 的候选夹具只覆盖「重复」「间距歧义」等形态，凑不出
  「数字卡打头」「运算符占参数位」这类语法错误）：它们都是合法 §4 文档，测试会先用
  `validate_layout` 校验再断言诊断码。
- 校验命令：

  ```bash
  # 跑全部语法夹具，打印逐条结果与验收对照（退出码 0 = 三条验收全过）
  liveblocks parse report --dir fixtures/ast
  ```

## 6. `ir/`（LB-006 规范化夹具）

规范化层（LB-006）的输入是协议 §5 的 `Ast`，输出是 §6 的 `Ir`，因此夹具成对出现：

```
fixtures/ir/
├── index.json          # 清单：每个夹具的期望 steps / ops / codes / limits 与来源
├── asts/*.json         # 输入：§5 的 Ast 文档
└── golden/*.json       # 期望输出：§6 的 Ir，逐字节比对
```

| 夹具 | 步数 | 诊断 | 来源 |
| --- | --- | --- | --- |
| `all-ops` | 8 | 无 | **手写 Ast**：覆盖 op 表全部 8 个命令卡面、元数全对 |
| `from-valid-basic` | 2 | `LB-I002` | **真跑出来的**：`liveblocks parse ast --layout fixtures/ast/valid-basic.json`（`turn-left 2 3` 元数不符） |
| `from-valid-empty` | 0 | 无 | 同上，空程序（`valid-empty`），`limits` 照样显式出现 |
| `from-overlap-cards` | 0 | `LB-I002` | 同上，单张 `move-back` 没带数字卡 |
| `unknown-op` | 1 | `LB-I001`×2 | **手写 Ast**：`teleport` 不在 op 表、`repeat` 是文法外的控制流卡 |
| `arity-mismatch` | 1 | `LB-I002`×2 | **手写 Ast**：`wait` 没带数字、`jump` 多给一个数字 |
| `unsupported-node` | 1 | `LB-I003` | **手写 Ast**：`Program.body` 里出现 `node: "Loop"` |
| `non-literal-arg` | 0 | `LB-I004` | **手写 Ast**：参数是 `node: "String"` |

- 三个 `from-*.json` 是**上游快照**：`tests/test_ir_fixtures.py::test_pipeline_fixtures_match_fresh_parse`
  会用当前语法层重跑并逐字段比对 `asts/from-*.json`，LB-005 变了会立刻失败；要更新就重跑上面的
  `parse ast --out fixtures/ir/asts/…`（再用 `ir normalize --out` 重生成 golden）。
- 手写 Ast 夹具是同类的例外（现有 `fixtures/ast` 里凑不出「未知 op」「元数不符」这类**规范化**错误，
  它们是合法 §5 文档，只是语义超纲）：测试会先用 `validate_ast` 校验，再断言诊断码。
- golden 只由命令生成，**不要手改**：

  ```bash
  # 重新生成黄金快照（改 op 表 / 改 args 折法后必须重跑，并在 PR 里说明 diff）
  liveblocks ir normalize --ast fixtures/ir/asts/all-ops.json --out fixtures/ir/golden/all-ops.json

  # 跑全部夹具：两次规范化比字节、与 golden 逐字节比、schema 校验 + 验收对照（退出码 0 = 全过）
  liveblocks ir report --dir fixtures/ir
  ```

## 7. `exec/`（LB-007 执行夹具）

执行层（LB-007）的输入是协议 §6 的 `Ir`，输出是事件流（§7.1，JSONL）与最终状态（本层加法式的
`ExecState`，JSON），因此夹具是三件套：

```
fixtures/exec/
├── index.json          # 清单：每个夹具的期望 status / 步数 / 事件条数 / 诊断码 / limits / 中断点
├── ir/*.json           # 输入：手写的合法 §6 Ir（其余夹具直接引用 ../ir/golden/ 的上游 golden）
├── events/*.jsonl      # 期望输出：ExecEvent 事件流，一行一个事件
└── state/*.json        # 期望输出：ExecState 最终状态
```

| 夹具 | `status` | 步数 | 事件 | 诊断 | 来源 |
| --- | --- | --- | --- | --- | --- |
| `all-ops` | ok | 8 | 17 | 无 | **上游 golden**：`../ir/golden/all-ops.json`，覆盖全部 8 个 op |
| `from-valid-basic` | ok | 2 | 5 | 无 | **上游 golden**：真实链路（`move-forward 1` + `jump`） |
| `from-valid-empty` | ok | 0 | 1 | 无 | **上游 golden**：空程序（0 步，只有起步 `log` 事件） |
| `non-terminating-steps` | limit_exceeded | 10 | 22 | `LB-R002` | **手写 Ir**：40 步直线行走 + `max_steps=10` |
| `non-terminating-wait` | timeout | 1 | 4 | `LB-R003` | **手写 Ir**：`wait 5` + `wait 500` + `timeout_ms=1000` |
| `drop-empty` | runtime_error | 2 | 7 | `LB-R005` | **手写 Ir**：`pick`、`drop`、`drop`（第二次空手） |
| `abort` | aborted | 3 | 8 | `LB-R004` | **手写 Ir**：6 步行走，清单声明 `abort_at_step: 3` |
| `timeout-exact` | ok | 1 | 3 | 无 | **手写 Ir**：`wait 100` = 1000 ms 恰好等于 `timeout_ms` |

- 三个上游 golden 夹具是**真实链路快照**（`../ir/golden/*.json` 由 LB-006 生成）；手写 Ir 夹具是同类
  例外（现有 golden 里凑不出「步数用尽」「超时」「空手 drop」「中断」这些**执行期**形态），它们都是
  合法 §6 文档，测试会先用 `validate_ir` 校验再断言结果。
- **P0 的 IR 没有控制流**（D-016 第 1 条），所以「死循环」在夹具里用两种等价形态表示：
  步骤无限长（`non-terminating-steps`）与一次 `wait` 吃光虚拟时间预算（`non-terminating-wait`）。
  两者都由 `limits` 兜住，实测都在 1 ms 内结束，不会挂死。
- golden 只由命令生成，**不要手改**：

  ```bash
  # 重新生成某个夹具的 golden（改语义表 / 时钟换算后必须重跑，并在 PR 里说明 diff）
  liveblocks exec run --ir fixtures/ir/golden/all-ops.json \
    --events-out fixtures/exec/events/all-ops.jsonl --state-out fixtures/exec/state/all-ops.json

  # 中断夹具要带上清单里的 --abort-at-step
  liveblocks exec run --ir fixtures/exec/ir/abort.json --abort-at-step 3 \
    --events-out fixtures/exec/events/abort.jsonl --state-out fixtures/exec/state/abort.json

  # 跑全部夹具：逐条结果 + 八条验收对照 + 实测耗时（退出码 0 = 全过）
  liveblocks exec report --dir fixtures/exec
  ```

## 8. 约定


1. 夹具必须是**确定性**的：生成脚本固定随机种子，重跑字节一致。
2. 每个夹具都要能追溯到生成它的脚本与提交（在 PR/Issue 中说明）。
3. 单个体积较大的二进制夹具（如整帧图像）入库前先确认仓库体积可接受：当前 `scenes/`
   共 6 张 640×480 灰度 PNG，合计约 0.95 MB。
4. fixtures 只由 `liveblocks synth` 生成，**不要手改**；手改会让 `synth verify` 与
   `tests/test_synth_reproducibility.py` 失败。
