"""LB-030 采集台的对外契约：HTTP 路由、请求/响应 ``kind``、错误码与枚举。

这一层**不改** ``docs/PROTOCOL.md``：§3 的 ``CardCandidateList`` 原样返回（``/api/frame`` 的
``card_candidate_list`` 字段就是 ``recognize detect`` 命令行的同一份文档），本模块只定义
「采集台自己的壳」——HTTP 状态码、响应 ``kind``、``LB-C0xx`` 错误码与落盘文件的 ``kind``。

约定（与 ``docs/CAPTURE.md`` 一一对应，``src/liveblocks/serve_cam/docs.py`` 会机器核对）：

- 所有 JSON 响应的第一层都有 ``kind`` 与 ``schema_version``；
- 出错时返回 ``{"kind": "ServeCamError", "error": {"code": "LB-C00x", "message": ...}}``；
- ``LB-C`` 是「采集台（Capture）」的码区间，未进 ``PROTOCOL.md`` §9（本阶段只报告，不改协议）。
"""

from __future__ import annotations

#: 采集台契约版本（独立于 ``PROTOCOL.md`` 的 ``schema_version``）。
CAPTURE_VERSION = "0.0.1"

#: 服务名（``/api/health`` 与页面标题里出现，便于确认连的是哪个进程）。
SERVICE_NAME = "lb-030-serve-cam"

#: HTTP 路由（文档核对与测试共用，避免字符串散落）。
ROUTE_INDEX = "/"
ROUTE_HEALTH = "/api/health"
ROUTE_INFO = "/api/info"
ROUTE_FRAME = "/api/frame"
ROUTE_CAPTURE = "/api/capture"
ROUTE_CAPTURES = "/api/captures"
ROUTE_CAPTURE_FILE = "/api/captures/<capture_id>/<file_name>"
ROUTE_FRAME_FILE = "/api/frames/<frame_token>/<file_name>"

#: 页面对外只暴露这一个入口；静态资源全部内联（断网可加载，验收③）。
STATIC_ROUTE_PREFIX = "/static/"

#: 响应 / 落盘文档的 ``kind``。
KIND_ERROR = "ServeCamError"
KIND_HEALTH = "ServeCamHealth"
KIND_INFO = "ServeCamInfo"
KIND_FRAME_RECOGNITION = "FrameRecognitionResult"
KIND_CAPTURE = "LiveCaptureRecord"
KIND_CAPTURE_RESULT = "CaptureResult"
KIND_CAPTURE_LIST = "LiveCaptureList"
KIND_CAPTURE_SCENE = "LiveCaptureScene"
KIND_CAPTURE_INDEX = "LiveCaptureIndex"

#: 一帧请求体的三种写法（页面用 raw-gray，测试与命令行用 PNG）。
MEDIA_PNG = "image/png"
MEDIA_GRAY = "application/x-liveblocks-gray"
MEDIA_JSON = "application/json"

#: 单帧请求体上限（16 MiB）：本地单用户服务，超大请求直接拒绝而不是吃满内存。
MAX_BODY_BYTES = 16 * 1024 * 1024

#: 帧令牌缓存容量（连续取帧时只保留最近几帧，``/api/capture`` 用令牌取回同一帧）。
FRAME_TOKEN_CAPACITY = 16

#: 采集结论：``accepted`` 入库到 ``fixtures/live/<id>/``；``misrecognised``（页面的
#: 「这帧识别错了」）单独归档到 ``fixtures/live/refused/<id>/``。
OUTCOME_ACCEPTED = "accepted"
OUTCOME_MISRECOGNISED = "misrecognised"
OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_MISRECOGNISED)

#: 人工标注里的卡片角色。允许自由文本（真实卡片可能不在字典里），但常见值由页面下拉给出。
ROLE_VALUES = ("action", "control", "number", "operator", "unknown")

#: 标注角点的来源，落盘时如实记录，供人工复核时判断这份真值有多可信。
CORNER_SOURCE_DETECTED = "detected"
CORNER_SOURCE_MANUAL = "manual"
CORNER_SOURCE_SINGLE_MARKER = "detected-single-marker"

#: ``LB-C0xx`` 码表（错误码 → 标题）。文档核对与测试共用。
ERROR_CODES: dict[str, str] = {
    "LB-C001": "请求体不可解析",
    "LB-C002": "不支持的媒体类型",
    "LB-C003": "图像解码失败",
    "LB-C004": "未知路由",
    "LB-C005": "方法不被允许",
    "LB-C006": "人工标注不合法",
    "LB-C007": "帧令牌不存在或已过期",
    "LB-C008": "采集样本不存在",
    "LB-C009": "采集落盘失败",
    "LB-C010": "请求体过大",
    "LB-C011": "服务内部错误",
}

#: 错误码 → HTTP 状态码。
ERROR_STATUS: dict[str, int] = {
    "LB-C001": 400,
    "LB-C002": 415,
    "LB-C003": 400,
    "LB-C004": 404,
    "LB-C005": 405,
    "LB-C006": 400,
    "LB-C007": 409,
    "LB-C008": 404,
    "LB-C009": 500,
    "LB-C010": 413,
    "LB-C011": 500,
}

#: 采集目录里允许通过 ``/api/captures/<id>/<file>`` 读回的文件（白名单，防目录穿越）。
CAPTURE_FILES = ("frame.png", "scene.json", "capture.json", "index.json")

#: 内存里那几帧允许通过 ``/api/frames/<token>/<file>`` 读回的文件（白名单）。
FRAME_FILES = ("overlay.png", "frame.png")

#: 采集目录里各文件的固定文件名。
FILE_FRAME = "frame.png"
FILE_SCENE = "scene.json"
FILE_RECORD = "capture.json"
FILE_INDEX = "index.json"

#: 识别错了的样本单独归档到这个子目录（``recognize report --dir`` 因此可以分开跑）。
REFUSED_DIRNAME = "refused"


__all__ = [
    "CAPTURE_FILES",
    "CAPTURE_VERSION",
    "CORNER_SOURCE_DETECTED",
    "CORNER_SOURCE_MANUAL",
    "CORNER_SOURCE_SINGLE_MARKER",
    "ERROR_CODES",
    "ERROR_STATUS",
    "FILE_FRAME",
    "FILE_INDEX",
    "FILE_RECORD",
    "FILE_SCENE",
    "FRAME_FILES",
    "FRAME_TOKEN_CAPACITY",
    "KIND_CAPTURE",
    "KIND_CAPTURE_INDEX",
    "KIND_CAPTURE_LIST",
    "KIND_CAPTURE_RESULT",
    "KIND_CAPTURE_SCENE",
    "KIND_ERROR",
    "KIND_FRAME_RECOGNITION",
    "KIND_HEALTH",
    "KIND_INFO",
    "MAX_BODY_BYTES",
    "MEDIA_GRAY",
    "MEDIA_JSON",
    "MEDIA_PNG",
    "OUTCOMES",
    "OUTCOME_ACCEPTED",
    "OUTCOME_MISRECOGNISED",
    "REFUSED_DIRNAME",
    "ROLE_VALUES",
    "ROUTE_CAPTURE",
    "ROUTE_CAPTURE_FILE",
    "ROUTE_CAPTURES",
    "ROUTE_FRAME",
    "ROUTE_FRAME_FILE",
    "ROUTE_HEALTH",
    "ROUTE_INDEX",
    "ROUTE_INFO",
    "SERVICE_NAME",
    "STATIC_ROUTE_PREFIX",
]
