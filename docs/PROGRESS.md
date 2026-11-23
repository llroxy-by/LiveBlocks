# 序界（LiveBlocks）进度

> 只记录**真实状态**。三态含义固定，不允许混用：
> - **已验证**：有可复现的证据（命令输出、截图、实测数据），且证据指向当前 commit/版本。
> - **计划中**：已明确范围与验收，尚未产出或尚未验证。
> - **待硬件验证**：结论依赖实物/环境，代理不得自行迁移到「已验证」。
>
> 最近更新：2026-11-23（LB-002 合成卡片图像生成器，PR 见本仓库 `agent/lb-card-generator-*` 分支）

## 1. 已验证

| 项 | 证据 | 版本 |
| --- | --- | --- |
| 基线文档存在且结构完整（README、AGENTS、docs 六篇、Issue 模板） | 交付 PR 中的「文档自检」记录：文件清单核对、Markdown 链接目标存在、代码围栏成对、`PROTOCOL.md` 内示例 JSON 全部可被 `python3 -m json.tool` 解析 | 基线 PR |
| `docs/PROTOCOL.md` 标注为草案并带 `schema_version` 约定 | 文档首段状态块 + §0/§10 | 基线 PR |
| `docs/HARDWARE.md` 中电源与引脚项均为待确认 | §2 / §3 表格中 `PW-*`、`PN-*` 全部标注待确认，无实测结论 | 基线 PR |
| `docs/ROADMAP.md` 的 Backlog 按依赖排序且每条可独立开 Issue | §1 依赖图 + §2/§3/§4 条目均含范围/依赖/可观察验收/不做 | 基线 PR |
| 变更范围仅限工单约定路径 | PR 的 `git diff --name-only` 清单 | 基线 PR |
| LB-001 工程骨架目录与依赖声明：`src/liveblocks/`、`tests/`、`fixtures/`、`scripts/`、`pyproject.toml`、`.github/workflows/ci.yml` | 在干净克隆（`git clone --branch agent/lb-skeleton-tests-*`）中按 README「快速开始」原样执行：`python3 -m venv .venv` → `source .venv/bin/activate` → `python -m pip install --upgrade pip` → `python -m pip install -e '.[dev]'`，全部退出码 0 | LB-001 PR（Python 3.14.4 / pytest 9.2.0） |
| LB-001 一条命令跑全量测试且用例数 ≥1 | `python -m pytest` → `collected 6 items`、`6 passed in 0.13s`，退出码 0 | 同上 |
| LB-001 断言失败时该命令非零退出 | 把 `tests/test_smoke.py::test_version_is_semver` 的断言临时改为 `assert liveblocks.__version__ == "9.9.9"`：`bash scripts/check.sh` → `1 failed, 5 passed`、退出码 **1**；`python -m pytest` 同样退出码 **1**；随后 `git checkout -- tests/test_smoke.py` 还原（`git status` 干净） | 同上 |
| LB-001 本地检查脚本与 CI 是同一套检查 | `bash scripts/check.sh` → `==> 解释器`、`compileall`、`pytest (6 passed)`、`==> 检查通过`，退出码 0；`.github/workflows/ci.yml` 直接调用同一脚本 | 同上 |
| LB-001 CI 对 PR 显示检查结果 | PR #5 的 `ci` 工作流（run 45161217507）两个矩阵任务均通过：`check (python 3.11)` success（21s）、`check (python 3.13)` success（14s），各自执行 `bash scripts/check.sh` | LB-001 PR #5 |
| LB-001 命令行入口可用 | `liveblocks --version` → `liveblocks 0.0.1`（退出码 0）；`python -m liveblocks --version` 由 `tests/test_smoke.py` 用子进程覆盖 | 同上 |
| LB-002 合成卡片生成器：按卡片字典与版式定义生成单卡 / 多卡场景（任意角度、间距、光照噪声、遮挡、裁切），每张图附真值 JSON | 实现见 `src/liveblocks/synth/`（模块：`dictionaries.py` / `geometry.py` / `texture.py` / `raster.py` / `png.py` / `spec.py` / `generator.py`），数据见 `src/liveblocks/data/`（`card_dictionary.json`、`aruco_dictionaries.json`、`synth_layouts.json`、`bitmap_font.json`）；`python -m liveblocks synth list` 列出 16 张卡、2 种卡面版式、6 个场景预设 | LB-002 PR |
| LB-002 固定随机种子 → 生成结果字节可复现 | `python -m liveblocks synth verify --dir fixtures/scenes` → 6 行全部 `OK … 字节=同 像素=同 真值=同`，末行 `6/6 个场景与入库 fixtures 完全一致`，退出码 0；`tests/test_synth_reproducibility.py` 覆盖「同种子两次 → PNG 字节 / 像素 / 真值文本全等」「换种子 → 像素与真值均变化」，并逐个比对入库 `*.png` 字节与 `*.json` 文本 | 同上 |
| LB-002 每张图附真值（卡片 id、角点、位姿） | `fixtures/scenes/*.json` 每张卡含 `card_id` / `marker_id` / `card_face` / `pose{x,y,z_mm,yaw,pitch,roll_deg}` / `corners_px` / `marker_corners_px` / `in_plane_angle_deg` / `visible_fraction` / `status` / `occluded_by` / `covered_by_cards`；`tests/test_synth_generator.py` 断言字段齐全、角点按左上→右上→右下→左下且为凸四边形、面内角与 `yaw` 相差 < 2° | 同上 |
| LB-002 五类样例作为 fixtures 入库 | `fixtures/scenes/`：`normal`、`slight-rotation`、`overlap`、`clipped`、`low-light`、`multi-row`（共 6 场景 / 6 张 640×480 灰度 PNG + 6 份真值 + `index.json`，约 0.95 MB）；`tests/test_synth_reproducibility.py::test_required_fixture_files_exist` 按类别断言文件与真值字段 | 同上 |
| LB-002 生成器与可复现性测试 | `PYTHON=./.venv/bin/python bash scripts/check.sh` → `collected 82 items`、`82 passed in 74.37s`、`==> 检查通过`，退出码 0（Python 3.14.4 / pytest 9.0.2；含 LB-001 的 6 条骨架用例） | 同上 |
| LB-002 CI 对 PR 显示检查结果 | PR #8 的 `ci` 工作流（run 45168889185）两个矩阵任务均通过：`check (python 3.11)` success（1m21s，`81 passed, 1 skipped in 65.77s`）、`check (python 3.13)` success（1m16s，`81 passed, 1 skipped in 61.78s`），各自执行 `bash scripts/check.sh`；skipped 的那条是 `test_vendored_bit_patterns_match_opencv`（CI 只装 `.[dev]`，没有 OpenCV） | LB-002 PR #8 |
| LB-002 入库 ArUco 位表与 OpenCV `DICT_4X4_50` 逐位一致 | 可选 extra：`python -m pip install -e '.[dev,aruco]'` 后 `python -m pytest tests/test_synth_dictionary.py` → `8 passed`，其中 `test_vendored_bit_patterns_match_opencv` 用 OpenCV 逐位核对 50 个码字；CI 只装 `.[dev]`，该用例自动 skip | 同上 |

**说明**：以上「已验证」覆盖**文档层面**的可核对事实、**LB-001 骨架层面**的命令输出，以及 **LB-002 合成卡片生成器**的可复现产物；仍未包含**识别、排列、解析、执行、界面**（LB-003 起）。

## 2. 计划中

对应 `docs/ROADMAP.md` 的条目。LB-001 与 LB-002 已完成并迁至 §1「已验证」；其余条目尚未开始（仓库中只有合成卡片生成器，没有识别及其后面的任何模块）。

| 条目 | 内容 | 状态 |
| --- | --- | --- |
| LB-003 | 识别模块（ArUco 候选 → 卡片候选） | 计划中 |
| LB-004 | 排列还原（→ Layout） | 计划中 |
| LB-005 | 语法解析（→ AST） | 计划中 |
| LB-006 | 规范化 IR | 计划中 |
| LB-007 | 模拟执行器 | 计划中 |
| LB-008 | 调试界面（分层可视化） | 计划中 |
| LB-009 | 端到端回归与运行记录 | 计划中 |
| LB-021 | 交付与打包 | 计划中（依赖 LB-020） |

尚未决的 Q1–Q10 见 `docs/ARCHITECTURE.md` §4 与 `docs/DECISIONS.md` 末表；它们只影响「怎么做」，不改变上述计划项的存在。

## 3. 待硬件验证

以下条目**没有任何实测数据**，全部需要人按 `.github/ISSUE_TEMPLATE/hardware-test.md` 提交记录后才能升级状态。

| 条目 | 待验证内容 | 参考 |
| --- | --- | --- |
| HW-1 摄像头选型 | UVC 1080p / 工业全局快门 / CSI 的实际检出率与角点重复性 | `docs/HARDWARE.md` §1.1 |
| HW-2 主机选型 | 树莓派 5 / 迷你主机 / MCU 的算力、散热、供电余量 | §1.2 |
| HW-3 显示与反馈硬件 | HDMI 小屏 / SPI 屏 / 灯带 / 蜂鸣器的可用性与干扰 | §1.3 |
| HW-4 实体卡片 | 基材、打印工艺、标记尺寸、可识别距离 | §1.4 |
| HW-5 电源 | `PW-1` … `PW-6`（输入电压、峰值电流、独立供电、共地、软开关、温升） | §2 |
| HW-6 引脚 | `PN-1` … `PN-8`（接口、总线、灯效数据脚、按键、调试口、中断与保留脚、地线与屏蔽、启动/复位脚） | §3 |
| HW-7 环境与安装 | 桌面材质、环境光与频闪、工作距离与俯角、摄像头固定与标定流程 | §4 |
| HW-8 真实闭环 | LB-010 / LB-011 / LB-012 / LB-013 / LB-020 的端到端联调与延迟 | `docs/ROADMAP.md` §3 |

## 4. 已知限制与阻塞

- **只有合成卡片生成器，没有识别能力**：仓库已有工程骨架与 LB-002 的合成卡片生成器（含 6 个场景 fixtures），但**没有识别、排列、解析、执行、界面**，因此任何「能识别」「能执行」的说法目前都不成立。
- **LB-002 已知局限**（详见 `docs/DECISIONS.md` D-012）：只出 8bit 灰度图；相机是针孔 + 无畸变（畸变留给 LB-010）；遮挡物只支持凸四边形；卡片数、字典与版式是临时假设（Q1 未定，临时固定 `DICT_4X4_50` + 16 张卡）。
- **LB-002 未改动 ROADMAP**：`docs/ROADMAP.md` 的范围与优先级一个字节都没改（LB-002 的条目仍按原样描述，实现落在 `src/liveblocks/synth/`）。
- **D-004 未定**：实现语言仍待仓库所有者确认；LB-001 骨架按 D-011 以 Python 3.11 + pytest **临时**落地，换语言需按 D-011 列出的范围重做骨架。
- **Q1/Q2/Q3/Q4/Q5 未定**：文法与识别细节是提案级，实现时先按最小假设并留扩展点。
- **硬件链路未知**：主机直连 vs MCU 分工（Q10）未定，`backend.hw` 无法设计引脚级细节。
- **本单不派发后续工单**：ROADMAP 条目只是 backlog，开新 Issue 需仓库所有者确认（`AGENTS.md` §5）。

## 5. 更新规则

1. 每个合并的 PR 更新本文件对应行，并写清证据位置（命令、文件、Issue 链接）。
2. 证据失效（版本已变）时把该行退回「计划中」，不保留过期「已验证」。
3. 「待硬件验证」只在人工测试 Issue 有结论且仓库所有者确认后迁移。
