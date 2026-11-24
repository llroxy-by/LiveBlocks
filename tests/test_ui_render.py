"""一屏渲染（``liveblocks.ui.render``）：五层同屏、可见提示、自包含 HTML、叠加图。

对应 Issue #20 第一条与第三条验收：

- 打开样例记录能看到五层数据同时呈现（HTML 里五个面板都在、都有内容）；
- 缺失/损坏字段给出**可见**提示（页面顶部与面板内的 ``LB-U0xx``），而不是崩溃或空白。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.synth.png import decode_gray
from liveblocks.ui.protocol import LAYER_ORDER, LAYER_TITLES
from liveblocks.ui.record import parse_record_file, parse_record_text
from liveblocks.ui.render import build_view, build_view_from_file

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "fixtures" / "runs"


def _panel_section(layer: str) -> str:
    return f'<section class="panel layer-{layer}" data-layer="{layer}" id="layer-{layer}">'


@pytest.mark.parametrize("name", ("sample-clipped.jsonl", "sample-multi-row.jsonl"))
def test_all_five_layers_appear_in_one_html_page(name: str) -> None:
    view = build_view_from_file(RUN_DIR / name)
    html = view.render_html()
    assert view.complete_layers == LAYER_ORDER
    for layer in LAYER_ORDER:
        assert _panel_section(layer) in html
        assert LAYER_TITLES[layer] in html
    assert "缺失" not in html  # 五层齐全时不该出现缺失标记


@pytest.mark.parametrize("name", ("sample-clipped.jsonl", "sample-multi-row.jsonl"))
def test_panels_have_real_content(name: str) -> None:
    view = build_view_from_file(RUN_DIR / name)
    html = view.render_html()
    vision = html.split(_panel_section("vision"))[1].split("</section>")[0]
    layout = html.split(_panel_section("layout"))[1].split("</section>")[0]
    ast = html.split(_panel_section("ast"))[1].split("</section>")[0]
    ir = html.split(_panel_section("ir"))[1].split("</section>")[0]
    exec_panel = html.split(_panel_section("exec"))[1].split("</section>")[0]
    assert "data:image/png;base64," in vision  # 帧叠加是真图
    assert "<table" in layout and "rowhead" in layout
    assert "class=\"tree\"" in ast
    assert "limits" in ir and ("<table" in ir if view.record.steps else "empty" in ir)
    assert "分层耗时" in exec_panel and "stepbar" in exec_panel


def test_html_is_self_contained_and_needs_no_network() -> None:
    html = build_view_from_file(RUN_DIR / "sample-clipped.jsonl").render_html()
    assert html.startswith("<!DOCTYPE html>")
    assert "<script" not in html.lower()
    assert "http://" not in html and "https://" not in html
    assert "<link" not in html
    assert html.count("data:image/png;base64,") == 1
    assert 'lang="zh-CN"' in html and 'charset="utf-8"' in html


def test_html_has_no_wall_clock_or_generated_at() -> None:
    html = build_view_from_file(RUN_DIR / "sample-clipped.jsonl").render_html()
    for forbidden in ("generated_at", "rendered_at", "datetime.now", "T00:", "Z</"):
        assert forbidden not in html


def test_html_escapes_record_strings() -> None:
    payload = "</script><script>alert(1)</script>"
    text = "\n".join(
        [
            json.dumps({"kind": "RunHeader", "run_id": "run-<evil>", "backend": "sim"}),
            json.dumps(
                {
                    "kind": "CardCandidateList",
                    "cards": [{"candidate_id": payload, "card_face": payload, "center_px": [10, 10]}],
                }
            ),
            json.dumps(
                {
                    "kind": "Layout",
                    "rows": [{"row_index": 0, "slots": [{"slot_index": 0, "card_face": payload}]}],
                }
            ),
        ]
    )
    view = build_view(parse_record_text(text))
    html = view.render_html()
    text_view = view.render_text()
    assert "<script" not in html.lower()
    assert "&lt;script&gt;" in html
    assert payload in text_view  # 文本形态原样输出，不做二次转义


def test_text_view_lists_every_layer_and_the_issue_section() -> None:
    text = build_view_from_file(RUN_DIR / "sample-clipped.jsonl").render_text()
    assert "--- 问题提示 ---" in text
    for layer in LAYER_ORDER:
        assert f"--- {LAYER_TITLES[layer]} ---" in text
    assert "回放（单步）" in text
    assert "只读回放" in text


@pytest.mark.parametrize("name", tuple(path.name for path in sorted((RUN_DIR / "corrupt").glob("*.jsonl"))))
def test_corrupt_records_render_visible_warnings_instead_of_crashing(name: str) -> None:
    view = build_view_from_file(RUN_DIR / "corrupt" / name)
    html = view.render_html()
    text = view.render_text()
    assert view.issues
    assert 'id="warnings"' in html
    assert "LB-U0" in html
    assert "LB-U0" in text
    # 五个面板的骨架仍然在（缺的层显示「缺失」而不是整个页面失败）
    for layer in LAYER_ORDER:
        assert f'data-layer="{layer}"' in html


def test_missing_layer_is_marked_and_placeholder_shown() -> None:
    view = build_view_from_file(RUN_DIR / "sample-all-ops.jsonl")
    html = view.render_html()
    assert view.complete_layers == ("ir", "exec")
    assert html.count('<span class="tag bad">缺失</span>') == 3
    assert "叠加面板显示占位图" in html


def test_missing_timings_are_shown_as_unknown() -> None:
    view = build_view_from_file(RUN_DIR / "corrupt" / "missing-fields.jsonl")
    html = view.render_html()
    assert html.count("记录未提供") >= 1
    assert "LB-U009" in html


# --- 帧叠加图 ----------------------------------------------------------------


def test_overlay_png_is_a_real_rendering_of_the_frame() -> None:
    view = build_view_from_file(RUN_DIR / "sample-clipped.jsonl")
    width, height, pixels = decode_gray(view.overlay_png)
    assert (width, height) == (640, 480)
    raw_width, raw_height, raw_pixels = decode_gray((REPO_ROOT / "fixtures/scenes/clipped.png").read_bytes())
    assert (raw_width, raw_height) == (640, 480)
    changed = sum(1 for left, right in zip(pixels, raw_pixels) if left != right)
    assert changed > width * height * 0.01, f"叠加图几乎没改动原帧（{changed} 像素）"


def test_overlay_png_is_byte_stable() -> None:
    first = build_view_from_file(RUN_DIR / "sample-clipped.jsonl").overlay_png
    second = build_view_from_file(RUN_DIR / "sample-clipped.jsonl").overlay_png
    assert first == second
    assert first.startswith(b"\x89PNG\r\n\x1a\n")


def test_overlay_without_frame_is_a_visible_placeholder() -> None:
    view = build_view_from_file(RUN_DIR / "sample-all-ops.jsonl")
    width, height, pixels = decode_gray(view.overlay_png)
    assert (width, height) == (640, 480)
    # 占位图是「斜纹底 + 深色说明框 + 亮色文字」，不是纯色
    assert len(set(pixels)) >= 3
    assert {96, 108} <= set(pixels)
    assert any(entry.code == "LB-U007" for entry in view.issues)


def test_unreadable_frame_image_reports_and_still_renders(tmp_path: Path) -> None:
    broken = tmp_path / "frame.png"
    broken.write_bytes(b"not a png at all")
    record_path = tmp_path / "record.jsonl"
    record_path.write_text(
        json.dumps(
            {
                "kind": "RunHeader",
                "run_id": "run-broken-frame",
                "backend": "sim",
                "frame": {"frame_id": "f-x", "image_ref": "frame.png", "width": 640, "height": 480},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    view = build_view_from_file(record_path)
    assert any(entry.code == "LB-U007" for entry in view.issues)
    width, height, _pixels = decode_gray(view.overlay_png)
    assert (width, height) == (640, 480)


def test_frame_size_mismatch_is_only_a_warning() -> None:
    record_path = RUN_DIR / "sample-clipped.jsonl"
    document = json.loads(record_path.read_text(encoding="utf-8").splitlines()[0])
    document["frame"]["width"] = 100
    document["frame"]["height"] = 100
    lines = record_path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps(document)
    view = build_view(parse_record_text("\n".join(lines), source=str(record_path)), record_path=record_path)
    mismatch = [entry for entry in view.issues if entry.code == "LB-U007" and entry.severity == "warning"]
    assert mismatch and "100x100" in mismatch[0].message
    width, height, _pixels = decode_gray(view.overlay_png)
    assert (width, height) == (640, 480)  # 按实际图像尺寸叠加


def test_view_falls_back_to_placeholder_when_header_has_no_frame() -> None:
    view = build_view(parse_record_text(json.dumps({"kind": "RunHeader", "run_id": "r", "backend": "sim"})))
    assert view.record.frame == {}
    assert any(entry.code == "LB-U007" for entry in view.issues)
    width, height, _pixels = decode_gray(view.overlay_png)
    assert (width, height) == (640, 480)
