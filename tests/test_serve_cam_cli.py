"""``liveblocks serve-cam`` 子命令：一条命令起服务、页面导出、以及真起端口的探活。

「一条命令起本地服务」不能只验证「构造函数形状对不对」：这里在**随机端口**上真起一次
``wsgiref`` 服务器（``--port 0`` 让内核分配），再用 ``urllib`` 打两个请求，确认打印出来的地址
真的能开页面、能拿 ``/api/health``。
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.cli import DEFAULT_LIVE_DIR, DEFAULT_SERVE_CAM_PORT, build_parser
from liveblocks.serve_cam import ServeCamConfig
from liveblocks.serve_cam.server import make_local_server

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_parser_has_the_serve_cam_group() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "serve-cam" in help_text
    subparsers_action = next(
        action for action in parser._actions if getattr(action, "choices", None) and "serve-cam" in action.choices
    )
    assert "serve" in subparsers_action.choices["serve-cam"].format_help()


def test_defaults_are_local_only() -> None:
    assert DEFAULT_SERVE_CAM_PORT == 8765
    assert str(DEFAULT_LIVE_DIR) == "fixtures/live"


def test_print_url_only_does_not_bind_a_port(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["serve-cam", "serve", "--print-url-only"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "http://127.0.0.1:8765/" in captured.out
    assert "fixtures/live" in captured.out
    assert "refused" in captured.out


def test_page_subcommand_writes_the_single_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "nested" / "page.html"
    assert cli.main(["serve-cam", "page", "--out", str(out)]) == 0
    assert out.is_file()
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "getUserMedia" in html
    assert "页面已写入" in capsys.readouterr().out


def test_page_subcommand_prints_to_stdout_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["serve-cam", "page"]) == 0
    assert capsys.readouterr().out.startswith("<!DOCTYPE html>")


def test_server_really_binds_and_serves(tmp_path: Path) -> None:
    """真起一个服务器（端口 0），用 urllib 打 ``/api/health`` 与 ``/``。"""
    from liveblocks.serve_cam import ServeCamApp

    config = ServeCamConfig(live_dir=tmp_path / "live")
    bound = make_local_server(ServeCamApp(config), "127.0.0.1", 0)
    thread = threading.Thread(target=bound.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        assert bound.port > 0
        with urllib.request.urlopen(f"{bound.url}api/health", timeout=10) as response:
            assert response.status == 200
            health = json.loads(response.read().decode("utf-8"))
            assert health["kind"] == "ServeCamHealth"
            assert health["service"] == "lb-030-serve-cam"
        with urllib.request.urlopen(bound.url, timeout=10) as response:
            assert response.status == 200
            body = response.read().decode("utf-8")
            assert body.startswith("<!DOCTYPE html>")
            assert "getUserMedia" in body
        # 未知路由也要给结构化 JSON 错误（不是 HTML 500 页）
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(f"{bound.url}api/nope", timeout=10)
        assert failure.value.code == 404
        document = json.loads(failure.value.read().decode("utf-8"))
        assert document["error"]["code"] == "LB-C004"
    finally:
        bound.server.shutdown()
        bound.server.server_close()
        thread.join(timeout=10)
