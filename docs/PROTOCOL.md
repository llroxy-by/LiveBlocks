# 序界（LiveBlocks）层间协议 — 草案

> **状态：草案（draft）v0.0.1，未冻结，随时可能变更。**
> 任何消费者必须容忍「新增字段」和「枚举新增值」；破坏性变更会提升 `schema_version` 并在 `docs/DECISIONS.md` 记录。
> 本文件不含实现，只约定字段语义、约束与错误模型。

## 0. 约定

- 序列化：JSON（UTF-8，`application/json`）。落盘运行记录用 JSONL，一行一个事件。
- 每个顶层对象都带 `schema_version`（字符串，`major.minor`）与 `kind`（对象类型名）。
- 未知字段：忽略但保留（消费者不得因未知字段失败）。
- 未知枚举值：必须降级为 `unknown` 而不是报错；`unknown` 必须在诊断中体现。
- 坐标与单位：像素为整数，坐标原点在图像左上角，x 向右、y 向下；角度为度，逆时针为正；长度单位为毫米（mm）；时间戳为毫秒（`float`，单调递增即可，不要求墙钟时间）。
- 标识符：`card_id`、`marker_id`、`step_id` 等为字符串，不假设是数字。

## 1. 分层消息一览

| 层 | `kind` | 生产者 | 消费者 |
| --- | --- | --- | --- |
| S1 | `Frame` | capture | vision.* |
| S2 | `MarkerCandidateList` / `CardCandidateList` | vision.detector / vision.card | vision.card / layout |
| S3 | `Layout` | layout | parser, ui.debug |
| S4 | `Ast` | parser | ir, ui.debug |
| S5 | `Ir` | ir | runtime, backend.*, ui.debug |
| S6 | `ExecEvent` / `ExecResult` | runtime / backend.* | feedback, ui.debug |
| S7 | `FeedbackCommand` | feedback | ui.debug / backend.hw |

## 2. S1 `Frame`

```json
{
  "schema_version": "0.0.1",
  "kind": "Frame",
  "frame_id": "f-000123",
  "captured_at_ms": 12345.6,
  "source": "synthetic",
  "width": 1280,
  "height": 720,
  "calibration_id": "calib-default",
  "intrinsics": { "fx": 900.0, "fy": 900.0, "cx": 640.0, "cy": 360.0 },
  "distortion": { "model": "plumb_bob", "k1": 0.0, "k2": 0.0, "p1": 0.0, "p2": 0.0 },
  "image_ref": "frames/f-000123.png"
}
```

- `source`：`camera` | `synthetic` | `replay`。合成帧与回放帧必须显式标注，避免与真实采集混淆。
- `image_ref` 可为相对路径或内容哈希；运行时不得内联大图到 JSON。

## 3. S2 识别层

### 3.1 `MarkerCandidateList`（标记候选）

```json
{
  "schema_version": "0.0.1",
  "kind": "MarkerCandidateList",
  "frame_id": "f-000123",
  "dictionary": "DICT_4X4_50",
  "markers": [
    {
      "marker_id": "17",
      "corners_px": [[100, 200], [160, 200], [160, 260], [100, 260]],
      "center_px": [130, 230],
      "angle_deg": 3.2,
      "confidence": 0.93,
      "pose": { "x_mm": 12.0, "y_mm": -4.5, "z_mm": 210.0, "yaw_deg": 3.2 }
    }
  ]
}
```

- 角点顺序固定为标记自身的左上、右上、右下、左下（按标记坐标系），不做「图像上方」解释。
- `confidence` ∈ [0,1]，来源与算法由实现决定，协议只要求可比。

### 3.2 `CardCandidateList`（卡片候选）

```json
{
  "schema_version": "0.0.1",
  "kind": "CardCandidateList",
  "frame_id": "f-000123",
  "cards": [
    {
      "candidate_id": "c-7",
      "marker_ids": ["17"],
      "card_type": "action",
      "card_face": "move-forward",
      "orientation_quadrant": 0,
      "center_px": [130, 230],
      "confidence": 0.93,
      "status": "ok"
    }
  ]
}
```

- `status`：`ok` | `unknown_card`（标记在字典内但未登记） | `occluded` | `clipped` | `ambiguous`。
- `orientation_quadrant`：0–3，表示卡片相对标记坐标系的 90° 倍数旋转；具体与阅读方向的关系由 S3 解释。
- 识别层**不得**排序，也不得丢弃低置信候选；筛选规则属于 S3。

## 4. S3 `Layout`（排列）

```json
{
  "schema_version": "0.0.1",
  "kind": "Layout",
  "frame_id": "f-000123",
  "reading_direction": "ltr_ttb",
  "rows": [
    {
      "row_index": 0,
      "slots": [
        { "slot_index": 0, "candidate_id": "c-7", "card_face": "move-forward", "orientation_quadrant": 0, "x_px": 130, "y_px": 230, "confidence": 0.93 }
      ]
    }
  ],
  "diagnostics": [
    { "code": "LB-L001", "severity": "warning", "message": "候选 c-9 与 c-12 间距低于阈值，排列顺序存疑", "candidate_ids": ["c-9", "c-12"] }
  ]
}
```

- `reading_direction`：`ltr_ttb`（默认，左上→右下）| `rtl_ttb` | `ttb_ltr`，可配置。
- 排列必须携带排序依据：`row_index`、`slot_index` 及其来源候选 id；不得只给一张「结果图」。
- 重复候选去除后，被去除候选需在 `diagnostics` 留痕（`LB-L002`）。

## 5. S4 `Ast`（语法树）

```json
{
  "schema_version": "0.0.1",
  "kind": "Ast",
  "grammar_version": "0.0.1",
  "root": {
    "node": "Program",
    "span": { "row": 0, "slot_from": 0, "slot_to": 2 },
    "body": [
      {
        "node": "Command",
        "span": { "row": 0, "slot_from": 0, "slot_to": 0 },
        "name": "move-forward",
        "args": [{ "node": "Number", "value": 1, "span": { "row": 0, "slot_from": 1, "slot_to": 1 } }],
        "origin": { "candidate_id": "c-7", "card_face": "move-forward" }
      }
    ]
  },
  "diagnostics": []
}
```

- 每个节点都带 `span`（行 + 槽位区间）与 `origin`（来源卡片），用于把错误指回实体卡片。
- 文法未定稿（见 `docs/ARCHITECTURE.md` Q4/Q5）；`node` 取值集合是开放枚举。

## 6. S5 `Ir`（规范化中间表示）

```json
{
  "schema_version": "0.0.1",
  "kind": "Ir",
  "ir_version": "0.0.1",
  "program": {
    "steps": [
      { "step_id": "s0", "op": "move", "args": { "distance": 1, "unit": "cell" }, "source": { "row": 0, "slot": 0 } }
    ],
    "limits": { "max_steps": 1000, "timeout_ms": 5000 }
  },
  "diagnostics": []
}
```

- 必须**确定性**：同一 AST 两次规范化结果字节一致（稳定键序、数字格式固定、无时间戳/随机量）。
- `limits` 是执行器的硬约束，缺省由运行时补齐，但规范化后必须显式出现。
- 只允许 `op` 取值来自文法定义；未知 `op` 属于规范化的错误，不是运行时错误。

## 7. S6 执行层

### 7.1 `ExecEvent`

```json
{ "schema_version": "0.0.1", "kind": "ExecEvent", "run_id": "run-0007", "seq": 3, "at_ms": 12.4, "step_id": "s0", "phase": "finish", "backend": "sim", "detail": { "state_delta": { "pos": 1 } } }
```

- `phase`：`start` | `finish` | `error` | `log` | `feedback`。
- `backend`：`sim` | `hw`。任何来自模拟器的事件都必须标 `sim`，禁止以模拟结果冒充硬件验证。
- `seq` 在单次运行内严格递增，`at_ms` 为相对运行起点的毫秒数。
- `backend:"hw"` 的串口帧格式、命令 / 事件表与 `LB-H0xx` 链路错误码见 `docs/HW.md`（LB-033，草案）；
  那一路的证据来自 pty 上的设备模拟器，属于模拟器证据，不是硬件验证。

### 7.2 `ExecResult`

```json
{ "schema_version": "0.0.1", "kind": "ExecResult", "run_id": "run-0007", "status": "ok", "steps_executed": 1, "duration_ms": 12.4, "backend": "sim", "diagnostics": [] }
```

- `status`：`ok` | `parse_error` | `runtime_error` | `timeout` | `aborted` | `limit_exceeded`。

### 7.3 S7 `FeedbackCommand`

```json
{ "schema_version": "0.0.1", "kind": "FeedbackCommand", "run_id": "run-0007", "target": "ui_overlay", "channel": "highlight", "payload": { "step_id": "s0", "color": "green" }, "ttl_ms": 800 }
```

- `target`：`ui_overlay` | `virtual_display` | `hw_display` | `hw_led`（后三者为后续阶段能力，草案保留）。
- 反馈命令必须幂等或带 `ttl_ms`，避免状态卡死。

## 8. 运行记录（JSONL）

一次运行一个文件，首行 `RunHeader`（含 `run_id`、输入帧引用、各层 `schema_version`、实现版本、`backend`），随后按产出顺序写入 `MarkerCandidateList` / `CardCandidateList` / `Layout` / `Ast` / `Ir` / `ExecEvent`* / `ExecResult`。运行记录是人工测试与回归的证据载体。

## 9. 错误与诊断码

| 区间 | 域 | 示例 |
| --- | --- | --- |
| `LB-C0xx` | 采集/图像 | `LB-C001` 帧尺寸与标定不符 |
| `LB-V0xx` | 识别 | `LB-V002` 标记被裁切 |
| `LB-L0xx` | 排列 | `LB-L001` 间距歧义、`LB-L002` 重复候选被去除 |
| `LB-P0xx` | 语法 | `LB-P003` 期望参数卡但槽位为空 |
| `LB-I0xx` | 规范化 | `LB-I001` 未知 `op` |
| `LB-R0xx` | 执行 | `LB-R002` 步数上限、`LB-R003` 超时 |
| `LB-H0xx` | 硬件后端 / 串口链路（LB-033） | `LB-H001` 串口断连、`LB-H005` 序号乱序（码表见 `docs/HW.md` §6） |

诊断对象统一形状：`{ "code": string, "severity": "info"|"warning"|"error", "message": string, "candidate_ids"?: string[], "span"?: object }`。

## 10. 兼容性规则（草案）

1. 只加字段、不删字段、不改字段语义 → 保持 `major`，提升 `minor`。
2. 删除字段、改语义、改必填性 → 提升 `major`，并在 `docs/DECISIONS.md` 写迁移说明。
3. 层与层之间的生产者/消费者必须同时声明其实现的 `schema_version` 区间；跨层测试用固定基准文件（`fixtures`）验证。

## 11. 明确未定义（待决）

- 卡片字典的登记格式（`card_type` / `card_face` 的完整枚举）。
- 多标记卡片（角标）在 `marker_ids` 中的语义与匹配算法。
- 硬件后端的事件时序保证（延迟抖动、丢帧）。
- 反馈通道在首阶段的具体取值（见 Q6）。

以上待决项在冻结前随时可能改动，实现不得依赖其稳定性。
