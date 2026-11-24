"""LB-031 文档核对（``docs/CALIB.md`` ↔ 实现）。

「文档写了、实现没做」和「实现有码、文档没写」都要能被抓出来：

- 诊断码表：``codes.CODES`` 的每个码与标题都必须在 ``docs/CALIB.md`` §6 出现（含**反向验证**）；
- 文档里的示例 JSON 必须能被 ``CalibrationFile.from_dict`` 接受（示例不是摆设）；
- 文档里点名的命令必须真能解析（``cli.build_parser``）；
- 「待人工验证」条目必须与 ``report.HUMAN_VERIFICATION`` 一致。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.calib.codes import CODES, document_gaps
from liveblocks.calib.format import CalibrationFile
from liveblocks.calib.report import HUMAN_VERIFICATION

REPO_ROOT = Path(__file__).resolve().parents[1]
CALIB_DOC = REPO_ROOT / "docs" / "CALIB.md"


@pytest.fixture(scope="module")
def calib_markdown() -> str:
    return CALIB_DOC.read_text(encoding="utf-8")


def test_doc_exists_and_is_a_draft(calib_markdown: str) -> None:
    assert CALIB_DOC.is_file()
    assert "草案" in calib_markdown
    assert "v0.0.1" in calib_markdown
    assert "待人工验证" in calib_markdown


def test_every_diagnostic_code_is_documented(calib_markdown: str) -> None:
    assert document_gaps(calib_markdown) == []


@pytest.mark.parametrize("code", sorted(CODES))
def test_removing_a_code_or_title_is_detected(calib_markdown: str, code: str) -> None:
    """反向验证：删掉码或标题后，文档核对必须报缺口（否则核对就是假通过）。"""
    without_code = calib_markdown.replace(code, "LB-B0XX")
    assert any(code in gap for gap in document_gaps(without_code))
    title = CODES[code]
    without_title = calib_markdown.replace(title, "改过的标题")
    assert any(title in gap for gap in document_gaps(without_title))


def test_example_json_in_the_doc_is_accepted(calib_markdown: str) -> None:
    blocks = re.findall(r"```json\n(.*?)```", calib_markdown, flags=re.DOTALL)
    assert blocks, "docs/CALIB.md 里没有 json 代码块"
    documents = [json.loads(block) for block in blocks]
    calibration_documents = [item for item in documents if item.get("kind") == "Calibration"]
    assert calibration_documents, "示例里没有 kind = Calibration 的标定文件"
    for document in calibration_documents:
        calibration = CalibrationFile.from_dict(document)
        assert calibration.image_count >= 3
        assert calibration.created_at.endswith("Z")


def test_documented_commands_are_real(calib_markdown: str) -> None:
    """文档里出现的 ``liveblocks calibrate …`` 命令必须都能被解析器接受。"""
    commands = set(re.findall(r"liveblocks calibrate ([a-z-]+)", calib_markdown))
    assert {"report", "run", "corners", "undistort", "frame", "validate", "targets"} <= commands
    parser = cli.build_parser()
    for subcommand in sorted(commands):
        args = [subcommand]
        if subcommand == "report":
            args += ["--preset", "all"]
        elif subcommand == "run":
            args += ["--dir", "fixtures/calib", "--target", "checkerboard-9x6-25mm"]
        elif subcommand == "corners":
            args += ["--image", "fixtures/scenes/normal.png", "--target", "checkerboard-9x6-25mm"]
        elif subcommand == "undistort":
            args += ["--calib", "fixtures/calib/checkerboard.json", "--image", "x.png", "--out", "y.png"]
        elif subcommand == "frame":
            args += ["--calib", "fixtures/calib/checkerboard.json", "--image", "x.png"]
        elif subcommand == "validate":
            args += ["--calib", "fixtures/calib/checkerboard.json"]
        parsed = parser.parse_args(["calibrate", *args])
        assert getattr(parsed, "handler", None) is not None


def test_human_verification_items_are_in_the_doc(calib_markdown: str) -> None:
    assert len(HUMAN_VERIFICATION) >= 3
    for item, _why, steps in HUMAN_VERIFICATION:
        assert item in calib_markdown
        assert steps.split("；")[0][:16] in calib_markdown


def test_doc_states_that_real_camera_numbers_are_out_of_scope(calib_markdown: str) -> None:
    assert "不在本文件结论内" in calib_markdown or "不在本命令内" in calib_markdown
    assert "真实摄像头" in calib_markdown
    assert "代理不得代填" in calib_markdown


def test_doc_lists_the_limitations_we_actually_have(calib_markdown: str) -> None:
    """已知限制必须写进文档：方板歧义、k2 弱约束、重复性不会因校正变好。"""
    for needle in ("方板歧义", "弱约束", "重复性不会因为校正而变好"):
        assert needle in calib_markdown
