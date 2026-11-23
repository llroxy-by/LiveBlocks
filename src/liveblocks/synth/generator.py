"""合成卡片场景生成器：场景规格 + 随机种子 → 图像字节 + 真值 JSON。

一次生成是**纯函数**：同样的 ``(场景, 种子)`` 必然得到同样的像素、同样的真值 JSON 文本
与同样的 PNG 字节（同一 zlib 版本内），因为

1. 所有随机量都来自 ``random.Random(seed)``，且使用顺序固定（抖动 → 每张卡 → 光照噪声）；
2. 光栅化是纯 Python 整数/浮点运算，没有并行、没有字典顺序依赖；
3. PNG 由本包自己的编码器写（``filter`` 固定、压缩级别固定）。

真值描述「实际画出来的东西」：位姿是**抖动之后**的位姿，角点是投影后的图像坐标，
可见比例按「出画面 / 被遮挡物盖住 / 被后画的卡片盖住」三种情况分别统计。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import __version__
from . import geometry
from .dictionaries import ArucoDictionary, CardDictionary, load_aruco_dictionaries, load_card_dictionary
from .font import BitmapFont
from .png import encode_gray
from .raster import (
    CardTexture,
    Raster,
    apply_lighting,
    box_blur,
    fill_convex_polygon,
    project_bbox,
    warp_texture,
)
from .spec import Lighting, Placement, SceneSpec, apply_jitter, load_layouts
from .texture import CardLayout, render_card_texture

SCHEMA_VERSION = "0.0.1"
GROUND_TRUTH_KIND = "SyntheticCardScene"

#: 计算可见比例时在卡面上取的采样网格（横向、纵向）。
VISIBILITY_SAMPLES = (24, 16)


@dataclass(frozen=True)
class GeneratedScene:
    """一次生成的完整结果。"""

    name: str
    seed: int
    width: int
    height: int
    image_png: bytes
    pixels: bytes
    ground_truth: dict[str, Any]
    ground_truth_json: str

    @property
    def image_name(self) -> str:
        return f"{self.name}.png"

    @property
    def ground_truth_name(self) -> str:
        return f"{self.name}.json"

    @property
    def pixel_sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()


@dataclass(frozen=True)
class SynthContext:
    """一次生成需要的全部外部定义（数据文件解析后的不可变视图）。"""

    layouts: dict[str, CardLayout]
    presets: dict[str, SceneSpec]
    aruco_dictionaries: dict[str, ArucoDictionary]
    card_dictionary: CardDictionary
    font: BitmapFont
    layout_version: str

    @classmethod
    def load(cls) -> "SynthContext":
        layouts, presets, layout_version = load_layouts()
        aruco_dictionaries = load_aruco_dictionaries()
        card_dictionary = load_card_dictionary(aruco_dictionaries)
        return cls(
            layouts=layouts,
            presets=presets,
            aruco_dictionaries=aruco_dictionaries,
            card_dictionary=card_dictionary,
            font=BitmapFont.load(),
            layout_version=layout_version,
        )

    @property
    def aruco(self) -> ArucoDictionary:
        return self.aruco_dictionaries[self.card_dictionary.aruco_dictionary]

    def scene(self, name: str) -> SceneSpec:
        try:
            return self.presets[name]
        except KeyError:
            raise KeyError(
                f"没有场景预设 {name!r}；可用：{', '.join(sorted(self.presets))}"
            ) from None

    def card_layout(self, name: str) -> CardLayout:
        try:
            return self.layouts[name]
        except KeyError:
            raise KeyError(f"没有卡面版式 {name!r}；可用：{', '.join(sorted(self.layouts))}") from None


def _r(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _points(points: list[tuple[float, float]]) -> list[list[float]]:
    return [[_r(x), _r(y)] for x, y in points]


def generate_scene(
    name: str, *, seed: int, context: SynthContext | None = None
) -> GeneratedScene:
    """按场景预设 ``name`` 与随机种子 ``seed`` 生成一张合成图与配套真值。"""
    ctx = context or SynthContext.load()
    spec = ctx.scene(name)
    aruco = ctx.aruco
    layout = ctx.card_layout(spec.card_layout)
    spec.validate(ctx.layouts, aruco)

    jittered = apply_jitter(spec, random.Random(seed))
    lighting = jittered.lighting
    _validate_lighting(lighting)

    canvas = Raster.filled(jittered.width, jittered.height, jittered.background_shade)
    textures = _render_textures(jittered, ctx, layout)
    drawn = _draw_cards(canvas, jittered, layout, textures)
    occluder_quads = _draw_occluders(canvas, jittered)
    _apply_lighting(canvas, lighting, seed)

    pixels = canvas.as_bytes()
    png_bytes = encode_gray(canvas.width, canvas.height, pixels)
    ground_truth = _build_ground_truth(
        scene=jittered,
        seed=seed,
        layout=layout,
        context=ctx,
        pixels=pixels,
        drawn=drawn,
        occluder_quads=occluder_quads,
    )
    return GeneratedScene(
        name=name,
        seed=seed,
        width=canvas.width,
        height=canvas.height,
        image_png=png_bytes,
        pixels=pixels,
        ground_truth=ground_truth,
        ground_truth_json=dump_ground_truth(ground_truth),
    )


def _validate_lighting(lighting: Lighting) -> None:
    if lighting.gain < 0:
        raise ValueError(f"gain 必须非负：{lighting.gain}")
    if lighting.noise_sigma < 0:
        raise ValueError(f"noise_sigma 必须非负：{lighting.noise_sigma}")
    if not 0.0 <= lighting.vignette <= 1.0:
        raise ValueError(f"vignette 必须在 0..1：{lighting.vignette}")
    if lighting.blur_radius_px < 0:
        raise ValueError(f"blur_radius_px 必须非负：{lighting.blur_radius_px}")


def _render_textures(
    spec: SceneSpec, ctx: SynthContext, layout: CardLayout
) -> dict[str, CardTexture]:
    aruco = ctx.aruco
    textures: dict[str, CardTexture] = {}
    for placement in spec.placements:
        card = ctx.card_dictionary.card(placement.card_id)
        textures[placement.card_id] = render_card_texture(card, layout, aruco, ctx.font)
    return textures


@dataclass(frozen=True)
class DrawnCard:
    """绘制阶段的中间结果，供真值构造复用。"""

    candidate_id: str
    placement_index: int
    draw_index: int
    placement: Placement
    pose: geometry.Pose
    plane_homography: geometry.Matrix3
    plane_inverse: geometry.Matrix3
    corners_px: list[tuple[float, float]]
    marker_corners_px: list[tuple[float, float]]
    drawn_pixels: int


def _draw_cards(
    canvas: Raster, spec: SceneSpec, layout: CardLayout, textures: dict[str, CardTexture]
) -> list[DrawnCard]:
    order = sorted(
        range(len(spec.placements)),
        key=lambda index: (spec.placements[index].z_order, index),
    )
    drawn: list[DrawnCard] = []
    for draw_index, placement_index in enumerate(order):
        placement = spec.placements[placement_index]
        pose = placement.pose()
        texture = textures[placement.card_id]
        plane_homography = geometry.plane_homography(spec.camera, pose)
        homography = geometry.texture_homography(
            spec.camera,
            pose,
            width_px=texture.width,
            height_px=texture.height,
            pixels_per_mm=texture.pixels_per_mm,
        )
        inverse = geometry.mat_inv(homography)
        corners = geometry.card_corners_px(
            spec.camera,
            pose,
            width_mm=texture.width_mm,
            height_mm=texture.height_mm,
        )
        bbox = project_bbox(corners, canvas.width, canvas.height, margin=2)
        written = 0
        if bbox is not None:
            written = warp_texture(canvas, texture, inverse, bbox=bbox)
        drawn.append(
            DrawnCard(
                candidate_id=f"c-{placement_index}",
                placement_index=placement_index,
                draw_index=draw_index,
                placement=placement,
                pose=pose,
                plane_homography=plane_homography,
                plane_inverse=geometry.mat_inv(plane_homography),
                corners_px=corners,
                marker_corners_px=geometry.marker_corners_px(
                    spec.camera,
                    pose,
                    card_width_mm=texture.width_mm,
                    card_height_mm=texture.height_mm,
                    marker_center_mm=layout.marker_center_mm,
                    marker_size_mm=layout.marker_size_mm,
                ),
                drawn_pixels=written,
            )
        )
    return drawn


def _scene_projection(spec: SceneSpec, z_mm: float) -> geometry.Matrix3:
    """相机坐标毫米 → 图像像素 的单应（遮挡物与卡片共用同一相机模型）。"""
    return geometry.plane_homography(spec.camera, geometry.Pose(x_mm=0.0, y_mm=0.0, z_mm=z_mm))


def _draw_occluders(canvas: Raster, spec: SceneSpec) -> list[tuple[str, list[tuple[float, float]]]]:
    projected: list[tuple[int, str, list[tuple[float, float]]]] = []
    for occluder in spec.occluders:
        homography = _scene_projection(spec, occluder.z_mm)
        quad = [geometry.apply_homography(homography, u, v) for u, v in occluder.corners_mm]
        projected.append((occluder.z_order, occluder.occluder_id, quad))
    projected.sort(key=lambda item: (item[0], item[1]))
    result: list[tuple[str, list[tuple[float, float]]]] = []
    for _, occluder_id, quad in projected:
        occluder = next(o for o in spec.occluders if o.occluder_id == occluder_id)
        fill_convex_polygon(canvas, quad, occluder.shade)
        result.append((occluder_id, quad))
    return result


def _apply_lighting(canvas: Raster, lighting: Lighting, seed: int) -> None:
    if lighting.blur_radius_px:
        box_blur(canvas, lighting.blur_radius_px)
    apply_lighting(
        canvas,
        gain=lighting.gain,
        vignette=lighting.vignette,
        noise_sigma=lighting.noise_sigma,
        rng=random.Random(seed),
    )


def _visibility(
    spec: SceneSpec,
    card: DrawnCard,
    drawn: list[DrawnCard],
    layout: CardLayout,
    occluder_quads: list[tuple[str, list[tuple[float, float]]]],
) -> dict[str, Any]:
    half_w = layout.image_width_mm / 2.0
    half_h = layout.image_height_mm / 2.0
    samples_x, samples_y = VISIBILITY_SAMPLES
    total = samples_x * samples_y
    out_of_frame = 0
    occluded = 0
    covered = 0
    covering: set[str] = set()
    occluded_by: set[str] = set()

    later = [other for other in drawn if other.draw_index > card.draw_index]

    for iy in range(samples_y):
        # 采样点落在卡面内部（留出 1% 余量，避免边界退化成「半个像素」问题）
        v = -half_h + (iy + 0.5) * (2.0 * half_h / samples_y)
        v = max(-half_h * 0.98, min(half_h * 0.98, v))
        for ix in range(samples_x):
            u = -half_w + (ix + 0.5) * (2.0 * half_w / samples_x)
            u = max(-half_w * 0.98, min(half_w * 0.98, u))
            point = geometry.apply_homography(card.plane_homography, u, v)
            if not (0.0 <= point[0] < spec.width and 0.0 <= point[1] < spec.height):
                out_of_frame += 1
                continue
            hit_occluder = False
            for occluder_id, quad in occluder_quads:
                if geometry.point_in_convex_quad(point, quad):
                    occluded_by.add(occluder_id)
                    hit_occluder = True
                    break
            if hit_occluder:
                occluded += 1
                continue
            for other in later:
                if geometry.point_in_card(
                    point,
                    other.plane_inverse,
                    width_mm=layout.image_width_mm,
                    height_mm=layout.image_height_mm,
                ):
                    covering.add(other.candidate_id)
                    covered += 1
                    break

    hidden = out_of_frame + occluded + covered
    visible_fraction = _r((total - hidden) / total, 4)
    clipped_corners = sum(
        1
        for x, y in card.corners_px
        if not (0.0 <= x < spec.width and 0.0 <= y < spec.height)
    )
    clipped = clipped_corners > 0 or out_of_frame > 0
    return {
        "visible_fraction": visible_fraction,
        "out_of_frame_fraction": _r(out_of_frame / total, 4),
        "occluded_fraction": _r(occluded / total, 4),
        "covered_by_cards_fraction": _r(covered / total, 4),
        "clipped": clipped,
        "clipped_corners": clipped_corners,
        "occluded_by": sorted(occluded_by),
        "covered_by_cards": sorted(covering),
    }


def _status(visibility: dict[str, Any]) -> str:
    if visibility["clipped"]:
        return "clipped"
    if visibility["occluded_by"] or visibility["covered_by_cards"]:
        return "occluded"
    return "ok"


def _build_ground_truth(
    *,
    scene: SceneSpec,
    seed: int,
    layout: CardLayout,
    context: SynthContext,
    pixels: bytes,
    drawn: list[DrawnCard],
    occluder_quads: list[tuple[str, list[tuple[float, float]]]],
) -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    for card in sorted(drawn, key=lambda item: item.placement_index):
        entry = context.card_dictionary.card(card.placement.card_id)
        visibility = _visibility(scene, card, drawn, layout, occluder_quads)
        cards.append(
            {
                "candidate_id": card.candidate_id,
                "card_id": entry.card_id,
                "card_face": entry.card_face,
                "card_type": entry.card_type,
                "marker_id": entry.marker_id,
                "aruco_dictionary": context.card_dictionary.aruco_dictionary,
                "card_layout": layout.name,
                "z_order": card.placement.z_order,
                "draw_index": card.draw_index,
                "pose": card.pose.to_dict(),
                "center_px": _points([geometry.apply_homography(card.plane_homography, 0.0, 0.0)])[0],
                "corners_px": _points(card.corners_px),
                "marker_corners_px": _points(card.marker_corners_px),
                "in_plane_angle_deg": _r(geometry.in_plane_angle_deg(card.corners_px)),
                "status": _status(visibility),
                "visible_fraction": visibility["visible_fraction"],
                "out_of_frame_fraction": visibility["out_of_frame_fraction"],
                "occluded_fraction": visibility["occluded_fraction"],
                "covered_by_cards_fraction": visibility["covered_by_cards_fraction"],
                "clipped": visibility["clipped"],
                "clipped_corners": visibility["clipped_corners"],
                "occluded_by": visibility["occluded_by"],
                "covered_by_cards": visibility["covered_by_cards"],
                "drawn_pixels": card.drawn_pixels,
            }
        )

    occluders = [
        {
            "occluder_id": occluder_id,
            "kind": "quad",
            "corners_px": _points(quad),
        }
        for occluder_id, quad in occluder_quads
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": GROUND_TRUTH_KIND,
        "scene_id": scene.name,
        "description": scene.description,
        "seed": seed,
        "generator": {
            "name": "liveblocks.synth",
            "package_version": __version__,
            "layout_version": context.layout_version,
            "card_dictionary_version": context.card_dictionary.version,
            "aruco_dictionary": context.card_dictionary.aruco_dictionary,
        },
        "image": {
            "file": f"{scene.name}.png",
            "mode": "L",
            "width": scene.width,
            "height": scene.height,
            "pixel_sha256": hashlib.sha256(pixels).hexdigest(),
        },
        "camera": {
            "fx": _r(scene.camera.fx),
            "fy": _r(scene.camera.fy),
            "cx": _r(scene.camera.cx),
            "cy": _r(scene.camera.cy),
        },
        "card_layout": layout.name,
        "reading_direction": scene.reading_direction,
        "background_shade": scene.background_shade,
        "lighting": scene.lighting.to_dict(),
        "jitter": {
            "x_mm": _r(scene.jitter.x_mm),
            "y_mm": _r(scene.jitter.y_mm),
            "z_mm": _r(scene.jitter.z_mm),
            "yaw_deg": _r(scene.jitter.yaw_deg),
            "pitch_deg": _r(scene.jitter.pitch_deg),
            "roll_deg": _r(scene.jitter.roll_deg),
        },
        "cards": cards,
        "occluders": occluders,
        "diagnostics": [],
    }


def dump_ground_truth(ground_truth: dict[str, Any]) -> str:
    """真值的规范文本形式：缩进 2、键序按插入顺序、行尾 ``\\n``。"""
    return json.dumps(ground_truth, ensure_ascii=False, indent=2) + "\n"


def generate_all(*, seed: int, context: SynthContext | None = None) -> list[GeneratedScene]:
    """按字母顺序生成全部场景预设。"""
    ctx = context or SynthContext.load()
    return [generate_scene(name, seed=seed, context=ctx) for name in sorted(ctx.presets)]


def write_scene(out_dir: Path, scene: GeneratedScene) -> tuple[Path, Path]:
    """把一张合成图与真值写进目录，返回 ``(image_path, ground_truth_path)``。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / scene.image_name
    truth_path = out_dir / scene.ground_truth_name
    image_path.write_bytes(scene.image_png)
    truth_path.write_text(scene.ground_truth_json, encoding="utf-8")
    return image_path, truth_path


def index_document(scenes: list[GeneratedScene], *, seed: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "SyntheticSceneIndex",
        "seed": seed,
        "generator": {"name": "liveblocks.synth", "package_version": __version__},
        "scenes": [
            {
                "scene_id": scene.name,
                "image": scene.image_name,
                "ground_truth": scene.ground_truth_name,
                "width": scene.width,
                "height": scene.height,
                "cards": len(scene.ground_truth["cards"]),
                "pixel_sha256": scene.pixel_sha256,
            }
            for scene in scenes
        ],
    }


def write_index(out_dir: Path, scenes: list[GeneratedScene], *, seed: int) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "index.json"
    path.write_text(
        json.dumps(index_document(scenes, seed=seed), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


@dataclass(frozen=True)
class VerifyRow:
    name: str
    seed: int
    image_ok: bool
    ground_truth_ok: bool
    pixel_ok: bool
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.image_ok and self.ground_truth_ok and self.pixel_ok


def verify_directory(
    fixture_dir: Path, *, seeds: dict[str, int] | None = None, context: SynthContext | None = None
) -> list[VerifyRow]:
    """重生成 fixtures 目录里的每个场景并与入库文件逐项比对。"""
    from .png import decode_gray

    ctx = context or SynthContext.load()
    fixture_dir = Path(fixture_dir)
    rows: list[VerifyRow] = []
    index_path = fixture_dir / "index.json"
    index_seed = None
    if index_path.is_file():
        index_seed = json.loads(index_path.read_text(encoding="utf-8")).get("seed")
    for name in sorted(ctx.presets):
        truth_path = fixture_dir / f"{name}.json"
        image_path = fixture_dir / f"{name}.png"
        note = ""
        if not truth_path.is_file() or not image_path.is_file():
            rows.append(
                VerifyRow(name, 0, False, False, False, note="缺少 fixtures 文件")
            )
            continue
        stored_truth_text = truth_path.read_text(encoding="utf-8")
        stored_truth = json.loads(stored_truth_text)
        seed = (seeds or {}).get(name, stored_truth.get("seed", index_seed))
        if seed is None:
            rows.append(VerifyRow(name, 0, False, False, False, note="无法确定种子"))
            continue
        generated = generate_scene(name, seed=int(seed), context=ctx)

        truth_ok = generated.ground_truth_json == stored_truth_text
        image_ok = generated.image_png == image_path.read_bytes()
        stored_w, stored_h, stored_pixels = decode_gray(image_path.read_bytes())
        pixel_ok = (stored_w, stored_h) == (generated.width, generated.height) and (
            stored_pixels == generated.pixels
        )
        if not truth_ok:
            note = "真值 JSON 与重生成结果不一致"
        elif not image_ok and pixel_ok:
            note = "PNG 字节不同但像素一致（zlib 版本差异）"
        elif not image_ok and not pixel_ok:
            note = "PNG 字节与像素都不一致"
        rows.append(
            VerifyRow(
                name=name,
                seed=int(seed),
                image_ok=image_ok,
                ground_truth_ok=truth_ok,
                pixel_ok=pixel_ok,
                note=note,
            )
        )
    return rows
