"""采集台的 WSGI 应用：路由、请求整形、JSON 错误模型。

为什么是 WSGI：``wsgiref`` 在标准库里，**零新增运行时依赖**；同时 WSGI 的调用形状
（``app(environ, start_response)``）让测试可以完全在进程内驱动（``serve_cam.testing.TestClient``），
不需要起端口、不需要第三方框架。

路由一览（``docs/CAPTURE.md`` 与 ``protocol.py`` 是唯一事实来源，文档缺口由测试核对）：

| 方法与路径 | 作用 |
| --- | --- |
| ``GET /`` | 自包含页面（内联 CSS/JS，无外链、无 CDN） |
| ``GET /api/health`` | 存活检查：服务名、版本、采集目录与样本计数 |
| ``GET /api/info`` | 页面需要的元数据：卡面版式、卡片字典、状态枚举、媒体类型 |
| ``POST /api/frame`` | 识别一帧 → §3 ``CardCandidateList`` + 叠加图 PNG |
| ``POST /api/capture`` | 把当前帧与人工标注落盘到 ``fixtures/live/<时间戳>/`` |
| ``GET /api/captures`` | 列出已采样本（含 ``refused``） |
| ``GET /api/captures/<id>/<file>`` | 读回样本里的图像/JSON（白名单） |
| ``GET /api/frames/<token>/<file>`` | 读回内存里最近几帧的原帧/叠加图 |
"""

from __future__ import annotations

import base64
import json
import secrets
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, unquote

from .. import __version__
from ..vision.cards import CardRegistry
from ..vision.protocol import CARD_STATUSES
from ..vision.recognize import recognize
from .captures import CaptureStore, parse_annotations
from .frames import (
    FrameStore,
    RequestError,
    StoredFrame,
    build_frame,
    decode_frame_body,
)
from .overlay import render_overlay
from .protocol import (
    CAPTURE_FILES,
    CAPTURE_VERSION,
    ERROR_CODES,
    ERROR_STATUS,
    FRAME_FILES,
    FRAME_TOKEN_CAPACITY,
    KIND_CAPTURE_LIST,
    KIND_CAPTURE_RESULT,
    KIND_ERROR,
    KIND_FRAME_RECOGNITION,
    KIND_HEALTH,
    KIND_INFO,
    MAX_BODY_BYTES,
    MEDIA_GRAY,
    MEDIA_JSON,
    MEDIA_PNG,
    OUTCOMES,
    REFUSED_DIRNAME,
    ROLE_VALUES,
    ROUTE_CAPTURE,
    ROUTE_CAPTURE_FILE,
    ROUTE_CAPTURES,
    ROUTE_FRAME,
    ROUTE_FRAME_FILE,
    ROUTE_HEALTH,
    ROUTE_INDEX,
    ROUTE_INFO,
    SERVICE_NAME,
)
from .clock import utc_now

SCHEMA_VERSION = "0.0.1"

_JSON_CONTENT_TYPE = "application/json; charset=utf-8"
_HTML_CONTENT_TYPE = "text/html; charset=utf-8"
_FILE_CONTENT_TYPES = {
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
}


@dataclass(frozen=True)
class ServeCamConfig:
    """启动参数（CLI 与测试都从这里进）。"""

    live_dir: Path
    card_layout: str | None = None
    max_body_bytes: int = MAX_BODY_BYTES
    frame_capacity: int = FRAME_TOKEN_CAPACITY
    clock: Callable[[], datetime] = utc_now
    capture_id_factory: Callable[[datetime], str] | None = None


def json_bytes(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class ServeCamApp:
    """LB-030 采集台的 WSGI 应用（单用户、本地、无鉴权：公网入口由运维侧隧道负责）。"""

    def __init__(
        self,
        config: ServeCamConfig,
        *,
        registry: CardRegistry | None = None,
        page_renderer: Callable[[], bytes] | None = None,
    ) -> None:
        self.config = config
        self.registry = registry or CardRegistry.load(capture_layout=config.card_layout)
        self.captures = CaptureStore(
            Path(config.live_dir), registry=self.registry, id_factory=config.capture_id_factory
        )
        self.frames = FrameStore(capacity=config.frame_capacity)
        self._page_renderer = page_renderer

    # --- WSGI 入口 ------------------------------------------------------------

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = unquote(str(environ.get("PATH_INFO", "") or "/"))
        query = {
            key: values[0]
            for key, values in parse_qs(str(environ.get("QUERY_STRING", "") or ""), keep_blank_values=True).items()
        }
        try:
            status, headers, body = self._dispatch(method, path, query, environ)
        except RequestError as exc:
            status, headers, body = self._error_response(exc)
        except Exception as exc:  # noqa: BLE001 - 顶层兜底：坏请求不能让本地服务整锅翻
            traceback.print_exc()
            status, headers, body = self._error_response(
                RequestError("LB-C011", f"服务内部错误：{type(exc).__name__}: {exc}")
            )
        start_response(status, headers)
        return [body]

    # --- 路由 -----------------------------------------------------------------

    def _dispatch(
        self, method: str, path: str, query: dict[str, str], environ: dict[str, Any]
    ) -> tuple[str, list[tuple[str, str]], bytes]:
        if path in {"/", "/index.html"}:
            if method not in {"GET", "HEAD"}:
                raise RequestError("LB-C005", f"{path} 只支持 GET")
            return self._html_response(self._page())

        if path == ROUTE_HEALTH:
            self._require(method, {"GET"})
            return self._json_response(self._health())

        if path == ROUTE_INFO:
            self._require(method, {"GET"})
            return self._json_response(self._info())

        if path == ROUTE_FRAME:
            self._require(method, {"POST"})
            return self._json_response(self._handle_frame(query, environ))

        if path == ROUTE_CAPTURE:
            self._require(method, {"POST"})
            status, document = self._handle_capture(query, environ)
            return self._json_response(document, status=status)

        if path == ROUTE_CAPTURES or path == ROUTE_CAPTURES + "/":
            self._require(method, {"GET"})
            return self._json_response(self._list_captures(query))

        capture_parts = self._match_capture_file(path)
        if capture_parts is not None:
            self._require(method, {"GET"})
            return self._serve_capture_file(*capture_parts)

        frame_parts = self._match_frame_file(path)
        if frame_parts is not None:
            self._require(method, {"GET"})
            return self._serve_frame_file(*frame_parts)

        raise RequestError("LB-C004", f"没有这个路由：{path}（页面在 {ROUTE_INDEX}，API 见 docs/CAPTURE.md）")

    @staticmethod
    def _require(method: str, allowed: set[str]) -> None:
        if method not in allowed:
            raise RequestError(
                "LB-C005", f"该方法不被允许：{method}（只支持 {'/'.join(sorted(allowed))}）"
            )

    @staticmethod
    def _match_capture_file(path: str) -> tuple[str, str] | None:
        prefix = "/api/captures/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix) :]
        if "/" not in rest:
            return None
        capture_id, _, file_name = rest.partition("/")
        return capture_id, file_name

    @staticmethod
    def _match_frame_file(path: str) -> tuple[str, str] | None:
        prefix = "/api/frames/"
        if not path.startswith(prefix):
            return None
        rest = path[len(prefix) :]
        if "/" not in rest:
            return None
        token, _, file_name = rest.partition("/")
        return token, file_name

    # --- 处理器 ---------------------------------------------------------------

    def _health(self) -> dict[str, Any]:
        summaries = self.captures.list()
        return {
            "kind": KIND_HEALTH,
            "schema_version": SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "service": SERVICE_NAME,
            "package_version": __version__,
            "live_dir": str(self.config.live_dir),
            "refused_dir": str(Path(self.config.live_dir) / REFUSED_DIRNAME),
            "captures": {
                "accepted": sum(1 for item in summaries if item.outcome != "misrecognised"),
                "misrecognised": sum(1 for item in summaries if item.outcome == "misrecognised"),
            },
            "ok": True,
        }

    def _info(self) -> dict[str, Any]:
        registry = self.registry
        return {
            "kind": KIND_INFO,
            "schema_version": SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "service": SERVICE_NAME,
            "card_layout": registry.capture_layout or registry.default_layout,
            "default_card_layout": registry.default_layout,
            "card_layouts": sorted(registry.layouts),
            "card_layout_source": "capture" if registry.capture_layout else "default",
            "roles": list(ROLE_VALUES),
            "statuses": list(CARD_STATUSES),
            "outcomes": list(OUTCOMES),
            "media_types": [MEDIA_PNG, MEDIA_GRAY, MEDIA_JSON],
            "max_body_bytes": self.config.max_body_bytes,
            "frame_token_capacity": self.config.frame_capacity,
            "routes": {
                "index": ROUTE_INDEX,
                "health": ROUTE_HEALTH,
                "info": ROUTE_INFO,
                "frame": ROUTE_FRAME,
                "capture": ROUTE_CAPTURE,
                "captures": ROUTE_CAPTURES,
                "capture_file": ROUTE_CAPTURE_FILE,
                "frame_file": ROUTE_FRAME_FILE,
            },
            "cards": [
                {
                    "card_id": card.card_id,
                    "marker_id": card.marker_id,
                    "card_type": card.card_type,
                    "card_face": card.card_face,
                    "label": card.label,
                }
                for card in registry.card_dictionary.cards
            ],
        }

    def _handle_frame(self, query: dict[str, str], environ: dict[str, Any]) -> dict[str, Any]:
        body = self._read_body(environ)
        incoming = decode_frame_body(
            body,
            environ.get("CONTENT_TYPE"),
            query=query,
            disposition=environ.get("HTTP_CONTENT_DISPOSITION"),
        )
        layout, registry = self._resolve_layout(
            query.get("card_layout") or self._layout_hint(body, environ.get("CONTENT_TYPE"))
        )
        token = secrets.token_hex(8)
        now = self.config.clock()
        frame = build_frame(incoming, token=token, captured_at_ms=now.timestamp() * 1000.0)
        result = recognize(frame, registry=registry)
        overlay_png = render_overlay(frame, result, extra_lines=(f"layout={layout}",))
        stored = StoredFrame(
            token=token,
            frame=frame,
            recognition=result,
            overlay_png=overlay_png,
            card_layout=layout,
            captured_at_ms=now.timestamp() * 1000.0,
        )
        self.frames.put(stored)
        return self._frame_document(stored)

    @staticmethod
    def _layout_hint(body: bytes, content_type: str | None) -> str | None:
        """``/api/frame`` 也允许把 ``card_layout`` 放进 JSON 请求体（页面用查询参数，curl 用 JSON）。"""
        media_type = str(content_type or "").split(";")[0].strip().lower()
        if media_type != MEDIA_JSON or not body:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict):
            raw = payload.get("card_layout")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None

    def _frame_document(self, stored: StoredFrame) -> dict[str, Any]:
        frame = stored.frame
        result = stored.recognition
        return {
            "kind": KIND_FRAME_RECOGNITION,
            "schema_version": SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "frame": frame.to_dict(),
            "card_candidate_list": result.cards.to_dict(),
            "marker_candidate_list": result.markers.to_dict(),
            "card_layout": stored.card_layout,
            "frame_token": stored.token,
            "overlay": {
                "content_type": "image/png",
                "width": frame.width,
                "height": frame.height,
                "png_base64": base64.b64encode(stored.overlay_png).decode("ascii"),
                "url": f"/api/frames/{stored.token}/overlay.png",
                "frame_url": f"/api/frames/{stored.token}/frame.png",
            },
        }

    def _handle_capture(
        self, query: dict[str, str], environ: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        body = self._read_body(environ)
        media_type = str(environ.get("CONTENT_TYPE") or "").split(";")[0].strip().lower()
        if media_type and media_type != MEDIA_JSON:
            raise RequestError(
                "LB-C002",
                f"/api/capture 只收 {MEDIA_JSON}（图像走 frame_token 或请求体里的 base64 字段）",
            )
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError("LB-C001", f"JSON 解析失败：{exc}") from None
        if not isinstance(payload, dict):
            raise RequestError("LB-C001", "JSON 请求体必须是对象")

        outcome = str(payload.get("outcome") or "accepted").strip().lower()
        if outcome == "refused":  # 页面的「识别错了」用 refused 也接受（等价写法）
            outcome = "misrecognised"
        if outcome not in OUTCOMES:
            raise RequestError("LB-C006", f"outcome={outcome!r} 不在 {', '.join(OUTCOMES)} 里")
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise RequestError("LB-C001", "notes 必须是字符串")
        annotations, warnings = parse_annotations(payload)

        token = payload.get("frame_token") or query.get("frame_token")
        if token:
            stored = self.frames.get(str(token))
            if stored is None:
                raise RequestError(
                    "LB-C007",
                    f"帧令牌 {token!r} 不存在或已过期（只保留最近 {self.config.frame_capacity} 帧）："
                    "请重新 POST /api/frame",
                )
            if payload.get("card_layout"):
                layout, registry = self._resolve_layout(str(payload["card_layout"]))
                if layout != stored.card_layout:
                    stored = self._reanalyze(stored, layout, registry, warnings)
                    self.frames.put(stored)
        else:
            incoming = decode_frame_body(
                body,
                MEDIA_JSON,
                query={**query, **(payload if isinstance(payload, dict) else {})},
            )
            layout, registry = self._resolve_layout(
                payload.get("card_layout") or query.get("card_layout")
            )
            frame_token = secrets.token_hex(8)
            now = self.config.clock()
            frame = build_frame(incoming, token=frame_token, captured_at_ms=now.timestamp() * 1000.0)
            result = recognize(frame, registry=registry)
            stored = StoredFrame(
                token=frame_token,
                frame=frame,
                recognition=result,
                overlay_png=render_overlay(frame, result, extra_lines=(f"layout={layout}",)),
                card_layout=layout,
                captured_at_ms=now.timestamp() * 1000.0,
            )
            self.frames.put(stored)

        created_at = self.config.clock()
        capture = self.captures.write(
            stored,
            annotations=annotations,
            outcome=outcome,
            notes=notes,
            created_at=created_at,
            extra_warnings=tuple(warnings),
        )
        document = {
            "kind": KIND_CAPTURE_RESULT,
            "schema_version": SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "capture": capture.to_dict(),
        }
        return "201 Created", document

    def _reanalyze(
        self,
        stored: StoredFrame,
        layout: str,
        registry: CardRegistry,
        warnings: list[str],
    ) -> StoredFrame:
        """换卡面版式重跑识别（页面允许在一帧上换卡纸，识别结论必须跟着变）。"""
        result = recognize(stored.frame, registry=registry)
        warnings.append(f"按请求里的 card_layout={layout!r} 重新识别了这一帧")
        return StoredFrame(
            token=stored.token,
            frame=stored.frame,
            recognition=result,
            overlay_png=render_overlay(stored.frame, result, extra_lines=(f"layout={layout}",)),
            card_layout=layout,
            captured_at_ms=stored.captured_at_ms,
        )

    def _resolve_layout(self, raw: str | None) -> tuple[str, CardRegistry]:
        registry = self.registry
        layout = (raw or "").strip() or registry.capture_layout
        if layout is None:
            return registry.default_layout, registry
        if layout not in registry.layouts:
            raise RequestError(
                "LB-C006",
                f"card_layout={layout!r} 不在版式定义里；可用：{', '.join(sorted(registry.layouts))}",
            )
        if registry.capture_layout == layout:
            return layout, registry
        return layout, registry.with_capture_layout(layout)

    def _list_captures(self, query: dict[str, str]) -> dict[str, Any]:
        wanted = (query.get("outcome") or "").strip().lower()
        if wanted and wanted not in OUTCOMES and wanted != "all":
            raise RequestError("LB-C006", f"outcome={wanted!r} 不在 {', '.join(OUTCOMES)} 里")
        summaries = [
            summary
            for summary in self.captures.list()
            if not wanted or wanted == "all" or summary.outcome == wanted
        ]
        return {
            "kind": KIND_CAPTURE_LIST,
            "schema_version": SCHEMA_VERSION,
            "capture_version": CAPTURE_VERSION,
            "live_dir": str(self.config.live_dir),
            "refused_dir": str(Path(self.config.live_dir) / REFUSED_DIRNAME),
            "count": len(summaries),
            "counts": {
                "accepted": sum(1 for item in summaries if item.outcome != "misrecognised"),
                "misrecognised": sum(1 for item in summaries if item.outcome == "misrecognised"),
            },
            "captures": [summary.to_dict() for summary in summaries],
        }

    def _serve_capture_file(self, capture_id: str, file_name: str) -> tuple[str, list[tuple[str, str]], bytes]:
        if file_name not in CAPTURE_FILES:
            raise RequestError(
                "LB-C008", f"样本里没有 {file_name!r}；可读：{', '.join(CAPTURE_FILES)}"
            )
        directory = self.captures.resolve_directory(capture_id)
        path = directory / file_name
        if not path.is_file():
            raise RequestError("LB-C008", f"样本 {capture_id} 里缺文件 {file_name}")
        return (
            "200 OK",
            self._file_headers(path.name),
            path.read_bytes(),
        )

    def _serve_frame_file(self, token: str, file_name: str) -> tuple[str, list[tuple[str, str]], bytes]:
        if file_name not in FRAME_FILES:
            raise RequestError(
                "LB-C007", f"内存帧里没有 {file_name!r}；可读：{', '.join(FRAME_FILES)}"
            )
        stored = self.frames.get(token)
        if stored is None:
            raise RequestError(
                "LB-C007",
                f"帧令牌 {token!r} 不存在或已过期（只保留最近 {self.config.frame_capacity} 帧）",
            )
        if file_name == "overlay.png":
            payload = stored.overlay_png
        else:
            from ..synth.png import encode_gray

            frame = stored.frame
            payload = encode_gray(frame.width, frame.height, frame.image.pixels)
        return "200 OK", self._file_headers(file_name), payload

    # --- 响应与请求体工具 -----------------------------------------------------

    def _page(self) -> bytes:
        if self._page_renderer is not None:
            return self._page_renderer()
        from .page import render_page

        return render_page()

    @staticmethod
    def _file_headers(file_name: str) -> list[tuple[str, str]]:
        suffix = Path(file_name).suffix.lower()
        return [
            ("Content-Type", _FILE_CONTENT_TYPES.get(suffix, "application/octet-stream")),
            ("Cache-Control", "no-store"),
        ]

    def _read_body(self, environ: dict[str, Any]) -> bytes:
        stream = environ.get("wsgi.input")
        if stream is None:
            return b""
        raw_length = environ.get("CONTENT_LENGTH") or "0"
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise RequestError("LB-C001", f"Content-Length 不是整数：{raw_length!r}") from None
        if length > self.config.max_body_bytes:
            raise RequestError(
                "LB-C010",
                f"请求体 {length} 字节超过上限 {self.config.max_body_bytes}（MAX_BODY_BYTES）",
            )
        return stream.read(length) if length > 0 else b""

    def _json_response(
        self, document: Any, *, status: str = "200 OK"
    ) -> tuple[str, list[tuple[str, str]], bytes]:
        return status, [("Content-Type", _JSON_CONTENT_TYPE), ("Cache-Control", "no-store")], json_bytes(
            document
        )

    def _html_response(self, body: bytes) -> tuple[str, list[tuple[str, str]], bytes]:
        return "200 OK", [("Content-Type", _HTML_CONTENT_TYPE), ("Cache-Control", "no-store")], body

    def _error_response(self, error: RequestError) -> tuple[str, list[tuple[str, str]], bytes]:
        status = ERROR_STATUS[error.code]
        document = {
            "kind": KIND_ERROR,
            "schema_version": SCHEMA_VERSION,
            "error": error.to_dict(),
            "error_codes": dict(ERROR_CODES),
        }
        return (
            f"{status} {_reason(status)}",
            [("Content-Type", _JSON_CONTENT_TYPE), ("Cache-Control", "no-store")],
            json_bytes(document),
        )


def _reason(status: int) -> str:
    import http

    try:
        return http.HTTPStatus(status).phrase
    except ValueError:  # pragma: no cover - 码表只会有合法状态码
        return "Error"


__all__ = ["SCHEMA_VERSION", "ServeCamApp", "ServeCamConfig", "json_bytes"]
