# 序界 / LiveBlocks

序界（LiveBlocks）是一个「实体卡片编程」方向的实验项目：把带标记的桌面实体卡片放到摄像头前，经图像识别得到卡片身份与排列，解析成语法树（AST）与规范化的 JSON 中间表示，再交给模拟器或真实硬件执行，最后把执行与反馈显示出来。

## 现状

- 仓库目前处于**研发基线（baseline）阶段**，只有文档，没有任何可运行代码。
- 首阶段目标是**纯软件闭环**：用合成的卡片图像代替摄像头、用模拟执行代替硬件，先把「识别 → 排列 → 解析 → 执行 → 反馈/调试」的整条链路和接口契约定下来。
- 硬件（摄像头、主机、屏幕、实体卡片、电源与引脚）尚未选型确认，相关结论均为**待验证**。
- 尚无 CI、构建脚本与测试，这些随软件 MVP 骨架任务引入。

更细的状态与证据见 [docs/PROGRESS.md](docs/PROGRESS.md)。

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

## 目录约定（基线）

```
.
├── README.md
├── AGENTS.md
├── docs/            # 架构、协议草案、硬件、路线图、决策、进度
└── .github/
    └── ISSUE_TEMPLATE/hardware-test.md
```

代码目录（如 `src/`、`tests/`、`fixtures/`）在软件 MVP 骨架任务中引入，届时同步更新本节。
