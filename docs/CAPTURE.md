# 序界（LiveBlocks）摄像头采集台（LB-030）— 草案

> **状态：草案（draft），`capture_version` 0.0.1，未冻结。**
> 本文件描述 LB-030 的采集服务与页面：它**调用** `docs/PROTOCOL.md` §3 的识别层拿候选，
> 把帧与人工标注落成**能被 `recognize report` 直接消费**的场景目录，并把「识别错了」的样本
> 单独归档。**不改** `docs/PROTOCOL.md`，协议缺口只在 §8 报告。
> 服务名 `lb-030-serve-cam`，一条命令起本地服务，**零新增依赖**（HTTP 用标准库 `wsgiref`，
> 页面是一个内联 CSS/JS 的 HTML 字符串，叠加图用 LB-008 的确定性光栅 + 仓库自带 PNG 编码器）。
>
> **验收状态（如实登记，别读错）**：① ② ③ 是离线可复现的，本 PR 有真跑证据（§7）；
> ④「在手机上选后置摄像头、对着一张实体卡看到叠加框与卡片 id」**待人工验证**——代理没有手机、
> 没有实体卡、没有相机，任何「已通过」都是伪造。人工步骤见 §6。

## 0. 范围与边界

- **一条命令起服务**：`liveblocks serve-cam serve` → 打印 `http://127.0.0.1:8765/`，浏览器打开即可。
- **摄像头不锁型号**：手机前置/后置（`facingMode` 预设）、笔记本内置、USB UVC 全部走
  `getUserMedia` 同一条路径；页面的设备下拉来自 `enumerateDevices`。
- **单帧抓拍 + 连续取帧**：都是「`video` → `canvas` → 手写灰度转换 → `POST /api/frame`」，
  识别在服务端跑（浏览器不需要任何 PNG 编码库，服务端也不需要解码彩图）。
- **落盘的是场景目录**：`fixtures/live/<时间戳>/` 与 `fixtures/scenes/` 同构，
  `liveblocks recognize report --dir fixtures/live/<id>` 直接能跑。
- **人工标注**：卡片 id（必填）、角色（可选）、角点（可选）、备注；「这帧识别错了」→ 单独归档。
- 不做：视频录制与上传、多用户与账号、**不做公网鉴权**（公网入口与 token 由运维侧隧道负责）、
  不改 `docs/PROTOCOL.md`、不改 `docs/ROADMAP.md` 的范围与优先级、不做固件/烧写/硬件联调。

## 1. 一条命令

```bash
# 起服务（默认只绑 127.0.0.1:8765；Ctrl-C 停止）
liveblocks serve-cam serve

# 换端口 / 换采集根目录 / 指定默认卡纸版式
liveblocks serve-cam serve --port 9000 --live-dir fixtures/live --card-layout card-60x40

# 只打印地址与采集目录就退出（脚本/自检用，不绑端口）
liveblocks serve-cam serve --print-url-only

# 把页面导成单个 HTML：断网也能打开（验收③的取证方式之一）
liveblocks serve-cam page --out /tmp/lb030-page.html
```

`--host 0.0.0.0` 会把服务暴露到局域网：手机上直接访问需要 **HTTPS 或 localhost** 才能用
`getUserMedia`，所以实际做法是运维侧的隧道（带 token）把本地端口转成 HTTPS 地址；
**本服务不做鉴权**，这一条属于「不做」范围。

## 2. HTTP API

所有 JSON 响应的第一层都有 `kind` 与 `schema_version`；出错时返回
`{"kind": "`ServeCamError`", "error": {"code": "LB-C00x", "title": ..., "message": ...}}`。

| 方法与路径 | 作用 | 主要响应 `kind` |
| --- | --- | --- |
| `GET /` | 自包含页面（内联 CSS/JS，无外链、无 CDN） | —（`text/html`） |
| `GET /api/health` | 存活检查：服务名、版本、采集目录、样本计数 | `ServeCamHealth` |
| `GET /api/info` | 页面元数据：卡面版式、卡片字典、状态枚举、媒体类型、路由表 | `ServeCamInfo` |
| `POST /api/frame` | 识别一帧 → §3 候选 + 识别叠加图 | `FrameRecognitionResult` |
| `POST /api/capture` | 当前帧 + 人工标注落盘 | `CaptureResult` |
| `GET /api/captures` | 列出已采样本（含 `refused`） | `LiveCaptureList` |
| `GET /api/captures/<id>/<file>` | 读回样本里的 `frame.png` / `scene.json` / `capture.json` / `index.json` | 文件字节 |
| `GET /api/frames/<token>/<file>` | 读回内存里最近几帧的 `overlay.png` / `frame.png` | 文件字节 |

### 2.1 `POST /api/frame` 的三种请求体

| `Content-Type` | 请求体 | 谁在用 |
| --- | --- | --- |
| `image/png` | 8bit 灰度 PNG（`fixtures/scenes/*.png` 就是这种） | 测试与命令行（验收①：与 `recognize detect` 逐字段对照） |
| `application/x-liveblocks-gray` | 裸灰度字节 + `?width=&height=` | 页面（浏览器只做灰度转换，不编码 PNG） |
| `application/json` | `{"width","height","gray_base64"}` 或 `{"png_base64"}` | curl / 脚本 |

`frame_id` 的解析顺序与 `recognize detect` 一致：`?frame_id=` → `Content-Disposition` 里的
`filename`（`normal.png` → `f-normal`，与命令行逐字段可比）→ 采集台自生成的时间戳 id。
`?card_layout=` 可临时换卡纸版式（页面上的「卡纸版式」输入框）。

响应（节选，字段以 `kind` 为 `FrameRecognitionResult` 的这份为准）：

```json
{
  "kind": "FrameRecognitionResult",
  "schema_version": "0.0.1",
  "capture_version": "0.0.1",
  "frame": { "kind": "Frame", "frame_id": "f-normal", "width": 640, "height": 480, "source": "camera" },
  "card_candidate_list": {
    "kind": "CardCandidateList",
    "frame_id": "f-normal",
    "cards": [{ "candidate_id": "c-0", "card_id": "C01", "status": "ok" }]
  },
  "marker_candidate_list": { "kind": "MarkerCandidateList", "markers": [] },
  "card_layout": "card-60x40",
  "frame_token": "0f1e2d3c4b5a6978",
  "overlay": {
    "content_type": "image/png",
    "width": 640,
    "height": 480,
    "png_base64": "…",
    "url": "/api/frames/0f1e2d3c4b5a6978/overlay.png"
  }
}
```

- `card_candidate_list` 就是 `recognize detect` 命令行 stdout 的**同一份文档**（同一段代码、同一
  注册表、同一 `frame_id`），验收①逐字段比对的就是它。只有 `frame` 这一层不同：采集台的
  `source` 是 `camera`、`captured_at_ms` 是真实墙钟，命令行的默认值是 `synthetic` / `0.0`——
  这两个字段不进 §3 候选，所以 §3 逐字段一致不受影响；
- `overlay` 是在原帧上画了标记框/卡片范围/卡片 id 的 PNG，**尺寸与输入帧一致**，只含 ASCII 标签；
- `frame_token` 指向内存里这一帧（只留最近 16 帧）：`/api/capture` 凭它可以不二次上传直接用同一帧。

### 2.2 `POST /api/capture`

只收 `application/json`：图像要么给 `frame_token`（推荐，就是刚识别的那一帧），要么在同一个
JSON 里给 `png_base64` / `gray_base64` + `width`/`height`。

```json
{
  "frame_token": "0f1e2d3c4b5a6978",
  "card_layout": "card-60x40",
  "outcome": "accepted",
  "notes": "室内自然光，卡片距镜头约 25 cm",
  "annotations": [
    { "card_id": "C01", "role": "action", "corners_px": [[292,202],[348,202],[348,258],[292,258]], "notes": "前景卡" }
  ]
}
```

- `outcome`：`accepted`（人认为这帧识别结论可用）| `misrecognised`（页面的「这帧识别错了」，
  也接受等价写法 `refused`）；
- `annotations[]`：`card_id` 必填（必须在 `data/card_dictionary.json` 里，否则没有 `marker_id`，
  这张卡不会写进 `scene.json`，只留在 `capture.json` 里并给警告）；`role` / `corners_px` /
  `status` / `notes` 可选；也支持顶层 `card_id` / `role` / `corners` 的单卡简写；
- `corners_px` 是**标记四角**（左上、右上、右下、左下），与 §3.1 `corners_px` 同一个约定；
  给了就当真值用（`corner_source=manual`）。

响应 `kind` 为 `CaptureResult`，关键字段：`capture.directory`（落盘目录）、
`capture.cards[].match`（标注与识别是否一致）、`capture.report_usable`（是否写进了 `scene.json`）、
`capture.recognize_report_command`（可直接复制的命令）、`capture.warnings`（如「标记没检出，
借用了画面里唯一一个标记候选的角点」这类如实提示）。

### 2.3 `GET /api/captures`

返回 `kind` 为 `LiveCaptureList`：`counts`（accepted / misrecognised 计数）与 `captures[]`
（`capture_id` / `outcome` / `card_ids` / `matches` / `report_usable` / `frame_url` …），
可按 `?outcome=accepted|misrecognised|all` 过滤。

## 3. 落盘结构

一次采集 = 一个目录，**和 `fixtures/scenes/` 的单个场景同构**：

```
fixtures/live/<时间戳>/
├── frame.png        # 采集到的那一帧（8bit 灰度，仓库自带编码器，字节可复现）
├── scene.json       # `LiveCaptureScene`：recognize report 读的就是它
├── capture.json     # `LiveCaptureRecord`：人工标注 + 当时的 §3 识别结果 + 警告
└── index.json       # `LiveCaptureIndex`：这份样本的清单行（尺寸/像素摘要/张数）
```

「这帧识别错了」的样本落到 **`refused`** 子目录：`fixtures/live/refused/<时间戳>/`（同构目录）。
这样 `recognize report --dir fixtures/live/<id>` 看的是人认为对的那批，坏样本单独可跑、
不混进正常统计；两边的目录结构完全一样，命令只差路径。

`scene.json` 与合成真值**同一套字段**（`image.file` / `card_layout` / `cards[].marker_id` /
`marker_corners_px` / `center_px` / `status`），只把 `kind` 换成 `LiveCaptureScene`：

- `status`：人工标注给了就用它，否则记 `ok` 并写 `status_assumed: true`（缺省值不随识别结果变化，
  避免「拿检测结论当真值」）；
- `center_px` 与 `center_source`：实拍样本没有独立测量的卡片中心，
  一次拍摄的真相只有「标记四边形 + 卡面版式」能给的几何外推
  （`card-geometry-from-marker-quad`；四边形退化时退回 `marker-quad-centroid`）。
  因此报表里的「卡片中心误差」在实拍样本上只说明**几何自洽**，不是拍摄精度；
- `cards` 里只有拿到四角的卡：没有四角就**不猜坐标**，那张卡留在 `capture.json` 的
  `rejected_annotations` 里并给警告（`report_usable=false`），`scene.json` 不收它。

## 4. 与 `recognize report` 的关系

```bash
# 单个样本
liveblocks recognize report --dir fixtures/live/20261124T045500Z-3f2a1c

# 识别错了的那批
liveblocks recognize report --dir fixtures/live/refused/20261124T051000Z-9a8b7c
```

识别层的统计代码（`liveblocks.vision.stats.evaluate_directory`）接受两种真值 `kind`：
`SyntheticCardScene`（LB-002 合成场景）与 `LiveCaptureScene`（本层的实拍样本）。
两者字段同构是刻意的：**同一份统计、同一批阈值、同一套验收对照**，不需要第二套报表代码。
为什么不干脆写成 `SyntheticCardScene`：实拍与合成的可信度完全不同，`kind` 必须能分清
（见 `docs/DECISIONS.md` D-021）。

## 5. 错误码（`LB-C0xx`，不进 `PROTOCOL.md` §9）

| 码 | 标题 |
| --- | --- |
| `LB-C001` | 请求体不可解析 |
| `LB-C002` | 不支持的媒体类型 |
| `LB-C003` | 图像解码失败 |
| `LB-C004` | 未知路由 |
| `LB-C005` | 方法不被允许 |
| `LB-C006` | 人工标注不合法 |
| `LB-C007` | 帧令牌不存在或已过期 |
| `LB-C008` | 采集样本不存在 |
| `LB-C009` | 采集落盘失败 |
| `LB-C010` | 请求体过大 |
| `LB-C011` | 服务内部错误 |

## 6. 人工步骤（验收④，**待人工验证**）

> 这一条只能人做：需要手机、需要实体卡片、需要在浏览器里看画面。代理没有这些，
> 所以状态是 **待人工验证**，不得写成通过。

准备：手机与电脑在同一网络（或运维侧隧道给出的 HTTPS 地址）；一张 `data/card_dictionary.json`
里登记过的实体卡（例如 `C01`，24 mm 标记）；`liveblocks serve-cam serve --host 0.0.0.0` 起服务。

1. 手机浏览器打开 `https://<隧道地址>/`（或 `http://<电脑局域网 IP>:8765/`，若浏览器允许非安全上下文）；
2. 点「后置 / 环境相机」授权后置摄像头；设备下拉里应能看到两个以上摄像头；
3. 把实体卡放到镜头前（占画面约 1/3），点「单帧抓拍 → 识别」；
4. **预期**：页面右侧出现叠加图（标记框、卡片框、卡片 id 与状态标签），卡片 id 与手上这张卡一致
   （`C01` 显示 `C01`，`status` 为 `ok`）；
5. 勾「连续取帧」，慢慢移动卡片：叠加框应跟随（页面每 900 ms 一帧，可调间隔）；
6. 在「3. 人工标注并落盘」里填卡片 id → 「落盘这一帧」→ 页面显示目录与可直接复制的
   `recognize report` 命令；跑一遍，`预期 1 / 识别 1 / 漏检 0`；
7. （可选，必须人做）故意把卡片倒着放或遮住一角 → 叠加结果异常时勾「这帧识别错了」→ 落盘，
   确认目录出现在 `fixtures/live/refused/` 下。

失败时请记录：手机型号、浏览器与版本、页面上的错误文字（`LB-C0xx`）、叠加图截图、`capture.json`，
按 `.github/ISSUE_TEMPLATE/hardware-test.md` 开 Issue。**代理不得代填这条的结论。**

## 7. 验收对照（离线三条 + 人工一条）

| 验收 | 方式 | 状态 |
| --- | --- | --- |
| ① `pytest tests/test_serve_cam.py` 用 TestClient 上传 `fixtures/scenes/*.png`，§3 JSON 与 `recognize detect` 逐字段一致；叠加图 PNG 可解码且尺寸与输入一致 | `TestClient`（`liveblocks.serve_cam.testing`，进程内 WSGI，不起端口）对 6 个场景逐一比对命令行输出 | 离线，本 PR 真跑过（§7.1） |
| ② 采集目录能被 `recognize report` 直接消费 | 落盘后在同一测试里调用 `evaluate_directory` 与服务端子进程 `liveblocks recognize report` | 离线，本 PR 真跑过（§7.1） |
| ③ 页面无外网 CDN 依赖，断网可加载（相机权限除外） | 断言页面里没有外部 URL / 外部 `src` / `link`，且所有 `fetch` 都是相对路径；`serve-cam page` 导出的 HTML 可直接打开 | 离线，本 PR 真跑过（§7.1） |
| ④ 手机选后置摄像头、对实体卡看到叠加框与卡片 id | 见 §6 的人工步骤 | **待人工验证**（不填、不代签） |

### 7.1 复现命令

```bash
# 验收 ① ② ③ 的自动化部分（含叠加图解码与尺寸断言、采集目录 → recognize report）
pytest tests/test_serve_cam.py

# 全套（API 细节、采集仓库、页面、文档缺口核对）
pytest tests/test_serve_cam*.py

# 手工看一眼页面（导出单文件，断网打开）
liveblocks serve-cam page --out /tmp/lb030-page.html
```

## 8. 已知限制

1. **只有 8bit 灰度**：页面在浏览器里做灰度转换（`0.299R + 0.587G + 0.114B`），PNG 请求体只接受
   8bit 灰度 PNG（`synth.png.decode_gray` 的能力边界，见 `docs/DECISIONS.md` D-012）。
   彩色/16bit PNG 会被 `LB-C003` 拒绝——这是如实拒绝，不是静默降级。
2. **没有标定**：`Frame.intrinsics` 不填（标定属 LB-031），所以实拍样本的 §3 输出里没有位姿，
   报表的 `--no-intrinsics` 语义与合成场景一致。
3. **实拍真值只到「人工标注 + 几何外推」**：卡片中心由版式外推（不是独立测量），`status` 缺省
   `ok` 且标 `status_assumed`；四角拿不到的卡不进 `scene.json`（不猜坐标）。
4. **帧令牌是内存的**：只留最近 16 帧，进程重启后旧令牌失效（`LB-C007`）；落盘的 PNG/JSON 不受影响。
5. **不推荐把 `--host 0.0.0.0` 直接暴露公网**：本服务不做鉴权（「不做」的一条），
   公网入口必须由运维侧隧道加 token；手机上的 `getUserMedia` 还需要 HTTPS 或 localhost。
6. **页面只做「抓帧 → 标注 → 落盘」**：不做视频录制、不做多卡批量标注（一次一条标注；
   API 的 `annotations[]` 已支持多条，页面只填一条）、不做样本改判（采完就固定，
   要改判就重采或在 `capture.json` 里如实记一笔后重采）。
7. **叠加图只画识别层**：不画排列（LB-004 的槽位），因为采集台的目标是「采符合识别层口径的
   样本」；要五层视图请用 `liveblocks ui view`（LB-008）。
8. **服务是单进程多线程**：并发写只会产生两个独立目录，不会互相覆盖；不做限流与鉴权。

## 9. 发现的协议缺口（只报告，不改 `docs/PROTOCOL.md`）

1. §2 `Frame` 没有「帧来自哪个摄像头 / 设备标识」的字段：实拍样本只能把设备信息写进
   `capture.json` 的自定义字段（本层没写，因为页面拿到的 `deviceId` 只在浏览器里有意义）；
2. §2 没有「帧的原始分辨率 vs 识别分辨率」的表示：本层在 `frame` 里存识别用的尺寸，
   原始分辨率只在 `capture.json` 的 `notes` 里由人记；
3. §3 没有「这一帧是谁采的、用哪份卡纸」的采集元数据位置：本层用 `capture.json` 的
   `card_layout` + `frame` 装，未进协议；
4. §9 没有「采集侧」的错误码区间：本层新增 `LB-C0xx`（见 §5），未进 §9；
5. §3 没有「候选的角点来源（检出/人工）」的表达：本层用 `scene.json` 的 `corner_source`
   与 `center_source` 加法式字段，属于真值侧而非协议对象；
6. 没有定义「实拍场景真值」的对象：本层用 `LiveCaptureScene`（与合成真值字段同构）补这个空，
   若将来协议要正式定义，应把它并入 §2 或新增一节。
