# 序界（LiveBlocks）进度

> 只记录**真实状态**。三态含义固定，不允许混用：
> - **已验证**：有可复现的证据（命令输出、截图、实测数据），且证据指向当前 commit/版本。
> - **计划中**：已明确范围与验收，尚未产出或尚未验证。
> - **待硬件验证**：结论依赖实物/环境，代理不得自行迁移到「已验证」。
>
> 最近更新：2026-11-23（LB-005 语法层：`Layout` → `Ast` + P0 文法文档，PR 见本仓库 `agent/lb-parser-*` 分支）

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
| LB-003 识别模块：标记检测 + 卡片字典映射 → `CardCandidateList`（含 `status`、置信度、朝向） | 实现见 `src/liveblocks/vision/`（`image.py` 二值化/连通域/凸包/亚像素细化、`decode.py` 单应+码字比对、`markers.py` 标记候选、`cards.py` 卡片注册与映射、`stats.py` 统计、`protocol.py` 校验、`pose.py` 可选位姿）；`python -m liveblocks recognize detect --image fixtures/scenes/normal.png` 输出 `CardCandidateList`，`--emit markers` 输出 `MarkerCandidateList` | LB-003 PR |
| LB-003 对 LB-002 fixtures 跑识别，输出符合 `PROTOCOL.md` §3 的 JSON | `python -m liveblocks recognize report --dir fixtures/scenes` 对 6 个场景逐帧生成 §3.1/§3.2 对象；`tests/test_vision_protocol.py`（22 例）用最小校验器逐条核对 kind / schema_version / frame_id / status 枚举 / confidence 区间 / 整数像素坐标 / 字符串 marker_ids，并断言 11 类违约文档会被拒绝、未知新增字段被容忍（§0）；`tests/test_vision_fixtures.py::test_outputs_conform_to_protocol` 对全部 fixtures 的每帧输出做同样校验 | LB-003 PR |
| LB-003「正常」样例无漏检 | 报表 `normal` 行：预期 1 / 识别 1 / 漏检 0；`tests/test_vision_fixtures.py::test_scene_identifies_every_card[normal]` 断言 `{"0": "ok"}`；`format_report` 的「验收对照」直接打印「「正常」样例无漏检：通过（预期 1，识别 1，漏检 0）」 | LB-003 PR |
| LB-003「裁切」样例标 `clipped` | 报表 `clipped` 行：2 张卡 `status` 均为 `clipped`，诊断码 `LB-V006`×2（由标记外推的卡片角点越出画面）+ `LB-V002`×1（标记 13 触及下边界）；`tests/test_vision_cards.py::test_clipped_scene_marks_cards_clipped`、`test_vision_fixtures.py::test_scene_identifies_every_card[clipped]` | LB-003 PR |
| LB-003 未登记标记标 `unknown_card` | 摘掉标记 0 的卡片字典 → `recognize detect --image fixtures/scenes/normal.png --card-dictionary <摘掉 C01 的字典>` 输出 `status=unknown_card`、`card_type/card_face/card_id=unknown`、无 `orientation_quadrant`、诊断 `LB-V001`；`tests/test_vision_cards.py::test_unregistered_marker_is_unknown_card`、`test_unknown_card_has_priority_over_clipped`、`tests/test_vision_cli.py::test_recognize_detect_unknown_card_via_alternative_dictionary` | LB-003 PR |
| LB-003 有一份可读的准确率 / 漏检统计输出 | `python -m liveblocks recognize report --dir fixtures/scenes`（退出码 0）输出 6 行场景表 + 合计 + 召回率/精确率 + 验收对照 + 明细：合计 预期 17 / 识别 15 / 未识别 1 / 漏检 1 / 误检 0，召回率 88.2%、精确率 100%；明细点名「漏检 overlap: 标记 12」「overlap: C07（标记 6）检出但不可识别 → 候选 c-0 报 occluded」「状态差异 overlap: C02: 识别=ok 真值=occluded」；`--json` 另存 `RecognitionAccuracyReport` | LB-003 PR |
| LB-003 识别精度（角点 / 卡片中心） | 同一报表的误差列（检测角点 vs 真值 `marker_corners_px`、外推卡片中心 vs 真值 `center_px`，均值/最大值，像素）：`normal` 0.01/0.01、0.005/0.005；`low-light` 0.13/0.15、0.04/0.05；`slight-rotation` 0.24/0.42、0.11/0.21；`clipped` 0.34/0.40、0.19/0.23；`multi-row`（19 px 小标记）0.57/0.77、0.27/0.43；`overlap` 0.17/0.17、0.06/0.06 | LB-003 PR |
| LB-003 亚像素边缘细化把角点误差压到亚像素 | `tests/test_vision_markers.py::test_subpixel_refinement_beats_raw_component_quad` 断言「细化后平均角点误差 < 凸包整数角点的一半」；`tests/test_vision_image.py::test_refine_quad_recovers_subpixel_edges` 用人工抗锯齿方块验证细化能还原到 0.25 px 内 | LB-003 PR |
| LB-003 与 OpenCV 的独立实现交叉核对 | 装 `.[dev,aruco]` 后 `python -m pytest tests/test_vision_opencv.py` → 13 passed：`normal`/`low-light`/`slight-rotation`/`multi-row` 四个完整可见场景的标记 id 集合与 `cv2.aruco.ArucoDetector` 完全一致；共有标记的角点差 ≤ 2 px；两处已知分歧写成断言（`clipped`：本实现读出被切到的标记 13，OpenCV 漏；`overlap`：OpenCV 读出被纸片压住的标记 6，本实现判不可译码）。CI 只装 `.[dev]`，这 13 例自动 skip | LB-003 PR |
| LB-003 识别路径的测试覆盖 | `tests/test_vision_image.py`(9) `test_vision_decode.py`(11) `test_vision_markers.py`(10) `test_vision_cards.py`(13) `test_vision_fixtures.py`(25) `test_vision_pose.py`(10) `test_vision_protocol.py`(22) `test_vision_cli.py`(11) `test_vision_opencv.py`(13，装 OpenCV 时) 共 124 例；`PYTHON=/tmp/lb-venv-ci/bin/python bash scripts/check.sh`（等价 CI 环境：只装 `.[dev]`）→ `192 passed, 2 skipped in 93.50s`，退出码 0；装 OpenCV 的本地 venv 里 `python -m pytest -q` → `206 passed in 96.48s` | LB-003 PR |
| LB-003 位姿估计（可选） | 只有给了内参（`--intrinsics`）且标记可见边长 ≥ 24 px 才输出 `pose`；`tests/test_vision_pose.py`（10 例）对照真值：`normal`/`clipped`/`low-light`/`overlap`/`slight-rotation` 的深度 z 误差 ≤ 5%（实测 0.0%~3.1%），重投影误差 ≤ 1 px；未给内参或 `multi-row`（19 px 标记）时不输出 `pose` | LB-003 PR |
| LB-003 CI 对 PR 显示检查结果 | PR #10 的 `ci` 工作流（run 45180592756）两个矩阵任务均通过：`check (python 3.11)` success（103.83s，`192 passed, 2 skipped`，解释器 Python 3.11.18）、`check (python 3.13)` success（91.80s，`192 passed, 2 skipped`，解释器 Python 3.13.18），各自执行 `bash scripts/check.sh` 并以「==> 检查通过」结束；跳过的 2 项是 `tests/test_vision_opencv.py` 与 `tests/test_synth_dictionary.py` 里需要 OpenCV 的交叉核对（CI 只装 `.[dev]`） | LB-003 PR #10 |
| LB-002 入库 ArUco 位表与 OpenCV `DICT_4X4_50` 逐位一致 | 可选 extra：`python -m pip install -e '.[dev,aruco]'` 后 `python -m pytest tests/test_synth_dictionary.py` → `8 passed`，其中 `test_vendored_bit_patterns_match_opencv` 用 OpenCV 逐位核对 50 个码字；CI 只装 `.[dev]`，该用例自动 skip | 同上 |
| LB-004 排列层：分簇 / 阅读方向排序 / 朝向归一 / 去重 / 歧义诊断 → `Layout`（§4） | 实现见 `src/liveblocks/layout/`（`protocol.py` 数据模型 + 最小校验、`arrange.py` 算法、`report.py` 自检报告）；`python -m liveblocks layout arrange --candidates fixtures/layouts/multi-row-cards.json` 输出 §4 的 `Layout`；`python -m liveblocks layout from-image --image fixtures/scenes/multi-row.png --card-layout card-40x40` 一步跑「识别 → 排列」（退出码 0，行 2 / 槽位 6） | LB-004 PR |
| LB-004 两行样例被正确分成两行 | `python -m liveblocks layout report --dir fixtures/layouts` → `multi-row-cards 候选 6 → 行 2 / 槽位 6`，行 0 `c-2(move-forward@257,206) c-1(number-1@322,201) c-0(jump@383,200)`、行 1 `c-4(repeat@257,275) c-5(number-2@318,275) c-3(end@384,271)`，验收对照打印 `[通过] 两行样例被正确分成两行`；`tests/test_layout_fixtures.py::test_two_row_fixture_is_split_into_two_rows`、`test_end_to_end_from_image_to_layout`（直接用 LB-003 在 `multi-row.png` 上的识别结果，`frame_id=f-multi-row`） | LB-004 PR |
| LB-004 间距歧义样例产出 `LB-L001` | 同上报告 `spacing-ambiguity 候选 4 → 行 1 / 槽位 4，诊断 1 条`：`LB-L001 warning: 候选 c-1 与 c-2 间距 38.0px 低于阈值 47.4px（行 0），排列顺序存疑 [c-1,c-2]`；`tests/test_layout_fixtures.py::test_spacing_ambiguity_fixture_yields_l001`、`tests/test_layout_arrange.py::test_spacing_ambiguity_emits_l001_with_both_ids`、`test_explicit_pitch_controls_thresholds`（显式 `pitch_px` 让 70px 间距被判歧义） | LB-004 PR |
| LB-004 重复候选去重后有 `LB-L002` | 同上报告 `duplicates 候选 4 → 行 1 / 槽位 3，去重移除 1`：`LB-L002 info: 候选 c-0（move-forward）与 c-1 是同一张卡的重复检出（圆心距 ≤ 22.9px）：保留置信度最高的 c-0（0.92），移除 c-1 [c-0,c-1]`；`tests/test_layout_fixtures.py::test_duplicate_fixture_dedupes_with_l002_trace`、`tests/test_layout_arrange.py::test_duplicate_candidates_are_removed_with_l002`、`test_same_face_far_apart_is_not_a_duplicate`（同卡面但不重合的两张卡不去重） | LB-004 PR |
| LB-004 所有排序结论可追溯到候选 id | 每个槽位带 `candidate_id` + `x_px/y_px`，`basis.rows[].candidate_ids` 给出每行的候选序列，`basis.counts` 给出输入/入槽/去重移除/身份未知四项计数；报告末行 `合计 4 个候选夹具 / 14 个槽位 / 未追溯候选 0 个`；`tests/test_layout_fixtures.py::test_every_fixture_layout_is_traceable`（4 个夹具参数化，断言「输入候选 ⊆ 槽位 ∪ 诊断点名」且槽位 id 都在输入内） | LB-004 PR |
| LB-004 输出符合 §4，且未知枚举/字段按 §0 处理 | `tests/test_layout_protocol.py`（24 例）：断言槽位字段集合恰为 §4 那 7 个、坐标是整数像素、`orientation_quadrant ∈ 0..3`、`row_index`/`slot_index` 从 0 连续、`candidate_id` 不重复，并参数化 17 类违约文档必须被 `validate_layout` 拒绝、未知新增字段被容忍；报告与 CLI 出口都调同一个校验器 | LB-004 PR |
| LB-004 身份未知的候选被排除但留痕（`LB-L003`，加法式） | `overlap-cards`（LB-003 在 `overlap.png` 上的真实输出，含 1 个 `occluded` 候选）：报告 `候选 2 → 行 1 / 槽位 1，身份未知 1`，诊断 `LB-L003 warning: 1 个候选身份未知，未纳入排列：c-0（occluded）…`；`tests/test_layout_fixtures.py::test_unknown_identity_fixture_is_traced_with_l003`、`tests/test_layout_arrange.py::test_unknown_identity_candidates_are_excluded_but_traced` | LB-004 PR |
| LB-004 行分组歧义（`LB-L004`）与非正立（`LB-L005`）也有路径与测试 | `tests/test_layout_arrange.py::test_row_boundary_near_threshold_emits_l004`（两行间隙刚过切分阈值）、`test_merged_rows_span_emits_l004`（行内跨度超卡距 → 可能两行被并成一行）、`test_orientation_is_normalised_against_reading_direction`（同一张卡在 `ltr_ttb` 下归一为 2 并报 `LB-L005`，在 `ttb_ltr` 下归一为 0 不报） | LB-004 PR |
| LB-004 排列层测试与本地检查 | `PYTHON=/tmp/lb-venv/bin/python bash scripts/check.sh` → `compileall` 通过、`259 passed, 2 skipped in 93.55s`、`==> 检查通过`，退出码 0（Python 3.14.4 / pytest 9.x；跳过的 2 项是只有装 OpenCV 才跑的两条交叉核对）；其中新增排列层 67 例：`tests/test_layout_protocol.py` 24、`tests/test_layout_arrange.py` 21、`tests/test_layout_fixtures.py` 13、`tests/test_layout_cli.py` 9（单独跑 `python -m pytest tests/test_layout_*.py` → `67 passed in 3.10s`） | LB-004 PR |
| LB-004 CI 对 PR 显示检查结果 | PR #13 的 `ci` 工作流（run 45193979675）两个矩阵任务均通过：`check (python 3.11)` pass（1m24s，`259 passed, 2 skipped in 66.15s`，解释器 Python 3.11.18）、`check (python 3.13)` pass（1m45s，`259 passed, 2 skipped in 92.43s`，解释器 Python 3.13.18），各自执行 `bash scripts/check.sh` 并以「==> 检查通过」结束；跳过的 2 项与 LB-002/LB-003 相同，是只有装 OpenCV 才跑的交叉核对 | LB-004 PR #13 |

| LB-005 语法层：`Layout`（§4）→ `Ast`（§5），P0 最小文法「语句序列 + 字面参数」，错误定位到行/槽位/卡片 | 实现见 `src/liveblocks/parser/`（`grammar.py` 机器可读文法 + 文档核对、`ast.py` 数据模型与 §5 校验、`parse.py` 解析、`report.py` 自检报告）；`python -m liveblocks parse ast --layout fixtures/ast/valid-basic.json` 输出 §5 的 `Ast`（3 条语句 / 7 个节点 / 0 诊断）；`python -m liveblocks parse report --dir fixtures/ast` 打印 8 个夹具的逐条结果与三条验收对照，退出码 0 | LB-005 PR |
| LB-005 合法样例得到符合 §5 的 AST | `python -m liveblocks parse report --dir fixtures/ast` 的 3 个合法夹具全部 `§5 校验通过`：`valid-basic`（3 语句 / 7 节点）、`valid-empty`（0 语句的空程序）、`from-overlap-cards`（1 语句 / 2 节点，LB-004 的真实排列输出）；`tests/test_parser_fixtures.py::test_fixture_parses_to_the_declared_outcome`（8 例参数化）逐节点断言 `span` + `origin` 齐全并调 `validate_ast`；`tests/test_parser_ast.py::test_ast_document_matches_section5_shape`、`test_validate_ast_accepts_section5_example_shape` 用 §5 示例形状做正例 | LB-005 PR |
| LB-005 非法样例产出带 `span` 与 `origin` 的 `LB-P0xx` 诊断且不崩溃 | 5 个非法夹具覆盖全部 5 个码：`from-multi-row-cards`（`LB-P002`+`LB-P004`，上游真实排列）、`invalid-number-first`（`LB-P001`）、`invalid-control-flow`（`LB-P004`×2）、`invalid-operator-arg`（`LB-P005`）、`invalid-empty-slot`（`LB-P003`×2）；每条诊断都带 `span`（行 + 槽位区间）与 `origin`（`candidate_id` + `card_face`），解析返回**部分 AST**、不抛异常（`tests/test_parser_parse.py`、`tests/test_parser_fixtures.py::test_invalid_fixtures_never_raise`）；`liveblocks parse ast --layout fixtures/ast/invalid-control-flow.json --fail-on-error` → 退出码 1（默认 0，诊断随 Ast 输出） | LB-005 PR |
| LB-005 文法定义有独立文档并可被测试引用 | `docs/GRAMMAR.md`（草案 `grammar_version` 0.0.1：产生式、16 张卡的角色表、AST 形状与 `span`/`origin` 约定、`LB-P001`…`LB-P005` 码表、8 条协议缺口）；`src/liveblocks/parser/grammar.py::grammar_document_gaps` 逐项核对文档里的产生式、节点名、5 个诊断码（含标题）与全部卡面角色，`parse report` 打印「文法文档缺口 0 项」；`tests/test_parser_grammar.py`（17 例）含 3 例**反向验证**（删掉一个诊断码 / 一行产生式 / 一张卡面后必须报缺口），因此「文档与实现一致」不是假通过 | LB-005 PR |
| LB-005 语法层测试与本地检查 | `PYTHON=/tmp/lb-venv-parser/bin/python bash scripts/check.sh` → `compileall` 通过、`372 passed, 2 skipped in 93.00s`、`==> 检查通过`，退出码 0（Python 3.14.4 / pytest 9.2.0；跳过的 2 项与 LB-002/LB-003/LB-004 相同，是只有装 OpenCV 才跑的交叉核对）；其中新增语法层 113 例：`tests/test_parser_grammar.py` 17、`tests/test_parser_ast.py` 35、`tests/test_parser_parse.py` 22、`tests/test_parser_fixtures.py` 26、`tests/test_parser_cli.py` 13 | LB-005 PR |

**说明**：以上「已验证」覆盖**文档层面**的可核对事实、**LB-001 骨架层面**的命令输出、**LB-002 合成卡片生成器**的可复现产物、**LB-003 识别模块**在 LB-002 fixtures 上的识别结果与统计，**LB-004 排列层**在 `fixtures/layouts` 候选夹具上的排列结论与验收对照，以及 **LB-005 语法层**在 `fixtures/ast` 语法夹具上的解析结果与验收对照；仍未包含**规范化 IR、执行、界面**（LB-006 起），也没有任何真实摄像头 / 实体卡片的证据（见 §3）。

## 2. 计划中

对应 `docs/ROADMAP.md` 的条目。LB-001 / LB-002 / LB-003 / LB-004 / LB-005 已完成并迁至 §1「已验证」；其余条目尚未开始（仓库中只有骨架、合成卡片生成器、识别模块、排列层与语法层，没有规范化 IR 及其后面的任何模块）。

| 条目 | 内容 | 状态 |
| --- | --- | --- |
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

- **还没有规范化 IR、执行、界面**：仓库已有骨架、LB-002 合成卡片生成器、LB-003 识别模块、LB-004 排列层与 LB-005 语法层（含 6 个场景 fixtures 的识别统计、4 个候选夹具的排列验收与 8 个语法夹具的解析验收），但**没有 IR 规范化、执行、界面**，因此任何「能执行」「能显示」的说法目前都不成立。
- **LB-003 只在合成图上验证过**：所有识别结论都来自 LB-002 的合成灰度图（无镜头畸变、无运动模糊）。真实摄像头的鲁棒性（反光、阴影、透视更大、运动模糊）没有证据，属 LB-010/LB-020。
- **LB-003 已知的识别偏差（如实计入统计，不隐藏）**：
  1. `overlap` 场景 3 张卡只识别出 1 张：标记 12 完全漏检（标记被纸片/卡片盖住），标记 6 检出形状但码字不可译码（报 `occluded` 候选）；
  2. 卡片级遮挡在识别层看不到：`overlap` 里 C02 的标记完好，因此报 `ok` 而真值是 `occluded`（区分「卡片被压住」需要 LB-004 的分簇信息或额外的卡面外观校验）；
  3. 位姿只在标记可见边长 ≥ 24 px 时输出，且没有非线性精化：真实相机上只能当粗值（LB-010 负责标定与精化）；
  4. 只支持 1 模块宽黑边的 4×4 字典；标记可见边长小于约 8 px 会漏检；不做多尺度检测。
- **LB-004 已知限制（详见 `docs/DECISIONS.md` D-015）**：
  1. 排列阈值需要长度尺度，而 §4/§3.2 只给了 `center_px`：本实现用「各候选到最近邻距离的中位数」当典型卡距（自适应），
     因此**只有两张卡、或两行贴得比卡距还近时无法判歧义**，这类场景需用 `--pitch-px` 显式给预期卡距；
  2. 行分簇是一维间隙聚类，**不做透视矫正**：桌面倾斜导致同一行的 y 跨度超过约一个卡距时会被切成两行（此时会报 `LB-L004` 提示，而不是静默出错）；
  3. `ttb_ltr` 下「一行」在画面上是一列，是本实现对 §4 的解释；LB-005 解析层只按 `rows`/`slots` 的顺序读（与几何无关），因此两种解释下都一致，但若将来把「一行」改成严格横排，解析层要一起改；
  4. 朝向归一依赖注册象限，而 LB-003 当前所有卡的象限都是 0（注册信息），「卡片实体转 90°」尚未在实测中出现；
  5. `fixtures/layouts` 里的 `spacing-ambiguity.json` / `duplicates.json` 是**手写**夹具（§3.4 的唯一例外，原因见 `fixtures/README.md` §4），
     LB-002 的场景预设不产生这两种候选形态。
- **LB-003 发现的协议缺口（只报告，未改 `docs/PROTOCOL.md`）**：见 `docs/DECISIONS.md` D-014 —— §3 没有 `diagnostics` 槽位、`status` 无法表达多事实冲突、`orientation_quadrant` 没有「未知」表示、§0 的 `schema_version`（`major.minor`）与示例（`0.0.1`）不一致、§0 的「逆时针为正」与相机坐标系 y 向下冲突、§3.2 缺 `card_id`、§3.1 的 `pose` 未定义必填性与精度。
- **LB-002 已知局限**（详见 `docs/DECISIONS.md` D-012）：只出 8bit 灰度图；相机是针孔 + 无畸变（畸变留给 LB-010）；遮挡物只支持凸四边形；卡片数、字典与版式是临时假设（Q1 未定，临时固定 `DICT_4X4_50` + 16 张卡）。
- **LB-002 未改动 ROADMAP**：`docs/ROADMAP.md` 的范围与优先级一个字节都没改（LB-002 的条目仍按原样描述，实现落在 `src/liveblocks/synth/`）。
- **LB-005 已知限制（详见 `docs/DECISIONS.md` D-016 与 `docs/GRAMMAR.md`）**：
  1. P0 文法只有「语句序列 + 字面参数」：`repeat` / `if` / `end` 报 `LB-P004`、运算符卡报 `LB-P005`，还没法表达表达式、变量、循环与条件；
  2. **不检查参数个数（arity）**：§5 与 D-005 都没定义每条命令收几个参数，解析一律接受零个或多个字面参数，arity 留给 LB-006；
  3. 错误恢复是「整行丢弃 + 只报第一条」：被丢掉那行里合法的卡也不会进 AST（诊断的 `span`/`origin` 能指回原卡，原 `Layout` 也仍在运行记录里）；
  4. 「一行 = 一条语句」是本实现对 §4 `rows` 的读法：与 D-015「`ttb_ltr` 下的一行是画面上的一列」兼容（本层只看 `rows`/`slots` 顺序），但若将来把「一行」改成严格横排，本层要一起改；
  5. `fixtures/ast` 里 6 个手写夹具是同类例外（LB-002/LB-003/LB-004 的现有夹具凑不出「数字卡打头」「运算符占参数位」这类语法错误），另外 2 个是 LB-004 真实输出的快照。
- **LB-005 发现的协议缺口（只报告，未改 `docs/PROTOCOL.md`）**：见 `docs/DECISIONS.md` D-016 —— §5 示例的 `Program.span` 与其槽位数自相矛盾、没定义跨行 `span`、没定义多卡 `origin`、示例的 `Number` 节点缺 `origin`、没有 `frame_id`（或上游引用）、`node` 开放枚举缺降级口径、`LB-P0xx` 只举了 `LB-P003` 一个例子、字面量只能是整数。
- **LB-005 未改动 ROADMAP**：`docs/ROADMAP.md` 的范围与优先级一个字节都没改（实现落在 `src/liveblocks/parser/`，LB-006 起的条目原样保留）。
- **LB-004 未改动 ROADMAP**：`docs/ROADMAP.md` 的范围与优先级一个字节都没改（实现落在 `src/liveblocks/layout/`，LB-005 起的条目原样保留）。
- **D-004 未定**：实现语言仍待仓库所有者确认；LB-001 骨架按 D-011 以 Python 3.11 + pytest **临时**落地，换语言需按 D-011 列出的范围重做骨架。
- **Q1/Q2/Q3/Q4/Q5 未定**：文法与识别细节是提案级，实现时先按最小假设并留扩展点（LB-005 的 P0 文法按 D-005 的方向落地，取舍与缺口见 D-016）。
- **硬件链路未知**：主机直连 vs MCU 分工（Q10）未定，`backend.hw` 无法设计引脚级细节。
- **本单不派发后续工单**：ROADMAP 条目只是 backlog，开新 Issue 需仓库所有者确认（`AGENTS.md` §5）。

## 5. 更新规则

1. 每个合并的 PR 更新本文件对应行，并写清证据位置（命令、文件、Issue 链接；CI 结果等 CI 真跑完再补）。
2. 证据失效（版本已变）时把该行退回「计划中」，不保留过期「已验证」。
3. 「待硬件验证」只在人工测试 Issue 有结论且仓库所有者确认后迁移。
