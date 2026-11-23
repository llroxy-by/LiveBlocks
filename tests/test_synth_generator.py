"""生成器测试：真值结构、几何一致性、五类样例的可见性与光照差异。"""

from __future__ import annotations

import json

import pytest

from liveblocks.synth.png import decode_gray

REQUIRED_CARD_FIELDS = {
    "candidate_id",
    "card_id",
    "card_face",
    "card_type",
    "marker_id",
    "pose",
    "center_px",
    "corners_px",
    "marker_corners_px",
    "status",
    "visible_fraction",
    "clipped",
    "occluded_by",
    "covered_by_cards",
}


def _scene(all_scenes, name):
    return all_scenes[name]


def test_ground_truth_is_json_serialisable(all_scenes) -> None:
    for scene in all_scenes.values():
        assert json.loads(scene.ground_truth_json) == scene.ground_truth
        assert scene.ground_truth_json.endswith("\n")


def test_ground_truth_shape(all_scenes, context) -> None:
    for name, scene in all_scenes.items():
        truth = scene.ground_truth
        assert truth["kind"] == "SyntheticCardScene"
        assert truth["scene_id"] == name
        assert truth["image"]["width"] == scene.width
        assert truth["image"]["height"] == scene.height
        assert truth["image"]["file"] == f"{name}.png"
        assert truth["camera"]["fx"] == pytest.approx(700.0)
        assert truth["cards"], "每个场景至少要有一张卡"
        for card in truth["cards"]:
            assert REQUIRED_CARD_FIELDS <= set(card)
            assert len(card["corners_px"]) == 4
            assert len(card["marker_corners_px"]) == 4
            assert card["status"] in {"ok", "clipped", "occluded", "unknown_card", "ambiguous"}
            assert 0.0 <= card["visible_fraction"] <= 1.0
            entry = context.card_dictionary.card(card["card_id"])
            assert card["marker_id"] == entry.marker_id
            assert card["card_face"] == entry.card_face


def test_card_count_matches_preset(all_scenes, context) -> None:
    for name, scene in all_scenes.items():
        spec = context.presets[name]
        assert len(scene.ground_truth["cards"]) == len(spec.placements)
        assert {card["card_id"] for card in scene.ground_truth["cards"]} == {
            placement.card_id for placement in spec.placements
        }


def test_corners_are_ordered_tl_tr_br_bl(all_scenes) -> None:
    for name, scene in all_scenes.items():
        for card in scene.ground_truth["cards"]:
            corners = card["corners_px"]
            # 四个角一律按顺时针给出（y 向下的图像坐标里叉积同号）
            signs = []
            for index in range(4):
                ax, ay = corners[index]
                bx, by = corners[(index + 1) % 4]
                cx, cy = corners[(index + 2) % 4]
                cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
                signs.append(1 if cross > 0 else -1)
            assert len(set(signs)) == 1, f"{name}/{card['card_id']} 角点不构成凸四边形"
            # 中心必须落在外接矩形内（透视下质心不必等于投影中心）
            xs = [point[0] for point in corners]
            ys = [point[1] for point in corners]
            assert min(xs) <= card["center_px"][0] <= max(xs)
            assert min(ys) <= card["center_px"][1] <= max(ys)


def test_pose_matches_in_plane_angle(all_scenes) -> None:
    for name, scene in all_scenes.items():
        for card in scene.ground_truth["cards"]:
            delta = abs(card["in_plane_angle_deg"] - card["pose"]["yaw_deg"])
            delta = min(delta, 360.0 - delta)
            assert delta < 2.0, f"{name}/{card['card_id']} 面内角与 yaw 不一致：{delta}"


def test_normal_scene_is_fully_visible(all_scenes) -> None:
    scene = _scene(all_scenes, "normal")
    scene_spec = scene.ground_truth
    assert len(scene_spec["cards"]) == 1
    card = scene_spec["cards"][0]
    assert card["status"] == "ok"
    assert card["visible_fraction"] == pytest.approx(1.0)
    assert card["clipped"] is False
    assert card["occluded_by"] == []
    assert card["covered_by_cards"] == []
    for x, y in card["corners_px"]:
        assert 0.0 <= x < scene.width
        assert 0.0 <= y < scene.height


def test_slight_rotation_scene_has_small_angles(all_scenes) -> None:
    scene = _scene(all_scenes, "slight-rotation")
    cards = scene.ground_truth["cards"]
    assert len(cards) == 3
    for card in cards:
        assert card["status"] == "ok"
        assert 1.0 <= abs(card["in_plane_angle_deg"]) <= 12.0
    # 三张卡的 x 坐标严格递增（同一行、按阅读方向排列）
    xs = [card["center_px"][0] for card in cards]
    assert xs == sorted(xs)


def test_overlap_scene_reports_occlusion(all_scenes) -> None:
    scene = _scene(all_scenes, "overlap")
    truth = scene.ground_truth
    cards = truth["cards"]
    assert len(cards) == 3
    covered = [card for card in cards if card["covered_by_cards"]]
    assert covered, "重叠样例里必须有卡片被其它卡片盖住"
    assert all(card["status"] == "occluded" for card in covered)
    assert truth["occluders"], "重叠样例应记录遮挡物几何"
    occluded = [card for card in cards if card["occluded_by"]]
    assert occluded, "重叠样例里必须有卡片被遮挡物盖住"
    for card in cards:
        assert card["visible_fraction"] < 1.0
        assert card["clipped"] is False
    assert truth["occluders"][0]["occluder_id"] == "o-0"
    assert len(truth["occluders"][0]["corners_px"]) == 4


def test_clipped_scene_has_cards_out_of_frame(all_scenes) -> None:
    scene = _scene(all_scenes, "clipped")
    cards = scene.ground_truth["cards"]
    assert len(cards) == 2
    for card in cards:
        assert card["status"] == "clipped"
        assert card["clipped"] is True
        assert card["clipped_corners"] >= 1
        assert card["visible_fraction"] < 0.95
    # 至少有一个角点真的落在画面外
    outside = [
        (card["card_id"], x, y)
        for card in cards
        for x, y in card["corners_px"]
        if not (0.0 <= x < scene.width and 0.0 <= y < scene.height)
    ]
    assert outside


def test_low_light_scene_is_darker(all_scenes) -> None:
    normal = _scene(all_scenes, "normal")
    low = _scene(all_scenes, "low-light")
    normal_mean = sum(normal.pixels) / len(normal.pixels)
    low_mean = sum(low.pixels) / len(low.pixels)
    assert low_mean < normal_mean * 0.5, f"低光照均值 {low_mean:.1f} 不够暗（正常 {normal_mean:.1f}）"
    assert low.ground_truth["lighting"]["gain"] < 0.5
    assert low.ground_truth["lighting"]["noise_sigma"] >= 5.0
    for card in low.ground_truth["cards"]:
        assert card["status"] == "ok"


def test_multi_row_scene(all_scenes, context) -> None:
    scene = _scene(all_scenes, "multi-row")
    cards = scene.ground_truth["cards"]
    assert len(cards) == 6
    ys = sorted({round(card["center_px"][1]) for card in cards})
    # 两行：行间 y 明显分开（行内差异受抖动影响但远小于行距）
    assert max(ys) - min(ys) > 20
    top = [y for y in ys if y < (max(ys) + min(ys)) / 2]
    bottom = [y for y in ys if y >= (max(ys) + min(ys)) / 2]
    assert (max(top) - min(top)) < (min(bottom) - max(top))
    # 抖动生效：实际位姿与预设不同
    preset = {placement.card_id: placement for placement in context.presets["multi-row"].placements}
    differs = [
        card
        for card in cards
        if abs(card["pose"]["x_mm"] - preset[card["card_id"]].x_mm) > 1e-6
        or abs(card["pose"]["yaw_deg"] - preset[card["card_id"]].yaw_deg) > 1e-6
    ]
    assert differs, "multi-row 场景应当有位姿抖动"


def test_every_card_is_salient(all_scenes) -> None:
    """卡身必须比背景亮得多、标记必须比卡身暗得多（否则「合成图」没有可测内容）。"""
    for name, scene in all_scenes.items():
        width, height, pixels = decode_gray(scene.image_png)
        assert (width, height) == (scene.width, scene.height)
        for card in scene.ground_truth["cards"]:
            if card["visible_fraction"] < 0.3:
                continue
            xs = [point[0] for point in card["corners_px"]]
            ys = [point[1] for point in card["corners_px"]]
            x0, x1 = int(max(0, min(xs))), int(min(width, max(xs)))
            y0, y1 = int(max(0, min(ys))), int(min(height, max(ys)))
            if x1 - x0 < 6 or y1 - y0 < 6:
                continue
            interior = [
                pixels[y * width + x]
                for y in range(y0 + 2, y1 - 2)
                for x in range(x0 + 2, x1 - 2)
            ]
            assert interior
            darkest = min(interior)
            brightest = max(interior)
            assert brightest - darkest > 40, f"{name}/{card['card_id']} 对比度不足"


def test_image_matches_pixel_sha(all_scenes) -> None:
    import hashlib

    for scene in all_scenes.values():
        assert scene.ground_truth["image"]["pixel_sha256"] == hashlib.sha256(scene.pixels).hexdigest()


def test_unknown_scene_raises() -> None:
    from liveblocks.synth import SynthContext, generate_scene

    context = SynthContext.load()
    with pytest.raises(KeyError, match="没有场景预设"):
        generate_scene("no-such-scene", seed=1, context=context)
