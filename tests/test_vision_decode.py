"""码字译码与单应：用 LB-002 生成器造「已知位姿」的图，验证 id、朝向与角点顺序。"""

from __future__ import annotations

import dataclasses
import math

import pytest

from liveblocks.synth import generate_scene
from liveblocks.synth.geometry import Camera
from liveblocks.synth.spec import Lighting, Placement, SceneSpec
from liveblocks.vision import GrayImage, decode_marker, homography, load_frame, sample_modules
from liveblocks.vision.decode import rotations_of
from liveblocks.vision.pose import marker_corners_mm

CARD_LAYOUT = "card-60x40"
MARKER_MATM = 24.0


@pytest.fixture(scope="module")
def aruco():
    from liveblocks.synth.dictionaries import load_aruco_dictionaries

    return load_aruco_dictionaries()["DICT_4X4_50"]


def render_single_card(context, *, yaw_deg: float = 0.0, card_id: str = "C01", z_mm: float = 300.0):
    """按给定 yaw 生成一张单卡图，返回（识别用的 Frame, 该卡真值）。"""
    base = context.presets["normal"]
    spec = SceneSpec(
        name="vision-decode",
        description="识别层解码测试用场景",
        width=640,
        height=480,
        background_shade=58,
        card_layout=CARD_LAYOUT,
        camera=Camera(700.0, 700.0, 320.0, 240.0),
        reading_direction="ltr_ttb",
        placements=(Placement(card_id, 0.0, 0.0, z_mm, float(yaw_deg), 0.0, 0.0, 0),),
        occluders=(),
        lighting=Lighting(1.0, 1.5, 0.08, 0),
        jitter=base.jitter,
    )
    patched = dataclasses.replace(context, presets={**context.presets, spec.name: spec})
    scene = generate_scene(spec.name, seed=20261123, context=patched)
    path = None
    import tempfile

    handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    handle.write(scene.image_png)
    handle.close()
    path = handle.name
    frame = load_frame(path)
    return frame, scene.ground_truth["cards"][0]


def test_homography_maps_unit_square_corners() -> None:
    quad = [(10.0, 20.0), (110.0, 25.0), (100.0, 120.0), (15.0, 115.0)]
    matrix = homography([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], quad)
    from liveblocks.synth.geometry import apply_homography

    for u, v in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
        got = apply_homography(matrix, u, v)
        want = quad[{(0.0, 0.0): 0, (1.0, 0.0): 1, (1.0, 1.0): 2, (0.0, 1.0): 3}[(u, v)]]
        assert got == pytest.approx(want, abs=1e-9)


def test_homography_is_stable_for_small_quads() -> None:
    """小四边形（19 px 的标记）在归一化 DLT 下也不能把透视项放大到失真。"""
    quad = [(313.0, 187.0), (332.0, 187.0), (332.0, 207.0), (312.0, 206.0)]
    matrix = homography(list(marker_corners_mm(16.0)), quad)
    from liveblocks.synth.geometry import apply_homography

    for (u, v), want in zip(marker_corners_mm(16.0), quad):
        assert apply_homography(matrix, u, v) == pytest.approx(want, abs=1e-6)
    # 外推到卡片中心（离标记约 10 mm）也必须是有限且合理的像素坐标
    x, y = apply_homography(matrix, 8.0, 22.5)
    assert 250.0 < x < 400.0
    assert 150.0 < y < 300.0


def test_rotations_of_code_cycle_back(aruco) -> None:
    code = aruco.codes[0]
    rotations = rotations_of(code, aruco.marker_modules)
    assert rotations[0] == code
    assert rotations[2] == rotations_of(rotations[1], aruco.marker_modules)[1]
    assert len(set(rotations)) == 4  # DICT_4X4_50 的码字没有 90° 自对称


@pytest.mark.parametrize("yaw_deg", [0.0, 90.0, 180.0, 270.0])
def test_decode_marker_recovers_identity_and_corner_order(context, aruco, yaw_deg: float) -> None:
    frame, truth = render_single_card(context, yaw_deg=yaw_deg)
    quad = [tuple(point) for point in truth["marker_corners_px"]]
    result = decode_marker(frame.image, quad, aruco)
    assert result.match.marker_id == truth["marker_id"]
    assert result.match.bit_errors == 0
    assert result.bits == aruco.codes[truth["marker_id"]]
    # 用真值角点作为四边形去解码时，旋转数与角点顺序应当相互自洽：
    rotations = result.match.rotations
    # 真值角点本身已经按标记坐标系（左上/右上/右下/左下）给出，所以采样帧就是标记坐标系，
    # 任何 yaw（0/90/180/270）下都不需要再旋转即可与码字完全一致。
    assert rotations == 0
    ordered = [quad[(i + rotations) % 4] for i in range(4)]
    for found, want in zip(ordered, truth["marker_corners_px"]):
        assert found == pytest.approx(tuple(want), abs=0.01)


def test_decode_marker_reports_rotation_of_input_order(context, aruco) -> None:
    """输入的四边形顶点顺序偏离标记坐标系时，``rotations`` 指出要转几次才对齐。"""
    frame, truth = render_single_card(context, yaw_deg=0.0)
    quad = [tuple(point) for point in truth["marker_corners_px"]]
    for shift in (0, 1, 2, 3):
        # 输入顶点整体错开 shift 个位置：采样帧等于把标记坐标系逆时针转了 shift 个角，
        # 因此要顺时针转 (4 - shift) % 4 次才回到码字的规范朝向。
        rotated_input = [quad[(i + shift) % 4] for i in range(4)]
        result = decode_marker(frame.image, rotated_input, aruco)
        assert result.match.marker_id == truth["marker_id"]
        assert result.match.rotations == (4 - shift) % 4
        # 无论错开多少，按 rotations 还原出来的顺序都必须等于标记自身的左上/右上/右下/左下
        ordered = [rotated_input[(i + result.match.rotations) % 4] for i in range(4)]
        for found, want in zip(ordered, truth["marker_corners_px"]):
            assert found == pytest.approx(tuple(want), abs=0.01)


def test_decode_marker_rejects_blank_and_noise(aruco) -> None:
    blank = GrayImage(64, 64, bytes([200]) * 64 * 64)
    quad = [(10.0, 10.0), (54.0, 10.0), (54.0, 54.0), (10.0, 54.0)]
    result = decode_marker(blank, quad, aruco)
    assert result.match.marker_id is None
    assert result.border_dark is False


def test_decode_marker_tolerates_one_flipped_bit(context, aruco) -> None:
    """把一个模块涂白模拟脏点：允许 1 位错时仍应识别，且误差计数为 1。"""
    frame, truth = render_single_card(context)
    quad = [tuple(point) for point in truth["marker_corners_px"]]
    matrix = homography(
        [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], quad
    )
    from liveblocks.synth.geometry import apply_homography

    modules = aruco.marker_modules + 2 * aruco.border_bits
    code = aruco.codes[truth["marker_id"]]
    # 找一个「本来是黑」的信息位（码字里的 "0"），把它涂白就翻了 1 位
    index = code.index("0")
    row, col = divmod(index, aruco.marker_modules)
    cx, cy = apply_homography(
        matrix, (col + 1 + 0.5) / modules, (row + 1 + 0.5) / modules
    )
    pixels = bytearray(frame.image.pixels)
    width = frame.image.width
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            pixels[int(cy + dy) * width + int(cx + dx)] = 244
    corrupted = GrayImage(frame.image.width, frame.image.height, bytes(pixels))

    tolerant = decode_marker(corrupted, quad, aruco, max_bit_errors=1)
    assert tolerant.match.bit_errors == 1
    strict = decode_marker(corrupted, quad, aruco, max_bit_errors=0)
    assert strict.match.marker_id is None


def test_sample_modules_shapes() -> None:
    image = GrayImage(20, 20, bytes([255]) * 400)
    values = sample_modules(image, [(2.0, 2.0), (18.0, 2.0), (18.0, 18.0), (2.0, 18.0)], 6)
    assert len(values) == 6
    assert all(len(row) == 6 for row in values)
    assert all(value == pytest.approx(255.0) for row in values for value in row)
    assert math.isfinite(values[0][0])
