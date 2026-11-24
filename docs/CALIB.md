# 标定与畸变校正（LB-031 草案）

> **状态：草案 v0.0.1（未冻结）**。本文件描述 LB-031 落地的标定流程、标定文件格式与验收口径。
> 冻结前任何字段与算法都可能改动；改动格式必须在本节与 §10 说明兼容性影响。
>
> **适用范围**：只覆盖算法与文件格式，以及**合成场景**上的可复现证据。
> **不在本文件结论内**：任何真实摄像头的重投影误差、可用拍摄距离、板面平整度——这些必须在
> 实物上实测，见 §8「待人工验证」。代理不得代填这些数字，也不得把合成场景的数字当成实测。

## 0. 约定

- 像素坐标：连续坐标，原点在左上**外边界**，像素 `(x, y)` 的中心在 `(x + 0.5, y + 0.5)`
  （与 `docs/PROTOCOL.md` §0 一致）。
- 长度单位：标定板与位姿一律用毫米（`mm`），内参与重投影误差用像素（`px`）。
- 一切随机量都由「预设种子 + 视图下标」决定，因此同一份代码必然给出同一批图像与同一组数字。
- 本文件里的命令都在仓库根目录执行，入口是 `liveblocks`（或 `python -m liveblocks`）。

## 1. 标定链条一览

```
标定板定义  ──▶ 拍摄图（可带真值）  ──▶ 角点检测  ──▶ 标定求解  ──▶ 标定文件 JSON
(§2)              (合成器 / 真实采集)     (§3)          (§5)          (§4)
                                                                    │
                                      Frame.intrinsics/distortion ◀─┘   (§6)
```

一条命令看到全部数字：

```bash
liveblocks calibrate report --preset all
```

它按预设生成合成标定图、检角点、解内参/畸变、打印重投影误差、打印**校正前后**同一静态场景的
角点重复性（均值 / 最大值，像素），并与内参真值对照。退出码 0 = 全部验收条目通过。

命令一览（`liveblocks calibrate <子命令>`）：

| 子命令 | 作用 |
| --- | --- |
| `report` | 一条命令复现全部验收数字；`--out-dir` 写标定文件，`--golden` 与入库快照逐字节比对 |
| `run` | 用目录里的图像（真实采集或合成）标定，`--out` 写标定文件 |
| `corners` | 打印一帧的角点检测结果（棋盘格 / ChArUco 两条流程） |
| `undistort` | 按标定把图校正成理想针孔图，写出 PNG |
| `frame` | 打印按标定填好 `intrinsics` / `distortion` 的 §2 `Frame`（`--undistort` 同时校正像素） |
| `validate` | 校验一份标定文件（问题码 `LB-B008`） |
| `targets` | 列出内置标定板与合成预设 |

验收阈值（写死在 `data/calib_presets.json` 的 `thresholds`，报告里逐条打印）：

| 条目 | 阈值 | 说明 |
| --- | --- | --- |
| 重投影误差（`LB-B006`） | `rms_px <= 0.5` | 合成场景实测 0.11 px，阈值留了 4 倍余量 |
| 校正后角点重复性（`LB-B007`） | 均值 `<= 0.5 px`、最大 `<= 2.0 px` | 口径见 §7.6 |
| 焦距相对误差 | `<= 3%` | **仅合成场景**（真值已知）才有的判据 |
| 主点偏差 | `<= 8 px` | 同上；主点是弱约束项，阈值刻意放得比实测宽 |
| 校正后画面能用针孔单应解释 | 单应残差 `<= 0.5 px` | 校正是否真的起作用 |

```bash
# 一条命令看全部数字（合成场景）
liveblocks calibrate report --preset all --out-dir fixtures/calib --golden fixtures/calib

# 用真实采集目录标定（人来做，见 §8）
liveblocks calibrate run --dir fixtures/live/20261124-120000 --target checkerboard-9x6-25mm --out calib.json

# 把标定填进帧 / 校正图像
liveblocks calibrate frame --calib calib.json --image frame.png
liveblocks calibrate undistort --calib calib.json --image frame.png --out frame-fixed.png
```

## 2. 标定板定义

一块板由 `data/calib_presets.json` 里的 `targets` 描述，字段固定为：

| 字段 | 含义 |
| --- | --- |
| `name` | 板名（`calibrate run --target <name>` 用它） |
| `kind` | `checkerboard` 或 `charuco` |
| `squares_x` / `squares_y` | 棋盘的方块数（横向 / 纵向） |
| `square_mm` | 方块边长（毫米） |
| `margin_mm` | 白色留边宽度（毫米；只影响打印，不参与内角点坐标） |
| `marker_mm` | 仅 ChArUco：标记边长（毫米），要求 `0 < marker_mm <= square_mm` |
| `dictionary` | 仅 ChArUco：ArUco 字典名（`DICT_4X4_50` 等，见 `data/aruco_dictionaries.json`） |

平面坐标：原点在**整块板（含留边）的中心**，x 向右、y 向下，`Z = 0`。棋盘从
`(-pattern_w/2, -pattern_h/2)` 起，`(col + row) % 2 == 0` 的格子为**黑格**。

**内角点**（标定用的观测点）是四个方块交汇的 X 型角点，共 `(squares_x - 1) × (squares_y - 1)` 个，
坐标 `(i·square_mm - pattern_w/2, j·square_mm - pattern_h/2)`，`1 ≤ i ≤ squares_x-1`、`1 ≤ j ≤ squares_y-1`，
id 按行优先从 0 起：`id = (j - 1)·(squares_x - 1) + (i - 1)`。

ChArUco 板把**白格**换成 ArUco 标记：标记居中、四周留白，id 也按行优先从 0 起递增，只落在白格上。
内角点的定义与纯棋盘格**完全相同**——所以两条流程共用同一套真值与同一套求解。

> 已知限制：本实现的 ChArUco 标记排布（行优先、只落在白格）与 OpenCV `CharucoBoard` 的编号顺序
> **没有经过逐格核对**，因此不能拿 OpenCV 生成的板图直接套本实现的目标定义。要做互操作得先补一条
> 交叉核对（见 §7 待办 3）。

## 3. 角点检测（两条流程）

两条流程共用同一条底层管线（纯标准库，复用 LB-003 的亚像素细化）：

1. 局部自适应二值化 → **4 邻域腐蚀 1 像素** → 4 连通域。腐蚀的作用是断开「黑白格只在角点处
   点接触」的那条亚像素桥；不腐蚀的话，相邻黑格会粘成一个连通域（实测 9×6 棋盘会粘成 3 对长条）。
2. 每个连通域：凸包 → 四边形化简 → 边长 / 长宽比 / 填充率 / **四邻对比度**筛选 →
   `vision.image.refine_quad` 做灰度亚像素边缘细化（角点精度约 0.05~0.1 px）。
3. **拓扑编号**：两个黑格共享一个角点 ⟺ 它们在棋盘上对角相邻。从一个黑格出发做广度优先搜索，
   用「第 k 个角点对应 (±1, ±1) 的对角格」把整块棋盘的编号铺开；铺完只剩「整块棋盘的旋转/镜像」
   自由度，用「格号在范围内 + 全是黑格 + 跨度与目标一致」三条硬条件穷举 8 种朝向定死。
   **这里刻意不用单应来编号**：镜头畸变让「板平面 → 图像」在整个画面上不是单应，用单应编号会在
   画面中部产生半格以上的误差，足以把编号错开一格。
4. 每个内角点由相邻两个黑格各给一个估计，取平均。缺格 / 缺角点会报 `LB-B003`。

两条流程的差别：

| | 棋盘格 | ChArUco |
| --- | --- | --- |
| 编号 | 只有拓扑编号 | 拓扑编号 + **ArUco 标记交叉核对** |
| 标记 | 无 | 先按 LB-003 检出标记，再用「黑格中心 → 观测中心」的单应把标记反投影成格号，核对「该格应有的标记 id」是否与检出 id 一致；对不上报 `LB-B004`（error，结果不可用于标定） |
| 候选剔除 | — | 已识别的标记四边形（黑边环）会被排除在「黑格候选」之外 |

自检命令：

```bash
liveblocks calibrate corners --image fixtures/scenes/normal.png --target checkerboard-9x6-25mm
```

它打印 `CalibrationCorners` JSON（角点 id 与像素坐标、方法、黑格数、标记数与诊断）。

## 4. 标定文件 JSON 格式（草案 v0.0.1）

```json
{
  "schema_version": "0.0.1",
  "kind": "Calibration",
  "calibration_id": "calib-checkerboard-synthetic",
  "created_at": "2026-11-24T00:00:00Z",
  "status": "synthetic",
  "target": {
    "name": "checkerboard-9x6-25mm",
    "kind": "checkerboard",
    "squares_x": 9,
    "squares_y": 6,
    "square_mm": 25.0,
    "margin_mm": 20.0,
    "inner_corners": 40
  },
  "image": { "width": 480, "height": 360 },
  "image_count": 10,
  "point_count": 400,
  "intrinsics": { "fx": 459.618, "fy": 461.579, "cx": 239.224, "cy": 179.784 },
  "distortion": { "model": "plumb_bob", "k1": -0.260709, "k2": 0.081442, "p1": 0.006009, "p2": -0.003978 },
  "reprojection_error": {
    "rms_px": 0.109,
    "max_px": 0.629,
    "point_count": 400,
    "per_view_rms_px": [0.083, 0.104]
  },
  "views": [
    { "view_id": "view-00", "corners": 40, "rms_px": 0.083, "method": "checkerboard-grid" }
  ],
  "source": { "kind": "synthetic", "preset": "checkerboard", "seed": 20261124 },
  "notes": ["本文件由合成场景生成……"]
}
```

字段说明：

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `schema_version` | 是 | 草案版本（本文件为 `0.0.1`） |
| `kind` | 是 | 固定 `Calibration` |
| `calibration_id` | 是 | 非空字符串；同一仓库内应唯一 |
| `created_at` | 是 | ISO8601 时间戳（`2026-11-24T00:00:00Z` 或带 `+08:00` 偏移） |
| `status` | 否 | `synthetic` / `capture`：**必须如实标注**，合成结果不得冒充实拍 |
| `target` | 是 | 标定板定义（形状见 §2） |
| `image` | 是 | 标定时的图像尺寸；**换分辨率必须重新标定** |
| `image_count` | 是 | 参与标定的图像张数（≥ 3） |
| `point_count` | 否 | 参与标定的角点总数 |
| `intrinsics` | 是 | `fx, fy, cx, cy`（`fx, fy > 0`） |
| `distortion` | 是 | `model`（当前只支持 `plumb_bob`）与 `k1, k2, p1, p2` |
| `reprojection_error` | 是 | `rms_px`（≥0）、`max_px`（≥ `rms_px`）、可选 `per_view_rms_px` |
| `views` | 否 | 每个视图的角点数与残差，便于定位坏图 |
| `source` | 否 | 来源（预设名/目录、种子） |
| `notes` | 否 | 已知限制与提醒（字符串数组） |

时间戳的口径：

- `calibrate run` 默认取**当前 UTC 时间**（真实标定就该这样）；
- `calibrate report` 默认用**固定值** `2026-11-24T00:00:00Z`，这样报告与 golden 可以逐字节复现；
  要写真实时间就显式给 `--created-at`。

校验：`liveblocks calibrate validate --calib <file>`，问题码统一 `LB-B008`。
零依赖的校验器在 `liveblocks/calib/format.py:validate_calibration`（不引入 jsonschema）。

## 5. 求解算法

1. **单应**：每个视图用归一化 DLT（Hartley 归一化 + 正规方程 + Jacobi 特征分解）求
   「板平面毫米 → 图像像素」的单应 `H`。
2. **闭式初值（Zhang）**：由 `h1ᵀ·B·h2 = 0` 与 `h1ᵀ·B·h1 = h2ᵀ·B·h2` 解出 `B` 的最小特征向量，
   拆出 `fx, fy, cx, cy`；每个视图的位姿由 `K⁻¹·H` 正交化得到。这一步不含畸变，只是初值。
3. **联合精化**：对「8 个内参/畸变参数 + 每视图 6 个位姿参数」做 Levenberg–Marquardt，
   位姿块按视图独立，用 **Schur 补**消去后只解一个 8×8 系统。
   为什么不交替解两个块：交替坐标下降在「畸变 ↔ 位姿 ↔ 主点」的强耦合上会停在
   重投影只剩 0.3 px 的**错误**点上（实测连无噪声的理想数据都会被拉偏 fx 7%、cx 15 px）；
   联合解同一份数据能回到真值附近。
4. **一轮外点剔除**：残差超过 `max(6 × RMS, 2 px)` 的观测不参与第二轮拟合（每视图至少留 6 个点，
   否则整条保留）。目的是挡住「编号错一格」「板上有脏点」这类孤立错误。

重投影误差定义：全部观测点上 `‖project(K, 畸变, 位姿, X) − 观测‖` 的均方根（`rms_px`）与最大值
（`max_px`）。**它衡量的是「估计出的模型能不能解释这批图像」，不是「这台相机有多好」**。

## 6. 诊断码（`LB-B0xx`）

| 码 | 标题 | 触发场景 |
| --- | --- | --- |
| `LB-B001` | 标定板方块检出不足 | 一帧里没有检出任何黑格（板不在画面里、对比度不足） |
| `LB-B002` | 格点编号（网格自洽）失败 | 黑格之间的邻接关系与目标棋盘不符，或部分黑格与最大棋盘块不连通 |
| `LB-B003` | 内角点缺失 | 内角点数量少于目标定义（板没完整入画、被遮挡） |
| `LB-B004` | ChArUco 标记不足 | 检出标记少于 2 个（解不出单应），或标记 id 与编号对不上 |
| `LB-B005` | 标定视图不足 | 可用视图少于 3 个（`calibrate run` / `report` 直接失败） |
| `LB-B006` | 重投影误差超过阈值 | `calibrate report` 的验收条目（阈值见预设 `thresholds`） |
| `LB-B007` | 校正前后角点重复性超过阈值 | `calibrate report` 的验收条目 |
| `LB-B008` | 标定文件字段缺失或类型不符 | `calibrate validate` 的校验问题 |

## 7. 已知限制与未覆盖

1. **只支持零倾斜、方形像素**：`plumb_bob` 不含倾斜项 `s`；`fx` 与 `fy` 分别估计，但两者相关。
2. **径向只到 `k2`**：`k3` 未纳入（留位）。强鱼眼镜头需要先确认模型是否够用。
3. **方板歧义**：`squares_x == squares_y` 时「转置」编号在几何上无法区分，本实现会因跨度检查而
   拒绝（报 `LB-B002`）。请用非方板（本仓库的 9×6 / 8×6）。
4. **板面内旋转需远离 45° 的整数倍**：编号的第一步靠「图像里最靠左上的角点」定起点，
   板面内旋转接近 45° 时不同格子的起点可能不一致（本仓库预设都在 ±26° 内）。
5. **主点与 `k2` 是弱约束**：合成场景 9~10 个视图下，`fx/fy` 能回到 0.1% 内、主点 1 px 内，
   但 `k2` 的偏差可达 0.04（见 `docs/PROGRESS.md` 的实测数字）。真实标定要更多样化的位姿。
6. **重复性不会因为校正而变好**：跨次重复性由检角算法与噪声决定（合成图上约 0.02~0.03 px），
   校正带来的重采样会让它略微变差（实测 0.021 → 0.023 px）。校正真正改善的是
   「画面能不能被一个针孔单应解释」：同一静态场景的针孔单应残差从 0.80 px 降到 0.10 px。
   报告同时打印两组数字，避免把「重复性」当成「校正有效」的证据。
7. **分辨率不可复用**：标定文件绑定 `image.width/height`；换分辨率必须重新标定（按比例缩放在 §10 待办）。
8. **未覆盖**：立体标定 / 手眼标定、卷帘快门、按时间变化的畸变、彩色与 RAW 输入。

## 8. 待人工验证（真实摄像头）

以下条目**只能由人在实物上验**，代理不得代填、不得宣称通过：

1. **真实摄像头重投影误差**
   - 为什么不能代填：本报告的数字全部来自合成图。要声称某个真实摄像头的重投影误差，必须由人在实物上采集并跑一遍标定。
   - 人工步骤：① 按 `liveblocks calibrate targets` 给出的参数打印标定板，并用卡尺量 3 处方块边长；
     ② 用 LB-030 采集台（`liveblocks serve-cam serve`）抓 15 张以上、覆盖画面四角、带 20°~30° 倾角的板图，
     落进 `fixtures/live/<时间戳>/`；③ 运行 `liveblocks calibrate run --dir fixtures/live/<时间戳> --target <板名> --out calib-<设备>.json`；
     ④ 把打印出的重投影 RMS 连同设备型号、分辨率记进 `docs/PROGRESS.md` 的「待硬件验证」，人确认后才能移到「已验证」。
2. **可用拍摄距离 / 识别距离**
   - 为什么不能代填：本单不声明任何真实设备的可用距离或景深；合成图里的 z 值只是渲染参数。
   - 人工步骤：在真实摄像头上按目标距离摆板，跑 `liveblocks recognize report` 记录检出率与角点误差，
     结论必须附实测记录（设备型号 + 距离 + 帧数 + 漏检数）。
3. **板面平整度与打印尺寸误差**
   - 为什么不能代填：合成板是理想平面、方块边长完全精确；打印板或亚克力板的实际平整度与尺寸误差会影响真实标定。
   - 人工步骤：用卡尺量方块边长（记 3 处以上）并与 `calib_presets.json` 里的 `square_mm` 对比，
     把实测值写进 Issue，不要沿用标称值当作已实测。

## 9. 协议缺口（本单只报告、不改 `docs/PROTOCOL.md`）

1. `PROTOCOL.md` §2 的 `Frame` 只有 `calibration_id` 字符串，**没有引用标定文件**（文件名/哈希/内联），
   也没有「标定文件对象」这个 `kind`——本单把标定文件定义在本文档里，等协议冻结时再并入 §2。
2. §2 的 `distortion` 只有 `plumb_bob` 五个字段，没有版本号与系数取值范围约定。
3. 没有规定「同一分辨率之外如何缩放内参」，也没有规定多机/多镜头的组织方式。
4. §9 的错误码表没有采集/标定侧区间：本单占用 `LB-B0xx`（`LB-C0xx` 已被 LB-030 采集层占用，见 `docs/CAPTURE.md` §5），建议并入 §9 时统一编号。

## 10. 待办

1. 把标定文件对象并入 `PROTOCOL.md` §2，并在 `Frame` 里加 `calibration_ref`（与 LB-030 的
   `device` 字段一起做）。
2. 分辨率缩放规则（`fx' = fx · w'/w` 之类）与「标定文件过期」判定。
3. 与 OpenCV `CharucoBoard` 的编号顺序做**逐格交叉核对**（装得上 `opencv-python` 时在测试里跑）。
4. 稳健性：RANSAC 编号、按视图剔除整张坏图、把外点剔除做成多轮。
5. 更强的畸变模型（`k3`、鱼眼）与立体标定。
