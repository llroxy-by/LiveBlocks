"""把 HTTP 请求体变成 ``Frame``，并把最近几帧留在内存里（令牌 → 同一帧）。

三种请求体写法（页面与测试/命令行各用一种，语义完全一样）：

1. ``image/png``：8bit 灰度 PNG（就是 ``fixtures/scenes/*.png`` 那种），走仓库自带的
   ``synth.png.decode_gray``——验收①用它逐字段对照 ``recognize detect``；
2. ``application/x-liveblocks-gray``：裸灰度字节 + ``width``/``height``（页面的
   ``getUserMedia`` → ``canvas`` → 手写灰度转换走这条：浏览器不需要任何 PNG 编码库）；
3. ``application/json``：``{"width", "height", "gray_base64"}`` 或 ``{"png_base64"}``（便于 curl）。

``frame_id`` 的解析顺序与 ``recognize detect`` 一致：显式 ``frame_id``（查询参数 / JSON 字段）→
``Content-Disposition`` 里的 ``filename``（推导规则同 ``vision.frame.frame_id_for``，所以上传
``normal.png`` 得到 ``f-normal``，与命令行逐字段可比）→ 采集台自己生成的时间戳 id。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from ..synth.png import decode_gray
from ..vision.frame import Frame, frame_id_for
from ..vision.image import GrayImage
from ..vision.recognize import RecognitionResult
from .protocol import (
    FRAME_TOKEN_CAPACITY,
    MEDIA_GRAY,
    MEDIA_JSON,
    MEDIA_PNG,
    ERROR_CODES,
)

_FILENAME_IN_DISPOSITION = re.compile(r'filename\*?=(?:"([^"]*)"|([^;]+))', re.IGNORECASE)
_CONTENT_TYPE_SPLIT = re.compile(r"\s*;\s*")


class RequestError(Exception):
    """请求层的可预期错误（带 ``LB-C0xx`` 码与 HTTP 状态，由 ``app`` 统一转成 JSON）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        if code not in ERROR_CODES:
            raise ValueError(f"未知错误码：{code}")
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "title": ERROR_CODES[self.code], "message": self.message}


@dataclass(frozen=True)
class IncomingFrame:
    """请求体解出来的一帧灰度图（还没变成 ``vision.Frame``）。"""

    width: int
    height: int
    pixels: bytes
    frame_id: str | None = None
    image_ref: str | None = None


def parse_content_type(raw: str | None) -> tuple[str, dict[str, str]]:
    """拆 ``Content-Type``：返回（小写媒体类型, 参数表）。"""
    if not raw:
        return "", {}
    parts = [part for part in _CONTENT_TYPE_SPLIT.split(raw.strip()) if part]
    media_type = parts[0].lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        params[key.strip().lower()] = value.strip().strip('"')
    return media_type, params


def filename_from_disposition(raw: str | None) -> str | None:
    """从 ``Content-Disposition`` 里取文件名（只取 basename，防路径穿越）。"""
    if not raw:
        return None
    match = _FILENAME_IN_DISPOSITION.search(raw)
    if match is None:
        return None
    name = (match.group(1) or match.group(2) or "").strip()
    if not name:
        return None
    return Path(name).name


def _positive_int(value: object, name: str) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RequestError("LB-C001", f"{name} 必须是正整数：{value!r}") from None
    if number <= 0:
        raise RequestError("LB-C001", f"{name} 必须是正整数：{value!r}")
    return number


def _b64(raw: object, name: str) -> bytes:
    if not isinstance(raw, str) or not raw:
        raise RequestError("LB-C001", f"{name} 必须是非空 base64 字符串")
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise RequestError("LB-C001", f"{name} 不是合法 base64") from None


def _decode_png(data: bytes) -> tuple[int, int, bytes]:
    try:
        return decode_gray(data)
    except (ValueError, OSError) as exc:
        raise RequestError(
            "LB-C003",
            f"PNG 解不出来（只支持 8bit 灰度 PNG，见 docs/CAPTURE.md 的三种请求体）：{exc}",
        ) from None


def _pixels_or_error(width: int, height: int, pixels: bytes) -> tuple[int, int, bytes]:
    expected = width * height
    if len(pixels) != expected:
        raise RequestError(
            "LB-C003",
            f"灰度字节数与 {width}x{height} 不符：期望 {expected}，实际 {len(pixels)}",
        )
    return width, height, pixels


def decode_frame_body(
    body: bytes,
    content_type: str | None,
    *,
    query: dict[str, str] | None = None,
    disposition: str | None = None,
) -> IncomingFrame:
    """把请求体解析成 ``IncomingFrame``（三种媒体类型，见模块头）。"""
    query = dict(query or {})
    media_type, _params = parse_content_type(content_type)
    frame_id = (query.get("frame_id") or "").strip() or None
    filename = filename_from_disposition(disposition)

    if not body:
        raise RequestError("LB-C001", "请求体是空的：请把一帧 PNG 或灰度字节放在请求体里")

    if media_type == MEDIA_PNG:
        width, height, pixels = _decode_png(body)
        return IncomingFrame(width, height, pixels, frame_id=frame_id, image_ref=filename)

    if media_type == MEDIA_GRAY:
        width = _positive_int(query.get("width"), "width")
        height = _positive_int(query.get("height"), "height")
        width, height, pixels = _pixels_or_error(width, height, body)
        return IncomingFrame(width, height, pixels, frame_id=frame_id, image_ref=filename)

    if media_type == MEDIA_JSON:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("LB-C001", f"JSON 解析失败：{exc}") from None
        if not isinstance(payload, dict):
            raise RequestError("LB-C001", "JSON 请求体必须是对象")
        explicit = payload.get("frame_id")
        if isinstance(explicit, str) and explicit.strip():
            frame_id = explicit.strip()
        png_b64 = payload.get("png_base64")
        gray_b64 = payload.get("gray_base64")
        if isinstance(png_b64, str) and png_b64:
            width, height, pixels = _decode_png(_b64(png_b64, "png_base64"))
            return IncomingFrame(width, height, pixels, frame_id=frame_id, image_ref=filename)
        if isinstance(gray_b64, str) and gray_b64:
            width = _positive_int(payload.get("width"), "width")
            height = _positive_int(payload.get("height"), "height")
            width, height, pixels = _pixels_or_error(
                width, height, _b64(gray_b64, "gray_base64")
            )
            return IncomingFrame(width, height, pixels, frame_id=frame_id, image_ref=filename)
        raise RequestError("LB-C001", "JSON 请求体需要 png_base64 或 gray_base64")

    raise RequestError(
        "LB-C002",
        f"不支持的 Content-Type={media_type or '(缺失)'}；可用：{MEDIA_PNG}、{MEDIA_GRAY}、{MEDIA_JSON}",
    )


def build_frame(incoming: IncomingFrame, *, token: str, captured_at_ms: float) -> Frame:
    """把请求体解出的灰度图包成 ``vision.Frame``（``source="camera"``）。"""
    frame_id = incoming.frame_id
    if not frame_id and incoming.image_ref:
        frame_id = frame_id_for(Path(incoming.image_ref))
    if not frame_id:
        frame_id = f"f-cam-{token}"
    return Frame(
        frame_id=frame_id,
        image=GrayImage(incoming.width, incoming.height, incoming.pixels),
        image_ref=incoming.image_ref or f"camera-{token}.png",
        source="camera",
        captured_at_ms=captured_at_ms,
    )


@dataclass(frozen=True)
class StoredFrame:
    """``/api/frame`` 处理过的一帧：``/api/capture`` 凭令牌取回同一帧，不必二次上传。"""

    token: str
    frame: Frame
    recognition: RecognitionResult
    overlay_png: bytes
    card_layout: str | None
    captured_at_ms: float


class FrameStore:
    """有界、线程安全的「令牌 → 最近几帧」缓存（连续取帧时只留住最近 ``capacity`` 帧）。"""

    def __init__(self, capacity: int = FRAME_TOKEN_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError(f"容量必须为正：{capacity}")
        self.capacity = capacity
        self._frames: OrderedDict[str, StoredFrame] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, stored: StoredFrame) -> None:
        with self._lock:
            self._frames[stored.token] = stored
            self._frames.move_to_end(stored.token)
            while len(self._frames) > self.capacity:
                self._frames.popitem(last=False)

    def get(self, token: str) -> StoredFrame | None:
        with self._lock:
            stored = self._frames.get(token)
            if stored is not None:
                self._frames.move_to_end(token)
            return stored

    def __len__(self) -> int:  # pragma: no cover - 仅用于调试/测试断言
        with self._lock:
            return len(self._frames)


__all__ = [
    "FrameStore",
    "IncomingFrame",
    "RequestError",
    "StoredFrame",
    "build_frame",
    "decode_frame_body",
    "filename_from_disposition",
    "parse_content_type",
]
