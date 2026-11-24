"""识别叠加图：尺寸、可解码、确定性、真的画了东西。

「字节可复现」是这套叠加图能当证据的前提（LB-008 的确定性光栅）：同一帧 + 同一识别结果
渲染两次必须字节相等，否则「截图能对上」就是空话。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from liveblocks.serve_cam import ServeCamApp, ServeCamConfig, TestClient, render_overlay
from liveblocks.synth.png import decode_gray
from liveblocks.vision import CardRegistry, load_frame, recognize

REPO_ROOT = Path(__file__).resolve().parents[1]
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"


def _recognized(scene: str):
    frame = load_frame(SCENES_DIR / f"{scene}.png")
    registry = CardRegistry.load()
    return frame, recognize(frame, registry=registry)


@pytest.mark.parametrize("scene", ["normal", "multi-row", "clipped"])
def test_overlay_is_deterministic(scene: str) -> None:
    frame, result = _recognized(scene)
    first = render_overlay(frame, result)
    second = render_overlay(frame, result)
    assert first == second
    width, height, _pixels = decode_gray(first)
    assert (width, height) == (frame.width, frame.height)


def test_overlay_marks_the_cards_it_recognised() -> None:
    frame, result = _recognized("normal")
    assert result.cards.cards, "normal 场景应该有 1 张卡，否则这条测试没有意义"
    overlay = render_overlay(frame, result)
    _w, _h, pixels = decode_gray(overlay)
    original = frame.image.pixels
    changed = sum(1 for before, after in zip(original, pixels) if before != after)
    assert changed > 200
    assert max(pixels) >= 245


def test_overlay_of_an_empty_frame_stays_a_valid_png() -> None:
    """没有卡片时叠加图也必须是一张合法 PNG（图例仍然画在最上面）。"""
    width, height = 64, 48
    blank = bytes([200]) * (width * height)
    app = ServeCamApp(ServeCamConfig(live_dir=Path("/tmp/lb030-unused")))
    client = TestClient(app)
    payload = client.post_gray("/api/frame", width, height, blank).json()
    assert payload["card_candidate_list"]["cards"] == []
    import base64

    png = base64.b64decode(payload["overlay"]["png_base64"])
    decoded_width, decoded_height, pixels = decode_gray(png)
    assert (decoded_width, decoded_height) == (width, height)
    assert max(pixels) >= 245  # 图例文字
    assert min(pixels) < 30  # 图例底色


def test_overlay_labels_are_ascii_only() -> None:
    """点阵字形集是 ASCII：叠加图里不能出现中文（否则会被替换成下划线，等于信息丢失）。"""
    frame, result = _recognized("normal")
    png = render_overlay(frame, result)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # 叠加图本身是灰度 PNG，没有文本层；这里核对标签来源都是 ASCII
    for card in result.cards.cards:
        assert card.card_id.isascii()
        assert card.card_face.isascii()
        assert card.status.isascii()
    assert frame.frame_id.isascii()


def test_overlay_extra_lines_are_rendered(tmp_path: Path) -> None:
    frame, result = _recognized("normal")
    plain = render_overlay(frame, result)
    annotated = render_overlay(frame, result, extra_lines=["camera=phone-rear", "capture=LB-030"])
    assert plain != annotated
    width, height, _pixels = decode_gray(annotated)
    assert (width, height) == (frame.width, frame.height)
