"""LB-002 合成卡片图像生成器（虚拟卡片）。

按卡片字典（``data/card_dictionary.json`` + ``data/aruco_dictionaries.json``）与版式
定义（``data/synth_layouts.json``）生成 ArUco 卡片图像：单卡、多卡、任意角度与间距、
光照噪声、遮挡与裁切，每张图都配套一份真值 JSON（卡片 id、角点、位姿、可见比例）。

本模块**不做识别**，也不评估识别效果（见 ``docs/ROADMAP.md`` LB-002 与 Issue #7）。

LB-031 起，本模块还提供「镜头畸变模型」（:class:`LensModel`）与「标定板 + 带畸变的拍摄图」
（:class:`CalibTarget` / :func:`render_capture`）：真值内参与畸变已知的合成标定数据，
标定算法（``liveblocks.calib``）与校正（``undistort_raster``）共用同一套数学。

典型用法::

    from liveblocks.synth import generate_scene, write_scene

    scene = generate_scene("normal", seed=20261123)
    write_scene(Path("fixtures/scenes"), scene)
"""

from __future__ import annotations

from .dictionaries import (
    ArucoDictionary,
    CardDictionary,
    CardEntry,
    load_aruco_dictionaries,
    load_card_dictionary,
)
from .generator import (
    GeneratedScene,
    SynthContext,
    generate_all,
    generate_scene,
    write_index,
    write_scene,
)
from .calibtarget import CalibTarget, CalibrationCapture, render_capture
from .geometry import Camera, Pose
from .lens import LensModel, distort_raster, undistort_raster
from .spec import Lighting, Occluder, Placement, SceneSpec, load_layouts, scene_names
from .texture import CardLayout, CardTexture, render_card_texture

__all__ = [
    "ArucoDictionary",
    "CalibTarget",
    "CalibrationCapture",
    "Camera",
    "CardDictionary",
    "CardEntry",
    "CardLayout",
    "CardTexture",
    "GeneratedScene",
    "LensModel",
    "Lighting",
    "Occluder",
    "Placement",
    "Pose",
    "SceneSpec",
    "SynthContext",
    "generate_all",
    "generate_scene",
    "load_aruco_dictionaries",
    "load_card_dictionary",
    "load_layouts",
    "distort_raster",
    "render_capture",
    "render_card_texture",
    "scene_names",
    "undistort_raster",
    "write_index",
    "write_scene",
]
