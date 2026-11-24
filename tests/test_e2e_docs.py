"""``docs/E2E.md`` 与实现的一致性（文档缺口 + 反向验证）。

与 LB-006/LB-007/LB-008 的 ``*_docs`` 测试同一思路：入库文档必须能被机器核对，而且要**反证**
核对函数不是永真式——删掉一行（或改掉一个时间上界）后必须真的报缺口。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from liveblocks.e2e.docs import DOC_PATH, REQUIRED_COMMANDS, REQUIRED_TERMS, e2e_document_gaps
from liveblocks.e2e.protocol import (
    CODE_TITLES,
    E2E_CODES,
    E2E_VERSION,
    HEADER_REQUIRED_FIELDS,
    RECORD_BOUND_MS,
    STAGE_LABELS,
    STAGES,
    TIME_BOUND_MS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / DOC_PATH


def test_document_exists_and_has_no_gaps() -> None:
    assert DOC.is_file()
    assert e2e_document_gaps() == ()


def test_document_is_a_draft_with_a_version() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "草案" in text
    assert f"`e2e_version`" in text and E2E_VERSION in text
    assert "不接真实摄像头" in text
    assert "不是性能结论" in text


@pytest.mark.parametrize("stage", STAGES)
def test_document_lists_each_stage_row(stage: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"| `{stage}` | {STAGE_LABELS[stage]} |" in text


@pytest.mark.parametrize("code", E2E_CODES)
def test_document_lists_each_code_row(code: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"| `{code}` | {CODE_TITLES[code]} |" in text


@pytest.mark.parametrize("field", HEADER_REQUIRED_FIELDS)
def test_document_lists_each_header_field(field: str) -> None:
    assert f"`{field}`" in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_document_lists_each_command(command: str) -> None:
    assert command in DOC.read_text(encoding="utf-8")


@pytest.mark.parametrize("term", REQUIRED_TERMS)
def test_document_lists_each_term(term: str) -> None:
    assert term in DOC.read_text(encoding="utf-8")


def test_document_states_the_fixed_time_bounds() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert f"{TIME_BOUND_MS:g} ms" in text
    assert f"{RECORD_BOUND_MS:g} ms" in text


def test_document_says_it_does_not_depend_on_the_lb008_script() -> None:
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## 8.", 1)[1].split("## 9.", 1)[0]
    assert "scripts/build_run_records.py" in section
    assert "不依赖" in section


# --- 反向验证：删一行 / 改一个数就必须报缺口 ----------------------------------


def _copy_doc(tmp_path: Path) -> Path:
    target = tmp_path / "E2E.md"
    shutil.copyfile(DOC, target)
    return target


def test_removing_a_code_row_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    row = f"| `LB-E010` | {CODE_TITLES['LB-E010']} |"
    text = path.read_text(encoding="utf-8")
    assert row in text
    path.write_text(text.replace(row, ""), encoding="utf-8")
    assert any("LB-E010" in gap for gap in e2e_document_gaps(path))


def test_removing_a_stage_row_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    row = f"| `parser` | {STAGE_LABELS['parser']} |"
    text = path.read_text(encoding="utf-8")
    assert row in text
    path.write_text(text.replace(row, ""), encoding="utf-8")
    assert any("阶段行" in gap for gap in e2e_document_gaps(path))


def test_changing_the_time_bound_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(f"{RECORD_BOUND_MS:g} ms", "9999 ms"), encoding="utf-8")
    gaps = e2e_document_gaps(path)
    assert any("时间上界" in gap and str(int(RECORD_BOUND_MS)) in gap for gap in gaps)


def test_removing_a_header_field_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("`generation`", "`gen`"), encoding="utf-8")
    assert any("generation" in gap for gap in e2e_document_gaps(path))


def test_removing_a_command_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("liveblocks e2e regression --dir", "liveblocks e2e regression"), encoding="utf-8")
    assert any("命令" in gap for gap in e2e_document_gaps(path))


def test_removing_the_draft_marker_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("草案", "定稿"), encoding="utf-8")
    assert any("草案" in gap for gap in e2e_document_gaps(path))


def test_pointing_at_a_missing_path_is_detected(tmp_path: Path) -> None:
    path = _copy_doc(tmp_path)
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "\n参考 `fixtures/e2e/does-not-exist.json`。\n", encoding="utf-8")
    assert any("路径不存在" in gap for gap in e2e_document_gaps(path))


def test_missing_document_is_reported(tmp_path: Path) -> None:
    gaps = e2e_document_gaps(tmp_path / "nope.md")
    assert gaps and "缺少文档" in gaps[0]
