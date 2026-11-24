# 序界 / LiveBlocks

序界（LiveBlocks）是一个「实体卡片编程」方向的实验项目：把带标记的桌面实体卡片放到摄像头前，经图像识别得到卡片身份与排列，解析成语法树（AST）与规范化的 JSON 中间表示，再交给模拟器或真实硬件执行，最后把执行与反馈显示出来。

## 现状

- 已完成 **LB-001 工程骨架**、**LB-002 合成卡片图像生成器**、**LB-003 识别模块**、**LB-004 排列还原**与 **LB-005 语法解析**：可以按卡片字典与版式定义生成单卡 / 多卡场景（任意角度、间距、光照噪声、遮挡、裁切）的 ArUco 卡片图像并输出真值 JSON；也可以对这些图跑「ArUco 标记检测 → 卡片字典映射」，输出符合协议 §3 的 `MarkerCandidateList` / `CardCandidateList`，并给出一份可读的准确率 / 漏检统计；再把候选还原成符合协议 §4 的 `Layout`（行/槽位、阅读方向、朝向归一、去重与歧义诊断），最后按 P0 最小文法（语句序列 + 字面参数）把 `Layout` 读成符合协议 §5 的 `Ast`（每个节点带 `span` 与 `origin`，语法错误报 `LB-P0xx` 并定位到行/槽位/卡片）。
- 仓库**还没有 IR 规范化、执行、界面**（LB-006 起），因此目前不能说「能执行」「能显示」。识别与排列只在**合成图**上验证过，真实摄像头与实体卡片尚未验证（见 `docs/PROGRESS.md` §3）。
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

## 文档入口

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 端到端链路、模块边界、数据契约位置、待决事项 | 基线草案 |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | 层间数据格式（识别 / 排列 / AST / IR / 执行反馈），**草案，未冻结** | 草案 v0.0.1 |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 待验证的硬件选型、电源与引脚待确认项、卡片制作 | 待验证 |
| [docs/GRAMMAR.md](docs/GRAMMAR.md) | P0 文法定义（产生式、卡面角色、AST 形状、`LB-P0xx` 码表），**草案** | 草案 v0.0.1（`grammar_version` 0.0.1） |
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
│   ├── cli.py            # liveblocks --version / synth … / recognize … / layout … / parse …
│   ├── data/             # 卡片字典、ArUco 位表、版式定义、点阵字体（JSON）
│   ├── synth/            # LB-002 合成卡片图像生成器（不做识别）
│   ├── vision/           # LB-003 识别模块（标记候选 → 卡片候选、统计）
│   ├── layout/           # LB-004 排列层（卡片候选 → Layout、自检报告）
│   └── parser/           # LB-005 语法层（Layout → Ast、P0 文法定义与自检报告）
├── tests/                # pytest 用例，testpaths = tests
├── fixtures/             # 测试夹具：skeleton.json + scenes/（合成图像与真值）+ layouts/（卡片候选）+ ast/（语法夹具）
├── scripts/check.sh      # 本地检查脚本，与 CI 同一条命令
└── docs/                 # 架构、协议草案、硬件、路线图、决策、进度
```

新增业务模块时应遵守：源码进 `src/liveblocks/`，用例进 `tests/`，夹具进 `fixtures/`，并且 `bash scripts/check.sh` 通过。
