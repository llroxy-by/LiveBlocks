"""``docs/UI.md`` 与实现的一致性（文档缺口 + 反向验证）。

与 LB-006/LB-007 的 ``*_docs`` 测试同一思路：入库文档必须能被机器核对，而且要**反证**
核对函数不是永真式——删掉一行文档后必须真的报缺口。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from liveblocks.ui.docs import DOC_PATH, REQUIRED_COMMANDS, REQUIRED_TERMS, ui_document_gaps
from liveblocks.ui.protocol import (
    ISSUE_CODES,
    ISSUE_TITLES,
    LAYER_ORDER,
    LAYER_TITLES,
    TIMING_LABELS,
    TIMING_LAYERS,
    UI_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / DOC_PATH


def test_document_exists_and_has_no_gaps() -> None:
    assert DOC.is_file()
    assert ui_document_gaps() == ()


def test_document_mentions_version_and_scope() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"`ui_version` {UI_VERSION}" in text
    assert "不做产品级 UI" in text
    assert "只读" in text


@pytest.mark.parametrize("layer", LAYER_ORDER)
def test_document_lists_each_panel(layer: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"| `{layer}` | {LAYER_TITLES[layer]} |" in text


@pytest.mark.parametrize("code", ISSUE_CODES)
def test_document_lists_each_issue_code(code: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"| `{code}` | {ISSUE_TITLES[code]} |" in text


@pytest.mark.parametrize("key", TIMING_LAYERS)
def test_document_lists_each_timing_key(key: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"| `{key}` | {TIMING_LABELS[key]} |" in text


# --- 反向验证：删一行就必须报缺口 ---------------------------------------------


def _copy_doc(tmp_path: Path) -> Path:
    target = tmp_path / "UI.md"
    shutil.copyfile(DOC, target)
    return target


def test_removing_an_issue_code_row_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    row = f"| `LB-U008` | {ISSUE_TITLES['LB-U008']} |"
    assert row in text
    path.write_text(text.replace(row, ""), encoding="utf-8")
    gaps = ui_document_gaps(path)
    assert any("LB-U008" in gap for gap in gaps)


def test_removing_a_panel_row_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    row = f"| `ast` | {LAYER_TITLES['ast']} |"
    path.write_text(text.replace(row, ""), encoding="utf-8")
    assert any("ast" in gap for gap in ui_document_gaps(path))


def test_removing_a_timing_row_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    row = f"| `parser` | {TIMING_LABELS['parser']} |"
    path.write_text(text.replace(row, ""), encoding="utf-8")
    assert any("parser" in gap for gap in ui_document_gaps(path))


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_removing_a_command_is_detected(tmp_path: Path, command: str) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert command in text
    path.write_text(text.replace(command, "liveblocks ui no-such-command"), encoding="utf-8")
    assert any("命令" in gap for gap in ui_document_gaps(path))


@pytest.mark.parametrize("term", REQUIRED_TERMS)
def test_removing_a_required_term_is_detected(tmp_path: Path, term: str) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert term in text
    path.write_text(text.replace(term, "（被删）"), encoding="utf-8")
    assert any(term in gap or "概念词" in gap for gap in ui_document_gaps(path))


def test_pointing_at_a_missing_path_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "\n见 `fixtures/runs/not-here.jsonl`。\n", encoding="utf-8")
    gaps = ui_document_gaps(path)
    assert any("not-here.jsonl" in gap for gap in gaps)


def test_missing_document_is_reported(tmp_path: Path) -> None:
    gaps = ui_document_gaps(tmp_path / "nope.md")
    assert gaps and "缺少文档" in gaps[0]
