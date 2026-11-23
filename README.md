# 序界 / LiveBlocks

序界（LiveBlocks）是一个「实体卡片编程」方向的实验项目：把带标记的桌面实体卡片放到摄像头前，经图像识别得到卡片身份与排列，解析成语法树（AST）与规范化的 JSON 中间表示，再交给模拟器或真实硬件执行，最后把执行与反馈显示出来。

## 现状

- 已完成 **LB-001 工程骨架**与 **LB-002 合成卡片图像生成器**：可以按卡片字典与版式定义生成单卡 / 多卡场景（任意角度、间距、光照噪声、遮挡、裁切）的 ArUco 卡片图像，并输出配套真值 JSON；固定随机种子时生成结果字节可复现。
- 仓库**还没有识别、排列、解析、执行、界面**（LB-003 起），因此目前不能说「能识别」「能执行」。
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

跑本地检查（与 CI 同一套检查：语法编译检查 + 全量测试，含合成卡片生成器与 fixtures 可复现性）：

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

## 文档入口

| 文档 | 内容 | 状态 |
| --- | --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 端到端链路、模块边界、数据契约位置、待决事项 | 基线草案 |
| [docs/PROTOCOL.md](docs/PROTOCOL.md) | 层间数据格式（识别 / 排列 / AST / IR / 执行反馈），**草案，未冻结** | 草案 v0.0.1 |
| [docs/HARDWARE.md](docs/HARDWARE.md) | 待验证的硬件选型、电源与引脚待确认项、卡片制作 | 待验证 |
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
│   ├── cli.py            # liveblocks --version / synth list|generate|verify
│   ├── data/             # 卡片字典、ArUco 位表、版式定义、点阵字体（JSON）
│   └── synth/            # LB-002 合成卡片图像生成器（不做识别）
├── tests/                # pytest 用例，testpaths = tests
├── fixtures/             # 测试夹具：skeleton.json + scenes/（合成卡片图像与真值）
├── scripts/check.sh      # 本地检查脚本，与 CI 同一条命令
└── docs/                 # 架构、协议草案、硬件、路线图、决策、进度
```

新增业务模块时应遵守：源码进 `src/liveblocks/`，用例进 `tests/`，夹具进 `fixtures/`，并且 `bash scripts/check.sh` 通过。
