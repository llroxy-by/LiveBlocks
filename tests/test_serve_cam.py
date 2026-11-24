"""LB-030 验收 ①②③（离线）：① §3 JSON 与 `recognize detect` 逐字段一致 + 叠加图尺寸；
② 采集目录能被 `recognize report` 直接消费；③ 页面无外网依赖、断网可加载。

这个文件就是 Issue #26 里写的那条命令 ``pytest tests/test_serve_cam.py``。验收④（手机后置摄像头 +
实体卡）只能人做，**不在这个文件里**，见 ``docs/CAPTURE.md`` §6。

关于「TestClient」：本单要求零新增依赖，所以用 ``liveblocks.serve_cam.testing.TestClient``——
它把 HTTP 请求直接喂给 WSGI 应用（``environ`` + ``start_response``），与真实 HTTP 走
``ServeCamApp.__call__`` 同一段代码，只是不起端口、不用第三方框架。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.synth.png import decode_gray
from liveblocks.vision import evaluate_directory

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"

#: 入库的 6 个场景（每个都有 PNG + 真值）。
SCENE_NAMES = tuple(sorted(path.stem for path in SCENES_DIR.glob("*.png")))


# --- 验收①：/api/frame 的 §3 JSON 与 recognize detect 命令行逐字段一致 ------------------


@pytest.mark.parametrize("scene", SCENE_NAMES)
def test_frame_json_matches_recognize_detect_cli(scene: str, serve_cam, tmp_path: Path) -> None:
    """上传 ``fixtures/scenes/<scene>.png``，返回的 §3 文档必须与命令行输出**逐字段相等**。"""
    png = (SCENES_DIR / f"{scene}.png").read_bytes()

    # 服务端：按文件名推导 frame_id（normal.png → f-normal），与命令行同一规则
    response = serve_cam.client.post_png("/api/frame", png, filename=f"{scene}.png")
    assert response.status == 200, response.text
    payload = response.json()
    assert payload["kind"] == "FrameRecognitionResult"
    assert payload["card_candidate_list"]["kind"] == "CardCandidateList"

    # 命令行：真正跑一次 liveblocks recognize detect，写到临时文件再读回来
    cli_out = tmp_path / f"{scene}.cards.json"
    assert cli.main(["recognize", "detect", "--image", str(SCENES_DIR / f"{scene}.png"), "--out", str(cli_out)]) == 0
    from_cli = json.loads(cli_out.read_text(encoding="utf-8"))

    assert payload["card_candidate_list"] == from_cli
    # frame_id 也要一致（否则「逐字段」是空话）
    assert payload["frame"]["frame_id"] == from_cli["frame_id"]
    assert payload["card_candidate_list"]["frame_id"] == from_cli["frame_id"]


@pytest.mark.parametrize("scene", SCENE_NAMES)
def test_overlay_png_decodes_and_matches_input_size(scene: str, serve_cam) -> None:
    """叠加图 PNG 可解码，尺寸与输入一致，而且真的画了东西（不是原帧照抄）。"""
    import base64

    png = (SCENES_DIR / f"{scene}.png").read_bytes()
    in_width, in_height, in_pixels = decode_gray(png)
    payload = serve_cam.client.post_png("/api/frame", png, filename=f"{scene}.png").json()

    assert payload["overlay"]["content_type"] == "image/png"
    assert payload["overlay"]["width"] == in_width
    assert payload["overlay"]["height"] == in_height

    overlay = base64.b64decode(payload["overlay"]["png_base64"])
    width, height, pixels = decode_gray(overlay)
    assert (width, height) == (in_width, in_height)
    assert len(pixels) == in_width * in_height

    # 叠加的可见证据：与输入不同的像素必须存在，且图上出现了最亮的墨迹（叠加线/标签）
    changed = sum(1 for before, after in zip(in_pixels, pixels) if before != after)
    assert changed > 100, f"{scene}: 叠加图几乎没有改动原帧（changed={changed}）"
    assert max(pixels) >= 245, f"{scene}: 叠加图里没有出现叠加墨迹"


def test_overlay_url_serves_the_same_png(serve_cam) -> None:
    """``overlay.url`` 拿到的字节必须与 JSON 里的 base64 是同一张图。"""
    import base64

    png = (SCENES_DIR / "normal.png").read_bytes()
    payload = serve_cam.client.post_png("/api/frame", png, filename="normal.png").json()
    response = serve_cam.client.get(payload["overlay"]["url"])
    assert response.status == 200
    assert response.content_type == "image/png"
    assert response.body == base64.b64decode(payload["overlay"]["png_base64"])


def test_frame_endpoint_accepts_raw_gray_from_the_page(serve_cam) -> None:
    """页面走的是「裸灰度字节 + width/height」：结果必须与上传同一张 PNG 完全一致。"""
    png = (SCENES_DIR / "normal.png").read_bytes()
    width, height, pixels = decode_gray(png)
    from_png = serve_cam.client.post_png("/api/frame", png, filename="normal.png").json()
    from_gray = serve_cam.client.post_gray("/api/frame", width, height, pixels, query={"frame_id": "f-normal"})
    assert from_gray.status == 200, from_gray.text
    assert from_gray.json()["card_candidate_list"] == from_png["card_candidate_list"]


# --- 验收②：采集目录能被 recognize report 直接消费 ------------------------------------


def _capture(
    serve_cam,
    scene: str = "normal",
    *,
    card_id: str = "C01",
    outcome: str = "accepted",
    notes: str | None = None,
    **annotation: object,
):
    png = (SCENES_DIR / f"{scene}.png").read_bytes()
    frame = serve_cam.client.post_png("/api/frame", png, filename=f"{scene}.png").json()
    body = {
        "frame_token": frame["frame_token"],
        "outcome": outcome,
        "notes": notes,
        "annotations": [{"card_id": card_id, "role": "action", **annotation}],
    }
    response = serve_cam.client.post_json("/api/capture", body)
    assert response.status == 201, response.text
    return response.json()["capture"]


def test_capture_directory_is_consumed_by_evaluate_directory(serve_cam) -> None:
    capture = _capture(serve_cam, "normal", card_id="C01")
    directory = Path(capture["directory"])

    # 四个文件都在（scene.json 就是 recognize report 读的那份真值）
    for name in ("frame.png", "scene.json", "capture.json", "index.json"):
        assert (directory / name).is_file(), f"采集目录缺少 {name}"

    truth = json.loads((directory / "scene.json").read_text(encoding="utf-8"))
    assert truth["kind"] == "LiveCaptureScene"
    assert truth["image"]["file"] == "frame.png"
    assert [card["marker_id"] for card in truth["cards"]] == [0]

    report = evaluate_directory(directory)
    assert len(report.scenes) == 1
    scene = report.scenes[0]
    assert scene.expected == 1
    assert scene.identified == 1
    assert scene.missed == ()
    assert scene.extra == ()


def test_recognize_report_cli_consumes_a_capture_directory(serve_cam) -> None:
    """真的起一个子进程跑 ``liveblocks recognize report --dir <采集目录>``（退出码 0）。"""
    capture = _capture(serve_cam, "normal", card_id="C01")
    directory = Path(capture["directory"])
    result = subprocess.run(
        [sys.executable, "-m", "liveblocks", "recognize", "report", "--dir", str(directory)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "1 个场景" in result.stdout
    assert "100.0%" in result.stdout


def test_misrecognised_capture_goes_to_refused_and_is_still_consumable(serve_cam, tmp_path: Path) -> None:
    """「这帧识别错了」的样本单独归档到 refused/，且同样能被 recognize report 消费。"""
    good = _capture(serve_cam, "normal", card_id="C01")
    bad = _capture(serve_cam, "normal", card_id="C02", outcome="misrecognised")

    assert good["outcome"] == "accepted"
    assert bad["outcome"] == "misrecognised"
    assert "refused" in Path(bad["directory"]).parts
    assert Path(bad["directory"]).parent == serve_cam.live_dir / "refused"
    assert Path(bad["directory"]).is_dir()

    # 坏样本的真值说这张卡是 C02（标记 1），识别读出来的是 C01（标记 0）：报告应该如实扣分。
    # 注意「误检」列在 LB-003 的统计里结构性恒为 0（``_evaluate_result`` 的 ``extra`` 只从真值
    # 卡片里收集，见 ``docs/CAPTURE.md`` §8 已知限制），所以这里只断言漏检与识别数，不拿误检凑证据。
    report = evaluate_directory(Path(bad["directory"]))
    scene = report.scenes[0]
    assert scene.expected == 1
    assert scene.identified == 0
    assert scene.missed == ("1",)
    assert scene.status_by_card == {"C02": "missing"}
    assert scene.recall == 0.0


def test_capture_index_and_record_describe_the_sample(serve_cam) -> None:
    capture = _capture(serve_cam, "normal", card_id="C01", notes="室内自然光")
    directory = Path(capture["directory"])
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    record = json.loads((directory / "capture.json").read_text(encoding="utf-8"))

    assert index["kind"] == "LiveCaptureIndex"
    assert index["scene"]["ground_truth"] == "scene.json"
    assert index["scene"]["cards"] == 1
    assert index["recognize_report_command"].endswith(str(directory))
    assert record["kind"] == "LiveCaptureRecord"
    assert record["notes"] == "室内自然光"
    assert record["card_candidate_list"]["kind"] == "CardCandidateList"
    assert record["annotations"][0]["match"] is True
    assert record["image"]["pixel_sha256"] == index["scene"]["pixel_sha256"]


# --- 验收③：页面无外网 CDN 依赖 --------------------------------------------------------


def test_page_is_self_contained_and_offline_loadable(serve_cam) -> None:
    response = serve_cam.client.get("/")
    assert response.status == 200
    assert response.content_type.startswith("text/html")
    html = response.text

    # 没有任何外链：不许出现绝对 URL、协议相对 URL、CDN 主机名
    for forbidden in ("http://", "https://", "//cdn", "cdn.", "unpkg", "jsdelivr", "googleapis", "@import url("):
        assert forbidden not in html, f"页面里出现了外链片段：{forbidden}"
    # 没有外部资源标签
    assert "<link" not in html  # 样式内联在 <style> 里
    assert "integrity=" not in html
    assert "crossorigin" not in html

    # 页面确实是个能用的采集台（不是占位页）
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    for element in ('id="video"', 'id="canvas"', 'id="deviceSelect"', 'id="grabOnce"', 'id="continuous"', 'id="wrong"'):
        assert element in html, f"页面缺少元素 {element}"
    assert "getUserMedia" in html
    assert "facingMode" in html
    assert "/api/frame" in html and "/api/capture" in html and "/api/captures" in html


def test_page_only_talks_to_its_own_origin(serve_cam) -> None:
    """页面里所有 ``fetch`` 都必须是相对路径（断网可用，也不把帧发到别处）。"""
    import re

    html = serve_cam.client.get("/").text
    targets = re.findall(r"fetch\(([^)]*)\)", html)
    assert targets, "页面里没有 fetch 调用？采集台不可能不调用 API"
    for target in targets:
        literal = target.strip()
        assert literal.startswith(("'", '"', "`")), f"fetch 参数不是字面量，人工核对：{literal}"
        value = literal[1:]
        assert value.startswith("/") or value.startswith("${"), f"fetch 目标不是本进程相对路径：{target}"
    # 页面导出的单文件 HTML 必须与服务返回的一致（验收③的取证方式）
    export = subprocess.run(
        [sys.executable, "-m", "liveblocks", "serve-cam", "page"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    )
    assert export.returncode == 0, export.stderr
    assert export.stdout == serve_cam.client.get("/").body
