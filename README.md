# 序界 / LiveBlocks

序界（LiveBlocks）是一个「实体卡片编程」方向的实验项目：把带标记的桌面实体卡片放到摄像头前，经图像识别得到卡片身份与排列，解析成语法树（AST）与规范化的 JSON 中间表示，再交给模拟器或真实硬件执行，最后把执行与反馈显示出来。

## 现状

- 已完成 **LB-001 工程骨架**、**LB-002 合成卡片图像生成器**、**LB-003 识别模块**、**LB-004 排列还原**、**LB-005 语法解析**、**LB-006 规范化 IR**、**LB-007 模拟执行器** 与 **LB-008 调试视图**：可以按卡片字典与版式定义生成单卡 / 多卡场景（任意角度、间距、光照噪声、遮挡、裁切）的 ArUco 卡片图像并输出真值 JSON；也可以对这些图跑「ArUco 标记检测 → 卡片字典映射」，输出符合协议 §3 的 `MarkerCandidateList` / `CardCandidateList`，并给出一份可读的准确率 / 漏检统计；再把候选还原成符合协议 §4 的 `Layout`（行/槽位、阅读方向、朝向归一、去重与歧义诊断），最后按 P0 最小文法（语句序列 + 字面参数）把 `Layout` 读成符合协议 §5 的 `Ast`（每个节点带 `span` 与 `origin`，语法错误报 `LB-P0xx` 并定位到行/槽位/卡片），再把 `Ast` 规范化成符合协议 §6 的 `Ir`（确定性输出、显式 `limits`、`op` 按 op 表校验，问题报 `LB-I0xx` 并通过入库 JSON Schema 校验），最后用**模拟执行器**把 `Ir` 跑成协议 §7 的 `ExecEvent[]` + `ExecResult`（虚拟时钟、来自 IR 的步数/超时限制、步骤边界中断，事件一律 `backend:"sim"`），并附一份可读的最终状态 JSON。
- **LB-008 调试视图**可以把一份 §8 运行记录（JSONL）渲染成**一屏五层**：帧叠加（原帧 + 标记框 / 卡片中心 / 槽位）、排列、AST、IR、事件流 + 分层耗时，支持**回放**与**单步**；坏记录只给可见的 `LB-U0xx` 提示而不崩溃。它**只读**、只服务开发者，不做产品级 UI、不对外发布。
- **LB-009 端到端回归**把整条软件闭环收成一条命令：`liveblocks e2e run` 真跑「生成 → 识别 → 排列 → 解析 → IR → 模拟」六阶段并把 §8 运行记录落盘，`liveblocks e2e regression` 再做记录形状校验、**黄金基准**比对、**破坏注入自检**与**固定时间上界**（整份 ≤ 60 s、单条记录 ≤ 20 s），失败时打印失败摘要并点名「最早失败层」。运行记录的生产在包内完成，不依赖 LB-008 为夹具拼记录的脚本。
- **LB-030 摄像头采集台**把「真实帧从哪来」这件事先接上：一条命令（`liveblocks serve-cam serve`）起本地服务，页面用浏览器 `getUserMedia` 选任意摄像头（手机前置/后置、笔记本内置、USB UVC），支持**单帧抓拍**与**连续取帧**；`POST /api/frame` 返回 §3 `CardCandidateList` 与识别叠加图，`POST /api/capture` 把当前帧与人工标注（卡片 id、角色、可选角点）落成 `fixtures/live/<时间戳>/`——**这份目录能被 `recognize report` 直接消费**；页面能标「这帧识别错了」，坏样本单独归档到 `fixtures/live/refused/`。零新增依赖（标准库 `wsgiref` + 内联 CSS/JS 的单文件页面）。
- **LB-031 标定（算法侧）**把「物理世界 → 图像」这一层接上：`liveblocks calibrate report` 一条命令在**合成畸变场景**（真值内参已知）上跑完「棋盘格 / ChArUco 标定 → 重投影误差 → 校正前后角点重复性（均值 / 最大值，像素）」，退出码 0；标定结果写成带内参、`plumb_bob` 畸变、重投影误差、图像尺寸、标定图数量与时间戳的 JSON，并能填进 `Frame.intrinsics/distortion`；合成器也多了**镜头畸变模型**（径向 + 切向），按真值内参渲染带畸变的场景作为可复现回归数据。真实摄像头的标定数字**不在验收内**（`docs/CALIB.md` §8 写了人怎么验）。
- **LB-032 实体卡片规范与标记页**把「卡片本身」这件实体前提写成草案并落成可打印的产物：`docs/CARD-SPEC.md` 覆盖卡面尺寸、标记尺寸、静区、基材、打印工艺、批次编号与质检项，`liveblocks cardsheet render` 生成 A4 标记页（卡面 + 静区 + 裁切线 + 卡片编号 + 朝向标记，PNG 或 PDF），`liveblocks cardsheet verify` 把页面渲染成图再识别，逐卡核对 id / 朝向 / 位置并核对「同参数两次字节一致」，`liveblocks cardsheet stencil` 给没打印机时的手绘降级版。规范里每个数字都能追到卡片字典 / ArUco 位表 / 卡面版式 / 静区规则，追不到的显式标「待实测」；**实体卡打印、基材、打印工艺、识别距离 = 待人工验证**（`docs/CARD-SPEC.md` §11 写了人怎么验）。
- **模拟通过 ≠ 硬件验证**：识别与排列只在**合成图**上验证过，真实摄像头与实体卡片尚未验证（见 `docs/PROGRESS.md` §3）。LB-030 的离线验收（§3 逐字段一致、采集目录进 `recognize report`、页面无外网依赖）有真跑证据；但「手机后置摄像头 + 实体卡看到叠加框与卡片 id」这一条**待人工验证**（`docs/CAPTURE.md` §6 写了人怎么验）。
- 首阶段目标是**纯软件闭环**：用合成的卡片图像代替摄像头、用模拟执行代替硬件，先把「识别 → 排列 → 解析 → 执行 → 反馈/调试」的整条链路和接口契约定下来。
- 硬件（摄像头、主机、屏幕、实体卡片、电源与引脚）尚未选型确认，相关结论均为**待验证**。
- 实现语言按 `docs/DECISIONS.md` D-011 暂定 Python 3.11+ / pytest（D-004 仍为提案，换语言只需替换 `pyproject.toml` 与 `scripts/check.sh`）。

更细的状态与证据见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 快速开始（工程骨架）

在仓库根目录依次执行以下命令（需要 Python 3.11 或更高版本）：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

跑全量测试（一条命令，输出用例数 ≥ 1）：

```bash
python -m pytest
```

跑本地检查（与 CI 同一套检查：语法编译检查 + 全量测试，含合成卡片生成器、fixtures 可复现性与识别模块）：

```bash
bash scripts/check.sh
```

查看命令行入口（`--version`；业务子命令见下一节的 `synth`）：

```bash
liveblocks --version
```

预期：`python -m pytest` 与 `bash scripts/check.sh` 均以退出码 0 结束；让任一断言失败时，两者以非零码退出（这正是它们在 CI 中拦回归的方式）。

CI：`.github/workflows/ci.yml` 在 PR 与 `main` 的 push 上对 Python 3.11 / 3.13 各跑一次 `bash scripts/check.sh`。无法使用 GitHub Actions 时，本地执行同一条命令即为等价检查。

## 合成卡片图像生成器（LB-002）

按 `src/liveblocks/data/` 里的**卡片字典**（16 张卡 → ArUco 标记 + 卡面语义）与**版式定义**
（卡面尺寸/标记位置/静区/标签 + 场景预设）生成合成图，每张图配一份真值 JSON
（卡片 id、角点、位姿、可见比例）。**不做识别，也不评估识别效果。**

```bash
# 看有哪些卡片、卡面版式与场景预设
liveblocks synth list

# 用固定种子生成全部场景到 fixtures/scenes（图像 + 真值 + index.json）
python -m liveblocks synth generate --scene all --out fixtures/scenes

# 只生成一个场景到临时目录
python -m liveblocks synth generate --scene overlap --seed 7 --out /tmp/scenes

# 校验入库 fixtures 与「用记录种子重新生成」的结果是否逐字节一致
python -m liveblocks synth verify --dir fixtures/scenes
```

入库的 6 个场景（`fixtures/scenes/`）：`normal`（正常）、`slight-rotation`（轻微旋转）、
`overlap`（重叠）、`clipped`（裁切）、`low-light`（低光照）、`multi-row`（多卡多行）。
字段说明与重新生成规则见 [fixtures/README.md](fixtures/README.md)，设计取舍见
[docs/DECISIONS.md](docs/DECISIONS.md) D-012。

运行时**零依赖**（只用标准库）：PNG 编码、ArUco 位表与点阵字体都在包里。可选装
`python -m pip install -e '.[dev,aruco]'` 让测试用 OpenCV 逐位核对入库的 ArUco 位表
（CI 只装 `.[dev]`，该交叉核对用例会自动跳过）。

## 识别模块（LB-003）

把一张灰度图变成「卡片候选」：局部自适应二值化 → 暗区连通域 → 凸包化简成四边形 → 亚像素边缘
细化 → 6×6 模块采样与 `DICT_4X4_50` 码字比对 → 卡片字典映射与状态判定（`ok` / `clipped` /
`occluded` / `ambiguous` / `unknown_card`）。**不做排序、不解析、不执行**（那是 LB-004 起的活）。

```bash
# 单张图识别，直接打印协议 §3.2 的 CardCandidateList JSON
liveblocks recognize detect --image fixtures/scenes/normal.png

# 输出标记层（§3.1）；给了内参才会附上位姿（粗值，小标记不输出）
liveblocks recognize detect --image fixtures/scenes/normal.png --emit markers --intrinsics 700,700,320,240

# 换一份少了某张卡的字典 → 该标记被标成 unknown_card
liveblocks recognize detect --image fixtures/scenes/normal.png --card-dictionary /tmp/cards-without-C01.json

# 对全部 fixtures 跑识别，打印准确率 / 漏检统计（含验收对照）
liveblocks recognize report --dir fixtures/scenes
```

当前实测（`fixtures/scenes`，6 个场景 17 张卡）：识别 15、未识别 1、漏检 1、误检 0，
召回率 88.2%、精确率 100%；「正常」样例无漏检、「裁切」样例 2/2 标 `clipped`。唯一漏检与唯一
未识别都出现在 `overlap`（纸片压住一张卡的标记、另一张卡的标记被后画的卡盖住），如实计入统计而
不做粉饰。取舍与已知限制见 [docs/DECISIONS.md](docs/DECISIONS.md) D-013/D-014 与
[docs/PROGRESS.md](docs/PROGRESS.md) §4。

运行时仍然**零依赖**：检测与译码只用到标准库（自带 4 点单应、连通域、码字位表）。可选装
`python -m pip install -e '.[dev,aruco]'` 让测试用 OpenCV 的 ArUco 检测器当独立实现交叉核对我们
的检出与角点（CI 只装 `.[dev]`，这组用例自动跳过）。

## 排列层（LB-004）

把识别层的卡片候选还原成「谁在第几行第几个位置」的 `Layout`（协议 §4）：**分簇**（按阅读方向的
换行轴切行）、**排序**（行自上而下、行内按阅读方向）、**朝向归一**（折算成相对阅读方向的正立
方向）、**去重**（同一身份的重复检出只留置信度最高的一个）与**歧义诊断**（间距过小的相邻槽位
报 `LB-L001`，行分组边界存疑报 `LB-L004`，卡片放歪报 `LB-L005`）。**不解析语法**（那是 LB-005）。

```bash
# 卡片候选夹具 → Layout JSON（协议 §4）
liveblocks layout arrange --candidates fixtures/layouts/multi-row-cards.json

# 一步「识别 → 排列」（调试用；先识别再排列）
liveblocks layout from-image --image fixtures/scenes/multi-row.png --card-layout card-40x40

# 对全部候选夹具跑排列，打印行/槽位/诊断与验收对照
liveblocks layout report --dir fixtures/layouts
```

当前实测（`fixtures/layouts`，4 个候选夹具）：两行样例的 6 张卡被正确分成 2×3；间距歧义样例产出
1 条 `LB-L001`（点名 c-1、c-2）；重复候选样例去重后留 3 槽位并产出 `LB-L002`（点名被移除的 c-1）；
每个槽位都带来源 `candidate_id`，没有一个候选既没进槽位也没被诊断点名。取舍与已知限制见
[docs/DECISIONS.md](docs/DECISIONS.md) D-015 与 [docs/PROGRESS.md](docs/PROGRESS.md) §4。

## 语法解析（LB-005）

把排列层输出的 `Layout` 读成语法树 `Ast`（协议 §5）：P0 只做**语句序列 + 字面参数**——一行卡片就是
一条语句（第 0 个槽位必须是命令卡，后面跟字面数字卡当参数），整份 `Layout` 就是一个 `Program`。
每个节点都带 `span`（行 + 槽位区间的闭区间）与 `origin`（来源卡片），因此报错能指回实体卡片。
语法问题报 `LB-P001`…`LB-P005`（缺命令卡 / 参数位不是字面卡 / 空槽位或未知卡面 / 控制流卡超出
P0 / 运算符卡超出 P0），策略是**整行丢弃、只报第一条**、其余行照常解析，所以非法输入也得到部分
AST 而不会崩。**不求值、不执行**（那是 LB-006/LB-007 的活）。

```bash
# Layout JSON（协议 §4）→ Ast JSON（协议 §5）
liveblocks parse ast --layout fixtures/ast/valid-basic.json

# 非法样例：诊断随 Ast 输出（退出码 0）；加 --fail-on-error 让它变成非零退出码
liveblocks parse ast --layout fixtures/ast/invalid-control-flow.json --fail-on-error

# 对全部语法夹具跑一遍，打印逐条结果与验收对照（退出码 0 = 三条验收全过）
liveblocks parse report --dir fixtures/ast
```

当前实测（`fixtures/ast`，8 个夹具 = 3 合法 + 5 非法）：3 条合法语句样例都通过 §5 校验（`valid-basic`
3 语句 / 7 节点；`valid-empty` 空程序；`from-overlap-cards` 是 LB-004 的真实输出）；5 个非法夹具覆盖
全部 5 个 `LB-P0xx` 码，每条诊断都带 `span` 与 `origin`，解析不抛异常。文法定义本身在
[docs/GRAMMAR.md](docs/GRAMMAR.md)（草案 `grammar_version` 0.0.1），取舍、`span`/`origin` 约定与
`PROTOCOL.md` §5/§9 的缺口清单见 [docs/DECISIONS.md](docs/DECISIONS.md) D-016 与
[docs/PROGRESS.md](docs/PROGRESS.md) §4。

## 规范化 IR（LB-006）

把语法树 `Ast` 折成**确定性**的中间表示 `Ir`（协议 §6）：一条 `Command` = 一步 `step`（`op` 取卡面、
参数折成 `{"<参数名>": <整数>, "unit": "<单位>"}`、来源写 `{"row", "slot"}`、`step_id` 密集编号
`s0`/`s1`/…），`limits` 在规范化阶段就显式写出来（缺省 `max_steps` 1000 / `timeout_ms` 5000），
`op` 与元数按 op 表校验（未知 `op` 报 `LB-I001`、元数不符报 `LB-I002`、语句节点不受支持 `LB-I003`、
参数不是字面量 `LB-I004`），产出的 `Ir` 通过入库的 JSON Schema 校验。**不执行**（那是 LB-007）。

```bash
# Ast JSON（协议 §5）→ Ir JSON（协议 §6）；stdout 可直接管道给下游
liveblocks ir normalize --ast fixtures/ir/asts/all-ops.json

# 显式覆盖 limits（覆盖值会写进 IR）
liveblocks ir normalize --ast fixtures/ir/asts/all-ops.json --max-steps 50 --timeout-ms 1200

# 出现 LB-I0xx 时诊断走 stderr、退出码仍为 0；加 --fail-on-error 让它变成 1
liveblocks ir normalize --ast fixtures/ir/asts/unknown-op.json --fail-on-error

# 对全部 IR 夹具跑一遍：两次规范化比字节、与 golden 逐字节比、schema 校验 + 验收对照
liveblocks ir report --dir fixtures/ir
```

当前实测（`fixtures/ir`，8 个夹具 = 3 个上游快照 + 5 个手写）：8 份 Ir 全部通过 schema 校验与语义校验，
两次规范化（含不同 `PYTHONHASHSEED` 的子进程）字节一致，且与入库 golden 逐字节一致；`all-ops` 覆盖
op 表全部 8 个命令卡面（8 步、0 诊断）；4 个 `LB-I0xx` 码都有真实触发路径。IR 规范本身在
[docs/IR.md](docs/IR.md)（草案 `ir_version` 0.0.1），取舍与 `PROTOCOL.md` §6/§9 的缺口清单见
[docs/DECISIONS.md](docs/DECISIONS.md) D-017 与 [docs/PROGRESS.md](docs/PROGRESS.md) §4。

## 模拟执行（LB-007）

把 `Ir` 跑成**事件流 + 运行结果 + 最终状态**：模拟后端只在**虚拟时钟**上走（1 tick = 10 ms），不睡真实
时间；`max_steps` / `timeout_ms` 只读 `Ir.program.limits`（LB-006 已让它们显式出现）；中断只在**步骤
边界**生效，有 `request_abort()`（线程安全）与 `should_abort` 回调两条入口。事件一律标 `backend:"sim"`，
**不得当作硬件验证证据**。

```bash
# 跑一份 Ir：stdout 打印摘要 + 最终状态 JSON（ExecState）
liveblocks exec run --ir fixtures/ir/golden/all-ops.json

# 事件流落 JSONL（§8 风格）、结果与状态各存一份
liveblocks exec run --ir fixtures/ir/golden/all-ops.json \
  --events-out /tmp/events.jsonl --result-out /tmp/result.json --state-out /tmp/state.json

# 确定性中断：执行到第 3 步之前停下（status=aborted，LB-R004）
liveblocks exec run --ir fixtures/exec/ir/abort.json --abort-at-step 3

# 对全部执行夹具真跑一遍：逐条结果 + 八条验收对照 + 实测耗时
liveblocks exec report --dir fixtures/exec
```

当前实测（`fixtures/exec`，8 个夹具 = 3 个上游 golden IR + 5 个手写 IR）：事件流 `seq` 严格递增、
`backend` 全为 `sim`；`max_steps` 用尽 → `limit_exceeded`（`LB-R002`）、虚拟耗时超限 → `timeout`
（`LB-R003`）；`--abort-at-step 3` → `aborted`（`LB-R004`）；空手 `drop` → `runtime_error`（`LB-R005`）；
恰好用满 `timeout_ms` 仍判 `ok`；8 个夹具真跑一遍合计约 9 ms（单夹具上限 250 ms），事件流与最终状态
与入库 golden 逐字节一致。执行层规范在 [docs/EXEC.md](docs/EXEC.md)（草案 `exec_version` 0.0.1），
取舍与 `PROTOCOL.md` §7/§9 的缺口清单见 [docs/DECISIONS.md](docs/DECISIONS.md) D-018 与
[docs/PROGRESS.md](docs/PROGRESS.md) §4。

## 调试视图（LB-008）

把一份 **§8 运行记录（JSONL）** 渲染成**一屏五层**的调试视图：① 帧叠加（原帧上画标记框、
卡片中心、排列槽位）② 排列（行/槽位表）③ AST（树）④ IR（`limits` + 逐步表，当前步高亮）
⑤ 事件流（`seq`/`at_ms`/`phase`/`detail` + 世界状态 + 收尾结论）+ 分层耗时。界面**只读**：
不改记录、不改业务数据，也不重新跑业务层。

```bash
# 文本形态（默认打印到标准输出）；--step 3 单步到第 3 条事件之后
liveblocks ui view --record fixtures/runs/sample-clipped.jsonl --step 3

# 自包含 HTML（无外链、无脚本；直接浏览器打开，也是截图证据的来源）
liveblocks ui view --record fixtures/runs/sample-clipped.jsonl --html-out /tmp/lb-008-view.html

# 帧叠加面板单独导出成 PNG（仓库自带编码器，字节可复现）
liveblocks ui view --record fixtures/runs/sample-clipped.jsonl --overlay-out /tmp/lb-008-overlay.png

# 回放：打印时间线，并核对「同一记录回放两次一致」
liveblocks ui replay --record fixtures/runs/sample-clipped.jsonl

# 对 fixtures/runs 跑一遍：逐夹具结论 + Issue #20 四条验收对照 + 截图证据核对
liveblocks ui report --dir fixtures/runs
```

- 样例记录：`fixtures/runs/sample-clipped.jsonl`（真实全链路，1 步 / 3 条事件）、
  `fixtures/runs/sample-multi-row.jsonl`（两行六卡，排列 + 语法诊断）、
  `fixtures/runs/sample-all-ops.jsonl`（只有执行层的局部记录，8 步 / 17 条事件，用来演示多步回放）；
  损坏记录在 `fixtures/runs/corrupt/`（坏 JSON、缺字段、无 `RunHeader`、未知 `kind`、坏帧引用、坏事件流）。
- 截图证据（**真渲染**：`scripts/screenshot_view.py` 把视图 HTML 交给 WebKitGTK 渲染，不是 mock-up）：
  `docs/evidence/lb-008-debug-view.png`、`docs/evidence/lb-008-debug-view-step0.png`、
  `docs/evidence/lb-008-debug-view-all-ops.png`、`docs/evidence/lb-008-corrupt-record.png`。
- 界面规范与 `LB-U0xx` 码表见 [docs/UI.md](docs/UI.md)（草案 `ui_version` 0.0.1），
  取舍与 `PROTOCOL.md` §8/§9 的缺口清单见 [docs/DECISIONS.md](docs/DECISIONS.md) D-019。

## 端到端回归与运行记录（LB-009）

一条命令跑全链路（**生成**合成卡片图 → **识别** → **排列** → **解析** → **IR** → **模拟执行**），
把 §8 运行记录写成 JSONL：首行 `RunHeader`（含帧引用、生成层证据与六阶段实测耗时），
随后 `MarkerCandidateList` / `CardCandidateList` / `Layout` / `Ast` / `Ir` / `ExecEvent`* / `ExecResult`。

```bash
# 一条命令跑全链路（默认全部场景），记录落到 runs/e2e（不入库：耗时是实测值）
liveblocks e2e run --scene normal --out runs/e2e

# 全场景回归：记录形状 + 黄金基准 + 破坏注入自检 + 固定时间上界；退出码 0 = 全过
liveblocks e2e regression --dir fixtures/e2e --out runs/e2e

# 手工复现「破坏一个 fixture → 回归明确失败并指出层级」
liveblocks e2e regression --dir fixtures/e2e --scene normal --corrupt layout

# 记录可以直接交给调试视图打开
liveblocks ui view --record runs/e2e/normal.jsonl
```

当前实测（`fixtures/e2e`，6 个入库场景）：六个阶段全部真跑，记录 8–10 行/份，共 50 行；
生成层的 PNG 与真值跟 `fixtures/scenes/*` **逐字节相同**（同种子 → 同字节）；
整份回归约 9–11 秒（上界 60 秒，瓶颈是生成层的纯 Python 光栅化）。
黄金基准 `fixtures/e2e/baseline.json` 只记「每层文档的 canonical JSON + sha256 指纹、`kind` 序列、
行数与关键计数」，**不记**实测耗时，因此重跑 `--update-baseline` 得到逐字节相同的文件。
规范与 `LB-E0xx` 码表见 [docs/E2E.md](docs/E2E.md)（草案 `e2e_version` 0.0.1），
取舍与 `PROTOCOL.md` §8/§9 的缺口清单见 [docs/DECISIONS.md](docs/DECISIONS.md) D-020。

## 摄像头采集台（LB-030）

真实帧的来源：一条命令起本地服务，页面用浏览器 `getUserMedia` 选任意摄像头（手机前置/后置、
笔记本内置、USB UVC 走同一条路径），单帧抓拍或连续取帧，识别在服务端跑（零新增依赖）。

```bash
# 一条命令起服务（默认只绑 127.0.0.1:8765），然后用浏览器打开打印出来的地址
liveblocks serve-cam serve

# 换端口 / 换采集根目录 / 指定默认卡纸版式
liveblocks serve-cam serve --port 9000 --card-layout card-60x40

# 只打印地址与采集目录就退出（脚本/自检用，不绑端口）
liveblocks serve-cam serve --print-url-only

# 把页面导成单个 HTML：断网也能打开（自带 CSS/JS，无外链、无 CDN）
liveblocks serve-cam page --out /tmp/lb030-page.html

# 采到的样本目录就是场景目录，直接喂给识别统计
liveblocks recognize report --dir fixtures/live/<时间戳>
```

- `POST /api/frame` 返回的 `card_candidate_list` 与 `liveblocks recognize detect` 命令行的输出
  **逐字段一致**（同一段识别代码、同一 `frame_id` 规则），叠加图 PNG 与原帧同尺寸；
- `POST /api/capture` 落成 `fixtures/live/<时间戳>/`（`frame.png` + `scene.json` + `capture.json`
  + `index.json`），`scene.json` 的 `kind` 是 `LiveCaptureScene`，与合成真值字段同构；
- 页面上的「这帧识别错了」把样本归到 `fixtures/live/refused/`，坏样本与正常样本分开跑；
- 不做：视频录制与上传、多用户与账号、公网鉴权（隧道侧负责）、不改 `docs/PROTOCOL.md`。

验收状态（如实）：① ② ③ 离线可复现（`pytest tests/test_serve_cam.py`），**④ 手机后置摄像头 +
实体卡 = 待人工验证**。规范与错误码见 [docs/CAPTURE.md](docs/CAPTURE.md)（草案
`capture_version` 0.0.1），取舍见 [docs/DECISIONS.md](docs/DECISIONS.md) D-021。

## 标定与畸变校正（LB-031）

合成场景上的标定全流程：棋盘格（9×6 / 25 mm）与 ChArUco（8×6 / 20 mm / `DICT_4X4_50`）两条流程、
标定文件 JSON、`Frame` 的内参与畸变填充路径，外加合成器的**镜头畸变模型**（径向 `k1,k2` + 切向 `p1,p2`）。

```bash
# 一条命令复现全部验收数字（合成场景，约 36 秒）：重投影误差 + 校正前后角点重复性
liveblocks calibrate report --preset all --golden fixtures/calib

# 看一帧的角点检测（棋盘格 / ChArUco 两条流程）
liveblocks calibrate corners --image fixtures/scenes/normal.png --target checkerboard-9x6-25mm

# 用目录里的图像标定（真实采集走这条；人来做，见 docs/CALIB.md §8）
liveblocks calibrate run --dir fixtures/live/<时间戳> --target checkerboard-9x6-25mm --out calib.json

# 把标定填进帧、或把图校正成理想针孔图
liveblocks calibrate frame --calib calib.json --image frame.png
liveblocks calibrate undistort --calib calib.json --image frame.png --out frame-fixed.png

# 校验一份标定文件
liveblocks calibrate validate --calib calib.json
```

实测（合成场景，`--preset all` 的真跑输出）：

- 棋盘格预设：重投影 **RMS 0.109 px**（最大 0.629 px），10 张标定图 / 400 个角点；
  估计内参 `fx=459.618 fy=461.579 cx=239.224 cy=179.784`（真值 `460 / 462 / 239.5 / 179.5`）；
  同一静态场景连拍 4 次的角点重复性：校正前 0.021 / 0.191 px → 校正后 0.023 / 0.206 px（均值 / 最大值）；
  单个针孔单应的拟合残差（校正是否有效）：0.800 px → 0.095 px。
- ChArUco 预设：重投影 **RMS 0.113 px**（最大 0.490 px），9 张标定图 / 314 个角点，
  标记 id 与棋盘编号逐一对上（每帧 10~22 个）。
- **重复性不会因为校正而变好**——它由检角算法与噪声决定；校正真正改善的是「画面能被针孔模型解释」。
  报告把两组数字都印出来（口径见 `docs/CALIB.md` §7.6）。
- 不做：不冻结摄像头型号、不声明任何真实设备的重投影误差/可用距离、不做立体或手眼标定；
  真实摄像头的标定数字 = **待人工验证**（`docs/CALIB.md` §8 给了人工步骤）。

规范与格式见 [docs/CALIB.md](docs/CALIB.md)（草案 `0.0.1`），取舍见
[docs/DECISIONS.md](docs/DECISIONS.md) D-022。

## 实体卡片规范与标记页（LB-032）

卡面尺寸、标记尺寸、静区、基材、打印工艺、批次编号与质检项的规范草案，加上一个把这份规范
落成 A4 标记页的生成器（零新增依赖：光栅、PNG、PDF 全部标准库）：

```bash
# 生成标记页：PNG + PDF + 清单（清单 = 这一页应该长什么样的机器可读声明）
liveblocks cardsheet render --out sheet.png --pdf sheet.pdf --manifest sheet.json

# 验收：渲染 → 重新识别 → 逐卡核对 id / 朝向 / 位置 → 转 90°/180°/270° 再核对 → 确定性
liveblocks cardsheet verify

# 手绘降级版：每张卡的 8×8 模块格子印成文字（没打印机时照着抄）
liveblocks cardsheet stencil --out stencil.txt

# 规范里每个数字的出处（可追溯 / 待实测），顺带核对文档与实现是否一致
liveblocks cardsheet spec --doc docs/CARD-SPEC.md
```

真跑（默认参数：A4、`card-60x40` 版式、4 px/mm、批次 `lb-b0001`）：

- 标记页 840×1188 px（4 px/mm ≈ 102 dpi）：3 行位 × 6 列位、单页容量 18、16 张卡一页；
- 把页面渲染成图再识别：**16/16 张卡的 id 全读出**、`status` 全 `ok`、未登记候选 0 个；朝向读数
  最大偏差 0.00°；卡片中心与清单卡槽中心的偏差 0.000 px（只说明版面几何自洽，不是识别精度）；
- 把同一页**顺时针转 90°/180°/270°** 再识别：16 张卡仍然全中，角读数按 −90°×圈数偏移（最大偏差
  0.00°）——这条是为了排除「朝向永远读成 0°」的假通过；
- **确定性**：同参数两次渲染的 PNG（8920 字节）、PDF（9389 字节）与清单逐字节一致；产物入库
  `fixtures/sheet/`，`tests/test_sheet_cli.py` 逐字节比对；
- **数字纪律**：规范里每个数字都能追到卡片字典 / ArUco 位表 / 卡面版式 / 静区规则（`liveblocks
  cardsheet spec` 打印整张溯源表），追不到的——页边距、裁切线宽、渲染分辨率这类排版参数，以及
  基材、打印工艺、可识别距离——显式标「待实测」；`tests/test_sheet_docs.py` 双向核对并含反向验证；
- **静区差异如实写**：卡面版式声明的静区 2.5 mm **小于**静区规则 4 mm（1 个模块），合成图上不影响
  识别，实体卡上够不够用 = **待实测**；
- 不做：不声称任何打印工艺 / 基材 / 可识别距离的实测结论，不替用户决定卡面美术，不改
  `docs/ROADMAP.md` 的范围与优先级；**实体卡打印、静区、打印质量、识别距离 = 待人工验证**。

规范与 `LB-S0xx` 码表见 [docs/CARD-SPEC.md](docs/CARD-SPEC.md)（草案 v0.0.1），取舍见
[docs/DECISIONS.md](docs/DECISIONS.md) D-023。

## 文档入口

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 端到端链路、模块边界、数据契约位置、待决事项 | 基线草案 |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | 层间数据格式（识别 / 排列 / AST / IR / 执行反馈），**草案，未冻结** | 草案 v0.0.1 |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 待验证的硬件选型、电源与引脚待确认项、卡片制作 | 待验证 |
| [docs/GRAMMAR.md](docs/GRAMMAR.md) | P0 文法定义（产生式、卡面角色、AST 形状、`LB-P0xx` 码表），**草案** | 草案 v0.0.1（`grammar_version` 0.0.1） |
| [docs/IR.md](docs/IR.md) | P0 规范化 IR 规范（op 表、`args`/`limits`/`step_id` 约定、`LB-I0xx` 码表、schema），**草案** | 草案 v0.0.1（`ir_version` 0.0.1） |
| [docs/EXEC.md](docs/EXEC.md) | S6 模拟执行层规范（虚拟时钟、op 语义表、limits 判定顺序、中断、`LB-R0xx` 码表、`ExecState`），**草案** | 草案 v0.0.1（`exec_version` 0.0.1） |
| [docs/UI.md](docs/UI.md) | S7b 调试视图规范（一屏五层、运行记录要求、回放与单步、`LB-U0xx` 码表），**草案** | 草案 v0.0.1（`ui_version` 0.0.1） |
| [docs/E2E.md](docs/E2E.md) | 端到端回归规范（六阶段链路、§8 记录的生产、黄金基准、失败摘要、固定时间上界、`LB-E0xx` 码表），**草案** | 草案 v0.0.1（`e2e_version` 0.0.1） |
| [docs/CAPTURE.md](docs/CAPTURE.md) | 摄像头采集台规范（`getUserMedia` 页面、`/api/frame` / `/api/capture` / `/api/captures`、`fixtures/live/` 落盘结构、`LB-C0xx` 码表、人工验证步骤），**草案** | 草案 v0.0.1（`capture_version` 0.0.1） |
| [docs/CARD-SPEC.md](docs/CARD-SPEC.md) | 实体卡片规范（卡面 / 标记 / 静区 / 基材 / 打印工艺 / 批次编号 / 质检项）+ A4 标记页生成器、`LB-S0xx` 码表、待实测与人工验证步骤，**草案** | 草案 v0.0.1（`sheet_version` 0.0.1） |
| [docs/CALIB.md](docs/CALIB.md) | 标定与畸变校正规范（棋盘格 / ChArUco 流程、标定文件 JSON、`Frame` 填充路径、`LB-B0xx` 码表、待人工验证步骤），**草案** | 草案 v0.0.1 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 按优先级与依赖排列的 Backlog，可直接拆成 Issue | 基线草案 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 已定决策与待决问题记录（ADR 式） | 持续更新 |
| [docs/PROGRESS.md](docs/PROGRESS.md) | 已验证 / 计划中 / 待硬件验证 三态进度与验证证据 | 持续更新 |
| [AGENTS.md](AGENTS.md) | 人机协作规则：Issue、分支与 PR、验证证据、失败与阻塞处理 | 生效 |

## 工作方式

- 每个任务对应一个 GitHub Issue、一个分支、一个 PR；人工合并，不做自动合并。
- 首版人工测试与反馈入口就是现有 GitHub Issues，使用 `.github/ISSUE_TEMPLATE/hardware-test.md` 模板提交。
- 协作细则见 [AGENTS.md](AGENTS.md)。

## 目录约定（骨架）

```
.
├── README.md
├── AGENTS.md
├── pyproject.toml        # 依赖声明、包元数据、pytest 配置
├── .github/
│   ├── ISSUE_TEMPLATE/hardware-test.md
│   └── workflows/ci.yml  # PR 上跑 bash scripts/check.sh
├── src/liveblocks/       # 实现包
│   ├── __init__.py
│   ├── __main__.py       # python -m liveblocks
│   ├── cli.py            # liveblocks --version / synth … / recognize … / layout … / parse … / ir … / exec … / e2e … / ui … / serve-cam …
│   ├── data/             # 卡片字典、ArUco 位表、版式定义、点阵字体（JSON）
│   ├── synth/            # LB-002 合成卡片图像生成器（不做识别）
│   ├── vision/           # LB-003 识别模块（标记候选 → 卡片候选、统计）
│   ├── layout/           # LB-004 排列层（卡片候选 → Layout、自检报告）
│   ├── parser/           # LB-005 语法层（Layout → Ast、P0 文法定义与自检报告）
│   ├── ir/               # LB-006 规范化层（Ast → Ir、op 表、schema 校验与自检报告）
│   ├── runtime/          # LB-007 模拟执行层（Ir → ExecEvent[] + ExecResult、虚拟时钟、limits、中断）
│   ├── calib/            # LB-031 标定（角点检测 / 求解 / 标定文件 / Frame 填充 / 验收报告）
│   └── serve_cam/        # LB-030 摄像头采集台（WSGI 服务 + 自包含页面 + fixtures/live 落盘）
├── tests/                # pytest 用例，testpaths = tests
├── fixtures/             # 测试夹具：skeleton.json + scenes/（合成图像与真值）+ layouts/（卡片候选）+ ast/（语法夹具）+ ir/（规范化夹具与 golden）+ exec/（执行夹具与 golden 事件流/最终状态）+ runs/ + e2e/ + live/（LB-030 实拍样本，不入库）+ calib/（LB-031 标定文件 golden）
├── scripts/check.sh      # 本地检查脚本，与 CI 同一条命令
└── docs/                 # 架构、协议草案、硬件、路线图、决策、进度
```

新增业务模块时应遵守：源码进 `src/liveblocks/`，用例进 `tests/`，夹具进 `fixtures/`，并且 `bash scripts/check.sh` 通过。
