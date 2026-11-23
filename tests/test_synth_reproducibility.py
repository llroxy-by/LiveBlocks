"""可复现性测试：固定种子 → 字节一致；入库 fixtures 与重生成结果逐项一致。

这是 Issue #7 验收条目的主证据：
1. 同一个种子生成两次，PNG 字节、像素、真值 JSON 文本完全相同；
2. 换一个种子，图像确实变化（说明种子真的起作用，不是「写死一张图」）；
3. 仓库里入库的 fixtures 与「用真值里记录的种子重新生成」的结果一致。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from liveblocks.cli import DEFAULT_SEED
from liveblocks.synth import generate_scene
from liveblocks.synth.png import decode_gray

#: Issue #7 要求覆盖的 5 类样例。
REQUIRED_CLASSES = ("normal", "slight-rotation", "overlap", "clipped", "low-light")


def test_same_seed_same_bytes(context) -> None:
    first = generate_scene("normal", seed=DEFAULT_SEED, context=context)
    second = generate_scene("normal", seed=DEFAULT_SEED, context=context)
    assert first.image_png == second.image_png
    assert first.pixels == second.pixels
    assert first.ground_truth_json == second.ground_truth_json
    assert first.ground_truth == second.ground_truth


def test_same_seed_same_bytes_for_every_scene(context, all_scenes) -> None:
    for name, scene in all_scenes.items():
        again = generate_scene(name, seed=DEFAULT_SEED, context=context)
        assert again.image_png == scene.image_png, f"{name} 的 PNG 字节不可复现"
        assert again.ground_truth_json == scene.ground_truth_json, f"{name} 的真值不可复现"


def test_different_seed_changes_pixels(context) -> None:
    base = generate_scene("multi-row", seed=1, context=context)
    other = generate_scene("multi-row", seed=2, context=context)
    assert base.pixels != other.pixels
    assert base.ground_truth_json != other.ground_truth_json
    # 几何确实被抖动过：位姿不同
    poses = [
        (a["pose"], b["pose"])
        for a, b in zip(base.ground_truth["cards"], other.ground_truth["cards"], strict=True)
    ]
    assert any(left != right for left, right in poses)


def test_seed_changes_noise_only_when_scene_has_no_jitter(context) -> None:
    first = generate_scene("normal", seed=10, context=context)
    second = generate_scene("normal", seed=11, context=context)
    # normal 场景没有位姿抖动，几何应完全一致，只有噪声不同
    assert [card["corners_px"] for card in first.ground_truth["cards"]] == [
        card["corners_px"] for card in second.ground_truth["cards"]
    ]
    assert first.pixels != second.pixels


@pytest.mark.parametrize("scene_name", REQUIRED_CLASSES)
def test_required_fixture_files_exist(scene_name: str, scenes_dir) -> None:
    image = scenes_dir / f"{scene_name}.png"
    truth = scenes_dir / f"{scene_name}.json"
    assert image.is_file(), f"缺少 {image}"
    assert truth.is_file(), f"缺少 {truth}"
    payload = json.loads(truth.read_text(encoding="utf-8"))
    assert payload["scene_id"] == scene_name
    assert payload["kind"] == "SyntheticCardScene"
    assert payload["seed"] == DEFAULT_SEED
    assert payload["cards"], "fixture 真值里没有卡片"
    for card in payload["cards"]:
        assert card["card_id"]
        assert len(card["corners_px"]) == 4
        assert set(card["pose"]) == {"x_mm", "y_mm", "z_mm", "yaw_deg", "pitch_deg", "roll_deg"}


def test_index_lists_all_scenes(scenes_dir, all_scenes) -> None:
    index = json.loads((scenes_dir / "index.json").read_text(encoding="utf-8"))
    assert index["kind"] == "SyntheticSceneIndex"
    assert index["seed"] == DEFAULT_SEED
    names = [entry["scene_id"] for entry in index["scenes"]]
    assert sorted(names) == sorted(all_scenes)
    for entry in index["scenes"]:
        scene = all_scenes[entry["scene_id"]]
        assert entry["pixel_sha256"] == scene.pixel_sha256
        assert entry["cards"] == len(scene.ground_truth["cards"])
        assert entry["width"] == scene.width
        assert entry["height"] == scene.height


def test_fixtures_match_regeneration(scenes_dir, all_scenes) -> None:
    """核心验收：入库 fixtures == 用记录种子重生成的结果（字节 + 真值文本）。"""
    for name, scene in all_scenes.items():
        image_bytes = (scenes_dir / f"{name}.png").read_bytes()
        truth_text = (scenes_dir / f"{name}.json").read_text(encoding="utf-8")
        assert image_bytes == scene.image_png, f"{name}.png 与重生成结果字节不同"
        assert truth_text == scene.ground_truth_json, f"{name}.json 与重生成结果文本不同"
        width, height, pixels = decode_gray(image_bytes)
        assert (width, height) == (scene.width, scene.height)
        assert pixels == scene.pixels
        assert hashlib.sha256(pixels).hexdigest() == scene.ground_truth["image"]["pixel_sha256"]


def test_verify_directory_reports_success(scenes_dir, context) -> None:
    from liveblocks.synth.generator import verify_directory

    rows = verify_directory(scenes_dir, context=context)
    assert rows
    for row in rows:
        assert row.ok, f"{row.name}: 字节={'同' if row.image_ok else '异'} 像素={'同' if row.pixel_ok else '异'} 真值={'同' if row.ground_truth_ok else '异'} {row.note}"


def test_verify_directory_detects_tampering(tmp_path, context) -> None:
    from liveblocks.synth.generator import verify_directory

    scene = generate_scene("normal", seed=DEFAULT_SEED, context=context)
    (tmp_path / "normal.png").write_bytes(scene.image_png)
    (tmp_path / "normal.json").write_text(scene.ground_truth_json, encoding="utf-8")

    # 少量篡改真值：验证必须报错
    tampered = scene.ground_truth_json.replace('"x_mm": 0.0', '"x_mm": 1.0', 1)
    (tmp_path / "normal.json").write_text(tampered, encoding="utf-8")
    rows = verify_directory(tmp_path, context=context)
    normal_row = next(row for row in rows if row.name == "normal")
    assert not normal_row.ground_truth_ok
