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
