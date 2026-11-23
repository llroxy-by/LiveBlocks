"""LB-002 合成卡片生成器的公共测试夹具。

场景生成本身有点慢（纯 Python 光栅化，单场景约 1 秒），所以按 session 缓存：
整个测试会话只按固定种子生成一遍全部场景，各测试文件共享。
"""

from __future__ import annotations

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
