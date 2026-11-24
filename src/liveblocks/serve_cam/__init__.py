"""LB-030 Web 摄像头采集与人工辅助测试台。

一条命令起本地服务（``liveblocks serve-cam serve``），页面用浏览器 ``getUserMedia`` 选任意
摄像头（手机前置/后置、笔记本内置、USB UVC 走同一条路径），支持单帧抓拍与连续取帧：

- ``POST /api/frame``  → §3 ``CardCandidateList`` + 识别叠加图（与原帧同尺寸的 PNG）；
- ``POST /api/capture`` → 当前帧 + 人工标注（卡片 id、角色、可选角点）落成
  ``fixtures/live/<时间戳>/``，**能被 ``recognize report`` 直接消费**；
- ``GET /api/captures`` → 列出已采样本；页面能标「这帧识别错了」，坏样本单独进
  ``fixtures/live/refused/``。

边界（与 Issue #26 一致）：不做视频录制与上传、不做多用户与账号、不做公网鉴权、不改
``docs/PROTOCOL.md``。识别层本身仍是 LB-003（本模块一行识别算法都没写，只调用）。

零新增依赖：HTTP 用标准库 ``wsgiref``，页面是一个内联 CSS/JS 的 HTML 字符串，
叠加图用 LB-008 的确定性光栅 + 仓库自带的 PNG 编码器。
"""

from __future__ import annotations

from .app import SCHEMA_VERSION, ServeCamApp, ServeCamConfig
from .captures import CaptureStore, CaptureSummary, parse_annotations
from .frames import FrameStore, RequestError, StoredFrame, decode_frame_body
from .overlay import render_overlay
from .page import render_page
from .protocol import CAPTURE_VERSION, ERROR_CODES, OUTCOMES, SERVICE_NAME
from .server import BoundServer, make_local_server, serve
from .testing import TestClient, TestResponse

__all__ = [
    "CAPTURE_VERSION",
    "BoundServer",
    "CaptureStore",
    "CaptureSummary",
    "ERROR_CODES",
    "FrameStore",
    "OUTCOMES",
    "RequestError",
    "SCHEMA_VERSION",
    "SERVICE_NAME",
    "ServeCamApp",
    "ServeCamConfig",
    "StoredFrame",
    "TestClient",
    "TestResponse",
    "decode_frame_body",
    "make_local_server",
    "parse_annotations",
    "render_overlay",
    "render_page",
    "serve",
]
