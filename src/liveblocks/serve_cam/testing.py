"""进程内 HTTP 客户端（``TestClient``）：不起端口，直接把请求喂给 WSGI 应用。

为什么不用第三方 ``TestClient``：本单要求**零新增运行时/测试依赖**，而 WSGI 的调用形状本身
就足够造一个客户端——把请求体塞进 ``wsgi.input``、收 ``start_response`` 的 status/headers、
拿 ``app`` 返回的字节串当响应体。``pytest`` 里这样跑验收①，跟真实 HTTP 走的是**同一段代码**
（``ServeCamApp.__call__`` 之前没有任何中间层）。

用法（``tests/test_serve_cam.py`` 就是这么写的）::

    client = TestClient(ServeCamApp(ServeCamConfig(live_dir=tmp_path)))
    response = client.post_png("/api/frame", png_bytes, filename="normal.png")
    assert response.status == 200
    assert response.json()["card_candidate_list"]["kind"] == "CardCandidateList"
    width, height, _pixels = response.json()["overlay"]["width"], ...
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib.parse import urlencode, urlsplit


@dataclass(frozen=True)
class TestResponse:
    """一次进程内请求的结果（``status`` 是整数，``headers`` 的键小写）。"""

    __test__ = False

    status: int
    reason: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")


class TestClient:
    """把 WSGI 应用当函数调用：``client.get('/api/health')``、``client.post_png(...)``。"""

    #: 别让 pytest 把这个类当用例收集（类名以 ``Test`` 开头，但它是个客户端）。
    __test__ = False

    def __init__(self, app: Callable[..., Any], *, host: str = "127.0.0.1") -> None:
        self.app = app
        self.host = host

    # --- 便捷方法 -------------------------------------------------------------

    def get(self, path: str, **kwargs: Any) -> TestResponse:
        return self.request("GET", path, **kwargs)

    def post(
        self,
        path: str,
        body: bytes | str = b"",
        *,
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> TestResponse:
        payload = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        return self.request("POST", path, body=payload, content_type=content_type, headers=headers)

    def post_json(self, path: str, document: Any) -> TestResponse:
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return self.post(path, payload, content_type="application/json")

    def post_png(self, path: str, png: bytes, *, filename: str | None = None) -> TestResponse:
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'} if filename else None
        return self.post(path, png, content_type="image/png", headers=headers)

    def post_gray(
        self, path: str, width: int, height: int, pixels: bytes, *, query: Mapping[str, str] | None = None
    ) -> TestResponse:
        return self.post(
            _with_query(path, {"width": width, "height": height, **dict(query or {})}),
            pixels,
            content_type="application/x-liveblocks-gray",
        )

    # --- 核心：把请求变成一次 WSGI 调用 ---------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        content_type: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> TestResponse:
        split = urlsplit(path)
        query = split.query
        environ: dict[str, Any] = {
            "REQUEST_METHOD": method.upper(),
            "SCRIPT_NAME": "",
            "PATH_INFO": split.path or "/",
            "QUERY_STRING": query,
            "SERVER_NAME": self.host,
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "REMOTE_ADDR": self.host,
            "HTTP_HOST": self.host,
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "CONTENT_LENGTH": str(len(body)),
        }
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        for key, value in (headers or {}).items():
            header_key = key.upper().replace("-", "_")
            if header_key not in {"CONTENT_TYPE", "CONTENT_LENGTH"}:
                environ["HTTP_" + header_key] = value

        captured: dict[str, Any] = {}

        def start_response(
            status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
        ) -> Callable[[bytes], None]:
            if exc_info is not None:
                raise exc_info[1].with_traceback(exc_info[2])
            captured["status"] = status
            captured["headers"] = response_headers
            return lambda data: None

        chunks = self.app(environ, start_response)
        try:
            payload = b"".join(chunks)
        finally:
            close = getattr(chunks, "close", None)
            if callable(close):
                close()

        status_line = captured.get("status", "500 Internal Server Error")
        code_text, _, reason = status_line.partition(" ")
        header_map = {name.lower(): value for name, value in captured.get("headers", [])}
        return TestResponse(status=int(code_text), reason=reason, headers=header_map, body=payload)


def _with_query(path: str, params: Mapping[str, Any]) -> str:
    split = urlsplit(path)
    merged = split.query
    extra = urlencode({key: value for key, value in params.items() if value is not None})
    query = "&".join(part for part in (merged, extra) if part)
    return f"{split.path}?{query}" if query else split.path


__all__ = ["TestClient", "TestResponse"]
