"""合成标定数据集预设（``data/calib_presets.json`` 的解析视图）。

一个预设描述「一整套可复现的标定实验」：板定义、真值内参/畸变、图像尺寸、光照、
若干标定视图位姿、一个静态场景的重复采集设置，以及验收阈值。所有随机量都由
``seed + 下标`` 决定，因此同一预设 + 同一份代码必然给出同一批图像与同一组数字。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..data import read_json
from ..synth.calibtarget import CalibTarget
from ..synth.geometry import Pose
from ..synth.lens import LensModel

SCHEMA_VERSION = "0.0.1"


@dataclass(frozen=True)
class Lighting:
    gain: float = 1.0
    vignette: float = 0.0
    noise_sigma: float = 0.0
    blur_radius_px: int = 0

    def to_dict(self) -> dict[str, float]:
        return {
            "gain": round(self.gain, 3),
            "vignette": round(self.vignette, 3),
            "noise_sigma": round(self.noise_sigma, 3),
            "blur_radius_px": self.blur_radius_px,
        }


@dataclass(frozen=True)
class StaticScene:
    """重复性测量用的静态场景：同一个位姿拍 ``repeats`` 次。"""

    pose: Pose
    repeats: int
    seed: int


@dataclass(frozen=True)
class Thresholds:
    """验收阈值。``report`` 用它判定通过/失败，并把数字原样打印。"""

    reprojection_rms_px: float
    repeatability_mean_px: float
    repeatability_max_px: float
    focal_relative_error: float
    principal_point_px: float

    def to_dict(self) -> dict[str, float]:
        return {
            "reprojection_rms_px": self.reprojection_rms_px,
            "repeatability_mean_px": self.repeatability_mean_px,
            "repeatability_max_px": self.repeatability_max_px,
            "focal_relative_error": self.focal_relative_error,
            "principal_point_px": self.principal_point_px,
        }


@dataclass(frozen=True)
class CalibPreset:
    """一个合成标定实验预设。"""

    name: str
    description: str
    target: CalibTarget
    width: int
    height: int
    seed: int
    true_lens: LensModel
    lighting: Lighting
    views: tuple[Pose, ...]
    static: StaticScene
    thresholds: Thresholds
    version: str = SCHEMA_VERSION

    def view_seed(self, index: int) -> int:
        """第 ``index`` 个标定视图的噪声种子（固定偏移，改视图数不影响前面的种子）。"""
        return self.seed + 1000 + index

    @property
    def static_seed(self) -> int:
        return self.static.seed


def _pose(document: dict[str, Any]) -> Pose:
    return Pose(
        x_mm=float(document.get("x_mm", 0.0)),
        y_mm=float(document.get("y_mm", 0.0)),
        z_mm=float(document.get("z_mm", 300.0)),
        yaw_deg=float(document.get("yaw_deg", 0.0)),
        pitch_deg=float(document.get("pitch_deg", 0.0)),
        roll_deg=float(document.get("roll_deg", 0.0)),
    )


def load_presets() -> tuple[dict[str, CalibPreset], str]:
    """读取 ``calib_presets.json``，返回 ``(预设字典, 版本号)``。"""
    raw = read_json("calib_presets.json")
    version = str(raw.get("preset_version", SCHEMA_VERSION))
    targets = {
        name: CalibTarget.from_dict(document) for name, document in raw["targets"].items()
    }
    presets: dict[str, CalibPreset] = {}
    for name, document in raw["presets"].items():
        target_name = str(document["target"])
        if target_name not in targets:
            raise KeyError(f"预设 {name!r} 引用了不存在的标定板 {target_name!r}")
        lens = document["true_lens"]
        lighting = document.get("lighting", {})
        thresholds = document["thresholds"]
        static = document["static"]
        presets[name] = CalibPreset(
            name=name,
            description=str(document.get("description", "")),
            target=targets[target_name],
            width=int(document["width"]),
            height=int(document["height"]),
            seed=int(document["seed"]),
            true_lens=LensModel(
                fx=float(lens["fx"]),
                fy=float(lens["fy"]),
                cx=float(lens["cx"]),
                cy=float(lens["cy"]),
                k1=float(lens.get("k1", 0.0)),
                k2=float(lens.get("k2", 0.0)),
                p1=float(lens.get("p1", 0.0)),
                p2=float(lens.get("p2", 0.0)),
            ),
            lighting=Lighting(
                gain=float(lighting.get("gain", 1.0)),
                vignette=float(lighting.get("vignette", 0.0)),
                noise_sigma=float(lighting.get("noise_sigma", 0.0)),
                blur_radius_px=int(lighting.get("blur_radius_px", 0)),
            ),
            views=tuple(_pose(item) for item in document["views"]),
            static=StaticScene(
                pose=_pose(static["pose"]),
                repeats=int(static["repeats"]),
                seed=int(static["seed"]),
            ),
            thresholds=Thresholds(
                reprojection_rms_px=float(thresholds["reprojection_rms_px"]),
                repeatability_mean_px=float(thresholds["repeatability_mean_px"]),
                repeatability_max_px=float(thresholds["repeatability_max_px"]),
                focal_relative_error=float(thresholds["focal_relative_error"]),
                principal_point_px=float(thresholds["principal_point_px"]),
            ),
            version=version,
        )
    return presets, version


def preset_names() -> list[str]:
    presets, _version = load_presets()
    return sorted(presets)


def load_preset(name: str) -> CalibPreset:
    presets, _version = load_presets()
    try:
        return presets[name]
    except KeyError:
        raise KeyError(
            f"没有标定预设 {name!r}；可用：{', '.join(sorted(presets))}"
        ) from None
