# `fixtures/live/` — LB-030 实拍采集样本（不入库）

这个目录是 `liveblocks serve-cam serve` 的落盘根目录。**除了本文件，这里的内容都不入库**
（见 `.gitignore`）：实拍样本是人现场采的，一采就变，没有「同种子重生成」这回事。

## 结构

```
fixtures/live/
├── <时间戳>/            # 人判可用（outcome=accepted）
│   ├── frame.png        # 那一帧（8bit 灰度，仓库自带 PNG 编码器）
│   ├── scene.json       # kind=LiveCaptureScene —— recognize report 读的真值
│   ├── capture.json     # kind=LiveCaptureRecord —— 人工标注 + 当时的 §3 识别结果 + 警告
│   └── index.json       # kind=LiveCaptureIndex —— 清单行（尺寸/像素摘要/张数）
└── refused/<时间戳>/    # 人判「这帧识别错了」（outcome=misrecognised），结构相同
```

## 怎么用

```bash
# 1. 起采集台，浏览器打开打印出来的地址
liveblocks serve-cam serve

# 2. 采样后，每个子目录就是一个场景目录
liveblocks recognize report --dir fixtures/live/<时间戳>
liveblocks recognize report --dir fixtures/live/refused/<时间戳>
```

## 真值边界（别抬高可信度）

- `card_id` / `role` / 角点来自**人工标注**，不是测量；
- `status` 缺省记 `ok` 并写 `status_assumed: true`（不拿检测结论冒充真值）；
- `center_px` 由「标记四边形 + 卡面版式」外推，`center_source` 写明来源——因此报表里的
  「卡片中心误差」在实拍样本上只说明几何自洽，不是拍摄精度；
- 四角拿不到的卡**不会**写进 `scene.json`（不猜坐标），只留在 `capture.json` 的
  `rejected_annotations` 里并给警告。

细节与错误码见 `docs/CAPTURE.md`。
