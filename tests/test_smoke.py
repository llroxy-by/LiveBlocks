"""骨架冒烟测试：只验证工程骨架本身可用，不含任何业务断言。

覆盖的验收点（对应 Issue #4）：
- 包可导入，版本号形状正确；
- 命令行入口 ``python -m liveblocks --version`` 退出码为 0；
- ``src/`` ``tests/`` ``fixtures/`` ``scripts/`` 四个目录存在；
- fixtures 目录真的被测试读取（不是空目录占位）；
- README 里写出的检查命令与实际脚本一致。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import liveblocks
from liveblocks import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", liveblocks.__version__), liveblocks.__version__


def test_required_directories_exist() -> None:
    missing = [
        name
        for name in ("src", "tests", "fixtures", "scripts")
        if not (REPO_ROOT / name).is_dir()
    ]
    assert missing == [], f"缺少目录: {missing}"


def test_fixture_is_readable_and_matches_package_version() -> None:
    data = json.loads((FIXTURES_DIR / "skeleton.json").read_text(encoding="utf-8"))
    assert data["kind"] == "skeleton"
    assert data["fixture_version"] == liveblocks.__version__


def test_cli_main_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"liveblocks {liveblocks.__version__}"


def test_module_entrypoint_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "liveblocks", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("liveblocks ")


def test_readme_commands_point_to_existing_scripts() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "bash scripts/check.sh" in readme
    assert (REPO_ROOT / "scripts" / "check.sh").is_file()
