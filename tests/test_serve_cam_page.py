"""页面本身：自包含（验收③）、控件齐、只跟本进程说话、导出的单文件与服务返回一致。

「断网可加载」只能靠两条间接证据证：一是页面里**没有任何外部 URL/资源标签**，
二是页面里所有 ``fetch`` 都是本进程的相对路径（连不上外网也无所谓）。相机权限是浏览器
自己的事，不在这两条里。
"""

from __future__ import annotations

import re

import pytest

from liveblocks import cli
from liveblocks.serve_cam import render_page
from liveblocks.serve_cam.protocol import CAPTURE_VERSION, SERVICE_NAME

REQUIRED_ELEMENT_IDS = (
    "video",
    "canvas",
    "deviceSelect",
    "refreshDevices",
    "start",
    "rear",
    "front",
    "stop",
    "maxSide",
    "grabOnce",
    "continuous",
    "interval",
    "overlay",
    "cardsList",
    "cardsJson",
    "captureForm",
    "cardId",
    "role",
    "corners",
    "fillCorners",
    "wrong",
    "samples",
)


def test_page_is_a_single_self_contained_document() -> None:
    html = render_page().decode("utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<script") == 1  # 只有一段内联脚本
    assert "<link" not in html  # 没有外部样式表
    assert "<style>" in html  # 样式内联
    assert "@import" not in html
    for forbidden in ("http://", "https://", "//cdn", "cdn.", "unpkg", "jsdelivr"):
        assert forbidden not in html
    assert len(html.encode("utf-8")) > 10_000  # 不是占位页


def test_page_declares_its_service_and_version() -> None:
    html = render_page().decode("utf-8")
    assert SERVICE_NAME in html
    assert f"v{CAPTURE_VERSION}" in html
    assert "不做鉴权" in html or "不做公网鉴权" in html


@pytest.mark.parametrize("element", REQUIRED_ELEMENT_IDS)
def test_page_has_the_control(element: str) -> None:
    html = render_page().decode("utf-8")
    assert f'id="{element}"' in html, f"页面缺少控件 {element}"


def test_page_wires_camera_constraints_for_phone_cameras() -> None:
    html = render_page().decode("utf-8")
    assert "getUserMedia" in html
    assert "facingMode" in html
    assert "'environment'" in html and "'user'" in html
    assert "enumerateDevices" in html
    assert "getSettings" in html


def test_page_converts_to_gray_and_uses_the_gray_media_type() -> None:
    html = render_page().decode("utf-8")
    assert "application/x-liveblocks-gray" in html
    assert "getImageData" in html
    assert "299" in html and "587" in html and "114" in html  # 灰度权重不是随手写的常数
    assert "willReadFrequently" in html


def test_page_supports_single_shot_and_continuous_capture() -> None:
    html = render_page().decode("utf-8")
    assert "setInterval" in html and "clearInterval" in html
    assert "单帧抓拍" in html
    assert "连续取帧" in html


def test_page_has_the_manual_annotation_and_refuse_paths() -> None:
    html = render_page().decode("utf-8")
    assert "这帧识别错了" in html
    assert "refused" in html
    assert "/api/capture" in html
    assert "misrecognised" in html


def test_page_fetches_only_its_own_origin(serve_cam) -> None:
    html = serve_cam.client.get("/").text
    targets = re.findall(r"fetch\((.*?)\)", html, flags=re.S)
    assert len(targets) >= 4
    for raw in targets:
        target = raw.strip()
        assert target[0] in "'\"`", f"fetch 目标不是字面量：{target}"
        assert target[1:].startswith("/"), f"fetch 目标不是相对路径：{target}"
    routes = serve_cam.client.get("/api/info").json()["routes"]
    known = {value.split("<")[0] for value in routes.values()} | {"/api/frame", "/api/capture", "/api/captures"}
    for raw in targets:
        literal = raw.strip()[1:]
        path = literal.split("'")[0].split('"')[0].split("?")[0]
        assert any(path.startswith(prefix) for prefix in known), f"页面调了未知路由：{path}"


def test_page_export_matches_the_served_page(tmp_path) -> None:
    out = tmp_path / "page.html"
    assert cli.main(["serve-cam", "page", "--out", str(out)]) == 0
    assert out.read_bytes() == render_page()


def test_every_dom_id_used_by_the_script_exists_in_the_markup() -> None:
    """JS 里 ``$('x')`` 的每个 id 都必须在 HTML 里存在——否则页面上就是 ``null`` 崩溃。"""
    import re

    html = render_page().decode("utf-8")
    markup, script = html.split("<script>", 1)
    used = set(re.findall(r"\$\('([^']+)'\)", script))
    declared = set(re.findall(r'id="([^"]+)"', markup))
    missing = sorted(used - declared)
    assert not missing, f"脚本引用了不存在的 DOM id：{missing}"
    assert used, "脚本里没有 $() 调用？页面不可能不碰 DOM"
