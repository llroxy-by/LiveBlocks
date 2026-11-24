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
