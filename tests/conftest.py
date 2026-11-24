"""LB-002 合成卡片生成器的公共测试夹具。

场景生成本身有点慢（纯 Python 光栅化，单场景约 1 秒），所以按 session 缓存：
整个测试会话只按固定种子生成一遍全部场景，各测试文件共享。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from liveblocks.cli import DEFAULT_SEED
from liveblocks.synth import GeneratedScene, SynthContext
from liveblocks.synth.generator import generate_all

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"
SCENES_DIR = FIXTURES_DIR / "scenes"

#: Issue #7 要求覆盖的 5 类样例（+ 多卡多行）。
REQUIRED_CLASSES = ("normal", "slight-rotation", "overlap", "clipped", "low-light")


@pytest.fixture(scope="session")
def context() -> SynthContext:
    return SynthContext.load()


@pytest.fixture(scope="session")
def all_scenes(context: SynthContext) -> dict[str, GeneratedScene]:
    scenes = generate_all(seed=DEFAULT_SEED, context=context)
    return {scene.name: scene for scene in scenes}


@pytest.fixture(scope="session")
def scenes_dir() -> Path:
    return SCENES_DIR


#: LB-003 识别层的 fixture 版式（采集配置）来自每个场景的真值 ``card_layout``：
#: 这是「用哪种卡纸」的采集参数，不是识别答案。
def scene_layout(scene: str) -> str:
    import json

    truth = json.loads((SCENES_DIR / f"{scene}.json").read_text(encoding="utf-8"))
    return str(truth["card_layout"])


@pytest.fixture(scope="session")
def recognized() -> dict[str, tuple[object, dict]]:
    """``{场景: (识别结果, 真值)}``，整个会话只跑一遍（单场景约 0.4 秒）。"""
    import json

    from liveblocks.vision import CardRegistry, recognize_path

    results: dict[str, tuple[object, dict]] = {}
    for truth_path in sorted(SCENES_DIR.glob("*.json")):
        if truth_path.name == "index.json":
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if truth.get("kind") != "SyntheticCardScene":
            continue
        name = str(truth["scene_id"])
        registry = CardRegistry.load(capture_layout=str(truth["card_layout"]))
        result = recognize_path(SCENES_DIR / f"{name}.png", registry=registry)
        results[name] = (result, truth)
    return results


# --- LB-009 端到端回归的公共夹具（全链路单次约 1 秒，按 session 缓存） ---------------

#: LB-009 的黄金基准目录。
E2E_DIR = FIXTURES_DIR / "e2e"


@pytest.fixture(scope="session")
def e2e_out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """运行记录的落盘目录（不入库：``timings_ms`` 是实测墙钟，每跑一次都不一样）。"""
    return tmp_path_factory.mktemp("e2e-out")


@pytest.fixture(scope="session")
def e2e_normal_run(e2e_out_dir: Path, context: SynthContext):
    """``normal`` 场景真跑一遍全链路的结果（记录已落盘）。"""
    from liveblocks.e2e import run_chain

    return run_chain("normal", out_dir=e2e_out_dir, context=context)


# --- LB-030 采集台的公共夹具 ---------------------------------------------------------

#: 采集台的固定时钟（落盘目录名与 JSON 时间戳因此是确定的）。
SERVE_CAM_NOW = datetime(2026, 11, 24, 5, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def serve_cam_scene_pngs() -> dict[str, bytes]:
    """``{场景名: PNG 字节}``：直接读入库的 ``fixtures/scenes/*.png``（与 ``recognize detect`` 同一批图）。"""
    pngs: dict[str, bytes] = {}
    for path in sorted(SCENES_DIR.glob("*.png")):
        pngs[path.stem] = path.read_bytes()
    return pngs


@pytest.fixture()
def serve_cam(tmp_path: Path):
    """一台「进程内」的采集台：固定时钟 + 确定性采集 id，采集根目录落在 tmp_path。"""
    import itertools
    from types import SimpleNamespace

    from liveblocks.serve_cam import ServeCamApp, ServeCamConfig, TestClient
    from liveblocks.serve_cam.captures import _default_capture_id  # noqa: PLC2701 - 复用时间戳形状

    counter = itertools.count(1)

    def fixed_id_factory(moment) -> str:
        stamp = _default_capture_id(moment).rsplit("-", 1)[0]
        return f"{stamp}-{next(counter):06x}"

    config = ServeCamConfig(
        live_dir=tmp_path / "live",
        clock=lambda: SERVE_CAM_NOW,
        capture_id_factory=fixed_id_factory,
    )
    app = ServeCamApp(config)
    return SimpleNamespace(app=app, client=TestClient(app), live_dir=tmp_path / "live", config=config)


# --- LB-031 标定的公共夹具 -----------------------------------------------------------

#: 标定文件 golden 目录（``fixtures/calib/*.json``，见 ``fixtures/README.md`` §11）。
CALIB_DIR = FIXTURES_DIR / "calib"

#: 测试用的最小预设参数（240×180、6×5 棋盘、5 个视图）：一次跑完约 2.5 秒，
#: 让「采集 → 检角点 → 标定 → 重复性」这条链路在普通测试里也能真跑，而不是打桩。
SMALL_PRESET_KWARGS = {
    "name": "small",
    "description": "测试用小预设（240×180、6×5 棋盘、5 个视图）",
    "target": dict(
        name="checkerboard-6x5-20mm",
        kind="checkerboard",
        squares_x=6,
        squares_y=5,
        square_mm=20.0,
        margin_mm=15.0,
    ),
    "width": 240,
    "height": 180,
    "seed": 777,
    "true_lens": dict(
        fx=230.0, fy=231.0, cx=119.5, cy=89.5, k1=-0.2, k2=0.05, p1=0.004, p2=-0.003
    ),
    "lighting": dict(gain=1.0, vignette=0.08, noise_sigma=1.5, blur_radius_px=0),
    "views": [
        dict(x_mm=0, y_mm=0, z_mm=210, yaw_deg=3, pitch_deg=16, roll_deg=-5),
        dict(x_mm=-24, y_mm=12, z_mm=195, yaw_deg=-12, pitch_deg=-14, roll_deg=8),
        dict(x_mm=22, y_mm=-14, z_mm=225, yaw_deg=14, pitch_deg=11, roll_deg=4),
        dict(x_mm=-12, y_mm=-16, z_mm=190, yaw_deg=10, pitch_deg=18, roll_deg=-9),
        dict(x_mm=14, y_mm=16, z_mm=215, yaw_deg=-10, pitch_deg=-16, roll_deg=7),
    ],
    "static": dict(
        pose=dict(x_mm=3, y_mm=-2, z_mm=190, yaw_deg=6, pitch_deg=15, roll_deg=-5),
        repeats=2,
        seed=888,
    ),
    "thresholds": dict(
        reprojection_rms_px=0.5,
        repeatability_mean_px=0.5,
        repeatability_max_px=2.0,
        focal_relative_error=0.03,
        principal_point_px=8.0,
    ),
}


def make_small_preset():
    """构造测试用的小预设（``run_preset`` 接受任意 :class:`CalibPreset`，不一定要在数据文件里）。"""
    from liveblocks.calib.presets import CalibPreset, Lighting, StaticScene, Thresholds
    from liveblocks.synth.calibtarget import CalibTarget
    from liveblocks.synth.geometry import Pose
    from liveblocks.synth.lens import LensModel

    kwargs = SMALL_PRESET_KWARGS
    return CalibPreset(
        name=kwargs["name"],
        description=kwargs["description"],
        target=CalibTarget.from_dict(kwargs["target"] | {"name": kwargs["target"]["name"]}),
        width=kwargs["width"],
        height=kwargs["height"],
        seed=kwargs["seed"],
        true_lens=LensModel(**kwargs["true_lens"]),
        lighting=Lighting(**kwargs["lighting"]),
        views=tuple(Pose(**view) for view in kwargs["views"]),
        static=StaticScene(
            pose=Pose(**kwargs["static"]["pose"]),
            repeats=kwargs["static"]["repeats"],
            seed=kwargs["static"]["seed"],
        ),
        thresholds=Thresholds(**kwargs["thresholds"]),
    )


@pytest.fixture(scope="session")
def calib_small_preset():
    return make_small_preset()


@pytest.fixture(scope="session")
def calib_small_outcome(calib_small_preset):
    """小预设真跑一遍（约 2.5 秒），多个测试文件共享同一份结果。"""
    from liveblocks.calib.report import run_preset

    return run_preset(calib_small_preset)


@pytest.fixture(scope="session")
def calib_dir() -> Path:
    return CALIB_DIR


# --- LB-034 固件骨架的公共夹具 --------------------------------------------------------

#: 主机侧编译出的固件工具（整个会话只编一次：固件源码是同一份，编一次就够）。
@pytest.fixture(scope="session")
def firmware_host_tool(tmp_path_factory: pytest.TempPathFactory):
    """用主机 gcc 编译 `firmware/main/` 里的可移植源码（不烧写、不需要 ESP-IDF）。

    编译失败一律 **fail 而不是 skip**：主机 gcc 是普通环境就该有的东西，
    编不过说明固件源码有缺陷（验收里的「源码能编译」）。
    """
    from liveblocks.firmware.hosttool import HostToolError, build_host_tool

    try:
        return build_host_tool(tmp_path_factory.mktemp("firmware-host"))
    except HostToolError as error:  # pragma: no cover - 只在固件源码坏了时触发
        pytest.fail(f"固件源码在主机上编译不过（实现缺陷，不是环境问题）：{error}")
