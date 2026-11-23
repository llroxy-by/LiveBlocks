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

## 3. 约定

1. 夹具必须是**确定性**的：生成脚本固定随机种子，重跑字节一致。
2. 每个夹具都要能追溯到生成它的脚本与提交（在 PR/Issue 中说明）。
3. 单个体积较大的二进制夹具（如整帧图像）入库前先确认仓库体积可接受：当前 `scenes/`
   共 6 张 640×480 灰度 PNG，合计约 0.95 MB。
4. fixtures 只由 `liveblocks synth` 生成，**不要手改**；手改会让 `synth verify` 与
   `tests/test_synth_reproducibility.py` 失败。
