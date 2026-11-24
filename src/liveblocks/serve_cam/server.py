"""本地服务：``wsgiref`` + 线程化服务器（**一条命令起服务**，零新增依赖）。

单用户本地工具，但仍用多线程：页面在连续取帧时会同时拉 ``/api/captures``，串行服务会让
列表等识别跑完才返回。线程安全问题只集中在 ``FrameStore`` 的锁上（见 ``frames.py``），
采集落盘是「一个样本一个新目录」，不会互相覆盖。

绑定 ``--port 0`` 时端口由内核分配，``serve`` 会把真实端口打印出来（``tests/test_serve_cam_cli.py``
用它验证「一条命令能起来」而不是只验证构造函数的形状）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .app import ServeCamApp, ServeCamConfig
from .protocol import CAPTURE_VERSION, REFUSED_DIRNAME, SERVICE_NAME


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    """每个请求一个线程的小型 WSGI 服务器（daemon 线程，Ctrl-C 能立刻退出）。"""

    daemon_threads = True
    allow_reuse_address = True


class QuietRequestHandler(WSGIRequestHandler):
    """默认不刷屏：只有 4xx/5xx 才打一行到 stderr（连续取帧时一帧一行会淹没终端）。"""

    verbose_all = False

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        try:
            status = int(str(code).split()[0])
        except (TypeError, ValueError):
            status = 200
        if self.verbose_all or status >= 400:
            super().log_request(code, size)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 与基类签名一致
        if self.verbose_all:
            super().log_message(format, *args)


@dataclass(frozen=True)
class BoundServer:
    """已绑定端口的服务器（调用方负责 ``serve_forever`` / ``shutdown``）。"""

    server: WSGIServer
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"


def build_app(config: ServeCamConfig) -> ServeCamApp:
    return ServeCamApp(config)


def make_local_server(
    app: ServeCamApp, host: str, port: int, *, verbose: bool = False
) -> BoundServer:
    """建服务器并绑定端口（不进入 serve_forever）。"""
    handler = type("Handler", (QuietRequestHandler,), {"verbose_all": verbose})
    server = make_server(host, port, app, server_class=ThreadingWSGIServer, handler_class=handler)
    return BoundServer(server=server, host=host, port=int(server.server_address[1]))


def banner(url: str, config: ServeCamConfig) -> str:
    return "\n".join(
        (
            f"LB-030 采集台已启动：{url}",
            f"  服务：{SERVICE_NAME}（capture_version {CAPTURE_VERSION}）",
            f"  采集目录：{config.live_dir}（识别错了的样本单独进 {refused_dir(config.live_dir)}）",
            "  页面：用浏览器打开上面的地址 → 选摄像头 → 单帧抓拍 / 连续取帧",
            "  手机上打开需要 HTTPS 或 localhost；公网入口与 token 由运维侧隧道负责，本服务不做鉴权",
            "  Ctrl-C 停止",
        )
    )


def refused_dir(live_dir: object) -> str:
    return f"{live_dir}/{REFUSED_DIRNAME}"


def serve(config: ServeCamConfig, *, host: str = "127.0.0.1", port: int = 8765, verbose: bool = False) -> int:
    """阻塞式起服务（返回进程退出码；Ctrl-C 视为正常退出）。"""
    app = build_app(config)
    try:
        bound = make_local_server(app, host, port, verbose=verbose)
    except OSError as exc:
        print(
            f"绑定 {host}:{port} 失败（{exc}）：端口可能被占用，用 --port 换一个，或用 --port 0 让内核分配",
            file=sys.stderr,
        )
        return 2
    print(banner(bound.url, config), flush=True)
    try:
        bound.server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C：已停止。", file=sys.stderr)
    finally:
        bound.server.server_close()
    return 0


__all__ = [
    "BoundServer",
    "QuietRequestHandler",
    "ThreadingWSGIServer",
    "banner",
    "build_app",
    "make_local_server",
    "refused_dir",
    "serve",
]
