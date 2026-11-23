"""场景规格（版式 + 位姿 + 遮挡 + 光照）的解析与不可变表示。

``data/synth_layouts.json`` 是「版式定义」的唯一来源：

- ``card_layouts``：卡面怎么画（见 :mod:`liveblocks.synth.texture`）。
- ``scene_presets``：一个场景摆几张卡、每张卡位姿、有没有遮挡物、光照与位姿抖动量。

``jitter`` 是「任意角度 / 任意间距」的来源：固定种子下按均匀分布给每张卡加扰动，
扰动后的位姿才是真值里记录的位姿（真值永远描述「实际画出来的东西」）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..data import read_json
from .dictionaries import ArucoDictionary
from .geometry import Camera, Pose
from .texture import CardLayout


@dataclass(frozen=True)
class Placement:
    """场景里一张卡的初始摆放（相机坐标毫米）。"""

    card_id: str
    x_mm: float
    y_mm: float
    z_mm: float
    yaw_deg: float
    pitch_deg: float
    roll_deg: float
    z_order: int

    def pose(self) -> Pose:
        return Pose(
            x_mm=self.x_mm,
            y_mm=self.y_mm,
            z_mm=self.z_mm,
            yaw_deg=self.yaw_deg,
            pitch_deg=self.pitch_deg,
            roll_deg=self.roll_deg,
        )


@dataclass(frozen=True)
class Occluder:
    """遮挡物：相机坐标毫米下的凸四边形，按 ``z_order`` 盖在卡片之上。"""

    occluder_id: str
    corners_mm: tuple[tuple[float, float], ...]
    shade: int
    z_order: int
    z_mm: float
    kind: str = "quad"

    def validate(self) -> None:
        if self.kind != "quad":
            raise ValueError(f"{self.occluder_id}: 暂只支持 quad，实际 {self.kind!r}")
        if len(self.corners_mm) != 4:
            raise ValueError(f"{self.occluder_id}: 需要 4 个角点，实际 {len(self.corners_mm)}")
        if not 0 <= self.shade <= 255:
            raise ValueError(f"{self.occluder_id}: shade 必须在 0..255")
        if self.z_mm <= 0:
            raise ValueError(f"{self.occluder_id}: z_mm 必须为正")


@dataclass(frozen=True)
class Lighting:
    gain: float
    noise_sigma: float
    vignette: float
    blur_radius_px: int = 0

    def to_dict(self) -> dict[str, float]:
        return {
            "gain": round(self.gain, 3),
            "noise_sigma": round(self.noise_sigma, 3),
            "vignette": round(self.vignette, 3),
            "blur_radius_px": self.blur_radius_px,
        }


@dataclass(frozen=True)
class Jitter:
    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @property
    def is_zero(self) -> bool:
        return not any(
            (self.x_mm, self.y_mm, self.z_mm, self.yaw_deg, self.pitch_deg, self.roll_deg)
        )


@dataclass(frozen=True)
class SceneSpec:
    name: str
    description: str
    width: int
    height: int
    background_shade: int
    card_layout: str
    camera: Camera
    reading_direction: str
    placements: tuple[Placement, ...]
    occluders: tuple[Occluder, ...]
    lighting: Lighting
    jitter: Jitter

    def validate(self, layouts: dict[str, CardLayout], aruco: ArucoDictionary) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"{self.name}: 画面尺寸必须为正")
        if not 0 <= self.background_shade <= 255:
            raise ValueError(f"{self.name}: background_shade 必须在 0..255")
        if self.card_layout not in layouts:
            raise ValueError(f"{self.name}: 未知卡面版式 {self.card_layout!r}")
        layouts[self.card_layout].validate(aruco)
        if not self.placements:
            raise ValueError(f"{self.name}: 至少需要一张卡")
        seen: set[str] = set()
        for placement in self.placements:
            if placement.card_id in seen:
                raise ValueError(f"{self.name}: card_id {placement.card_id} 在同一场景里重复")
            seen.add(placement.card_id)
            if placement.z_mm <= 0:
                raise ValueError(f"{self.name}/{placement.card_id}: z_mm 必须为正")
        for occluder in self.occluders:
            occluder.validate()

    def pose_list(self) -> list[Pose]:
        return [placement.pose() for placement in self.placements]


def _layout_from_spec(name: str, spec: dict[str, Any]) -> CardLayout:
    return CardLayout(
        name=name,
        image_width_mm=float(spec["image_width_mm"]),
        image_height_mm=float(spec["image_height_mm"]),
        pixels_per_mm=int(spec["pixels_per_mm"]),
        corner_radius_mm=float(spec["corner_radius_mm"]),
        border_mm=float(spec["border_mm"]),
        border_shade=int(spec["border_shade"]),
        background_shade=int(spec["background_shade"]),
        marker_size_mm=float(spec["marker_size_mm"]),
        marker_center_mm=(float(spec["marker_center_mm"][0]), float(spec["marker_center_mm"][1])),
        marker_quiet_zone_mm=float(spec["marker_quiet_zone_mm"]),
        label_anchor_mm=(float(spec["label_anchor_mm"][0]), float(spec["label_anchor_mm"][1])),
        label_max_height_mm=float(spec["label_max_height_mm"]),
        label_shade=int(spec["label_shade"]),
        label_max_width_mm=float(spec["label_max_width_mm"]),
    )


def load_layouts() -> tuple[dict[str, CardLayout], dict[str, SceneSpec], str]:
    """读取版式定义，返回 ``(card_layouts, scene_presets, layout_version)``。"""
    raw = read_json("synth_layouts.json")
    layouts = {
        name: _layout_from_spec(name, spec) for name, spec in raw["card_layouts"].items()
    }
    presets: dict[str, SceneSpec] = {}
    for name, spec in raw["scene_presets"].items():
        camera = spec["camera"]
        presets[name] = SceneSpec(
            name=name,
            description=str(spec.get("description", "")),
            width=int(spec["width"]),
            height=int(spec["height"]),
            background_shade=int(spec["background_shade"]),
            card_layout=str(spec["card_layout"]),
            camera=Camera(
                fx=float(camera["fx"]),
                fy=float(camera["fy"]),
                cx=float(camera["cx"]),
                cy=float(camera["cy"]),
            ),
            reading_direction=str(spec.get("reading_direction", "ltr_ttb")),
            placements=tuple(
                Placement(
                    card_id=str(item["card_id"]),
                    x_mm=float(item["x_mm"]),
                    y_mm=float(item["y_mm"]),
                    z_mm=float(item["z_mm"]),
                    yaw_deg=float(item["yaw_deg"]),
                    pitch_deg=float(item["pitch_deg"]),
                    roll_deg=float(item["roll_deg"]),
                    z_order=int(item["z_order"]),
                )
                for item in spec["placements"]
            ),
            occluders=tuple(
                Occluder(
                    occluder_id=str(item["occluder_id"]),
                    corners_mm=tuple((float(point[0]), float(point[1])) for point in item["corners_mm"]),
                    shade=int(item["shade"]),
                    z_order=int(item["z_order"]),
                    z_mm=float(item["z_mm"]),
                    kind=str(item.get("kind", "quad")),
                )
                for item in spec.get("occluders", [])
            ),
            lighting=Lighting(
                gain=float(spec["lighting"]["gain"]),
                noise_sigma=float(spec["lighting"]["noise_sigma"]),
                vignette=float(spec["lighting"]["vignette"]),
                blur_radius_px=int(spec["lighting"].get("blur_radius_px", 0)),
            ),
            jitter=Jitter(
                x_mm=float(spec["jitter"]["x_mm"]),
                y_mm=float(spec["jitter"]["y_mm"]),
                z_mm=float(spec["jitter"]["z_mm"]),
                yaw_deg=float(spec["jitter"]["yaw_deg"]),
                pitch_deg=float(spec["jitter"]["pitch_deg"]),
                roll_deg=float(spec["jitter"]["roll_deg"]),
            ),
        )
    return layouts, presets, str(raw["layout_version"])


def scene_names() -> list[str]:
    """场景预设名（排序后返回；顺序即 fixtures 的生成顺序）。"""
    _, presets, _ = load_layouts()
    return sorted(presets)


def apply_jitter(spec: SceneSpec, rng) -> SceneSpec:
    """按 ``spec.jitter`` 给每张卡加扰动，返回新的 :class:`SceneSpec`。"""
    jitter = spec.jitter
    if jitter.is_zero:
        return spec

    def offset(amplitude: float) -> float:
        if amplitude <= 0.0:
            return 0.0
        return rng.uniform(-amplitude, amplitude)

    placements = tuple(
        replace(
            placement,
            x_mm=placement.x_mm + offset(jitter.x_mm),
            y_mm=placement.y_mm + offset(jitter.y_mm),
            z_mm=placement.z_mm + offset(jitter.z_mm),
            yaw_deg=placement.yaw_deg + offset(jitter.yaw_deg),
            pitch_deg=placement.pitch_deg + offset(jitter.pitch_deg),
            roll_deg=placement.roll_deg + offset(jitter.roll_deg),
        )
        for placement in spec.placements
    )
    return replace(spec, placements=placements)
