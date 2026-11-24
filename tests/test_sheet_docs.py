"""LB-032 文档核对（``docs/CARD-SPEC.md`` ↔ 实现）。

三种「假通过」都要能被抓出来：

- 文档写了、实现没做（数字对不上）→ ``test_number_gaps_detects_a_changed_value``；
- 实现有码、文档没写（``LB-S0xx`` / 章节缺失）→ ``test_every_code_is_documented`` 的正反两向；
- 数字来路不明（文档自己发明一行，或正文冒出没有出处的「数字 + 单位」）→
  ``test_orphan_rows_are_detected`` / ``test_prose_number_without_source_is_detected``。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from liveblocks.sheet.codes import CODES, document_gaps
from liveblocks.sheet.docs import (
    REQUIRED_SECTIONS,
    TRACE_SECTION,
    card_spec_document_gaps,
    command_gaps,
    default_spec,
    number_gaps,
    orphan_gaps,
    prose_gaps,
    section_gaps,
)
from liveblocks.sheet.spec import HUMAN_VERIFICATION, all_numbers, unmeasured_items

REPO_ROOT = Path(__file__).resolve().parents[1]
CARD_SPEC = REPO_ROOT / "docs" / "CARD-SPEC.md"


@pytest.fixture(scope="module")
def markdown() -> str:
    return CARD_SPEC.read_text(encoding="utf-8")


def test_doc_exists_and_is_marked_as_a_draft(markdown: str) -> None:
    assert CARD_SPEC.is_file()
    assert "草案" in markdown
    assert "v0.0.1" in markdown
    assert "待实测" in markdown
    assert "待人工验证" in markdown


def test_no_gaps_at_all(markdown: str) -> None:
    assert card_spec_document_gaps(markdown) == []


def test_every_code_is_documented(markdown: str) -> None:
    assert document_gaps(markdown) == []


@pytest.mark.parametrize("code", sorted(CODES))
def test_removing_a_code_or_title_is_detected(markdown: str, code: str) -> None:
    """反向验证：删掉码或标题后必须报缺口（否则核对就是假通过）。"""
    without_code = markdown.replace(code, "LB-S0XX")
    assert any(code in gap for gap in document_gaps(without_code))
    title = CODES[code]
    without_title = markdown.replace(title, "改过的标题")
    assert any(title in gap for gap in document_gaps(without_title))


@pytest.mark.parametrize("heading", REQUIRED_SECTIONS)
def test_removing_a_section_is_detected(markdown: str, heading: str) -> None:
    assert section_gaps(markdown) == []
    without = markdown.replace(heading, "## 换个标题")
    assert any(heading in gap for gap in section_gaps(without))


def test_every_number_is_traceable(markdown: str) -> None:
    assert number_gaps(markdown) == []
    # 每一项都必须真的出现在文档里（标签 + 数值 + 单位），且待实测项带标注
    for entry in all_numbers(default_spec()):
        line = [line for line in markdown.splitlines() if entry.label in line and line.startswith("|")]
        assert line, entry.label
        if entry.measured:
            assert any(entry.value_text in row for row in line), entry.label
        else:
            assert any("待实测" in row for row in line), entry.label


def test_number_gaps_detects_a_changed_value(markdown: str) -> None:
    tampered = markdown.replace("| 卡片数量 | 16 张 |", "| 卡片数量 | 99 张 |")
    assert any("卡片数量" in gap for gap in number_gaps(tampered))


def test_number_gaps_detects_a_missing_unmeasured_marker(markdown: str) -> None:
    tampered = markdown.replace(
        "| 页边距 | 8 mm | 设计默认值 liveblocks.sheet.spec.DEFAULT_MARGIN_MM（与打印机的不可打印边距无关） | **待实测** |",
        "| 页边距 | 8 mm | 设计默认值 liveblocks.sheet.spec.DEFAULT_MARGIN_MM（与打印机的不可打印边距无关） | 已验证 |",
    )
    assert tampered != markdown
    assert any("页边距" in gap for gap in number_gaps(tampered))


def test_orphan_rows_are_detected(markdown: str) -> None:
    """文档自己发明一行数字（实现里查不到）必须被抓出来。"""
    assert orphan_gaps(markdown) == []
    tampered = markdown.replace(
        "| 卡片数量 | 16 张 |", "| 卡片厚度 | 0.3 mm | 我拍脑袋定的 | **待实测** |\n| 卡片数量 | 16 张 |"
    )
    assert any("卡片厚度" in gap for gap in orphan_gaps(tampered))


def test_prose_number_without_source_is_detected(markdown: str) -> None:
    assert prose_gaps(markdown) == []
    tampered = markdown.replace("按网格把 `#` 涂满", "按 30 mm 的网格把 `#` 涂满", 1)
    assert tampered != markdown
    assert any("30 mm" in gap for gap in prose_gaps(tampered))


def test_commands_in_code_blocks_really_parse(markdown: str) -> None:
    assert command_gaps(markdown) == []
    tampered = markdown.replace(
        "liveblocks cardsheet verify", "liveblocks cardsheet verify-typo", 1
    )
    assert any("verify-typo" in gap for gap in command_gaps(tampered))


@pytest.mark.parametrize(("item", "steps"), HUMAN_VERIFICATION)
def test_every_human_verification_item_is_in_the_doc(markdown: str, item: str, steps: str) -> None:
    assert item in markdown
    # 步骤要点也要在文档里（防止文档与代码各说一套）
    for marker in re.findall(r"[①②③④]", steps):
        assert marker in markdown


def test_doc_lists_all_unmeasured_items(markdown: str) -> None:
    for entry in unmeasured_items():
        assert entry.label in markdown, entry.label


def test_doc_does_not_claim_measured_print_results(markdown: str) -> None:
    """不许出现「已实测/已验证」的打印结论：这些项必须是待实测。"""
    for forbidden in ("实测通过", "已实测", "打印验证通过"):
        assert forbidden not in markdown
    assert markdown.count("**待实测**") >= len(unmeasured_items())


def test_trace_section_is_a_markdown_table(markdown: str) -> None:
    start = markdown.index(TRACE_SECTION)
    section = markdown[start:]
    rows = [line for line in section.splitlines() if line.startswith("|")]
    assert len(rows) >= len(all_numbers(default_spec()))
    assert rows[1].startswith("| --- |")


def test_number_json_matches_the_doc_table(tmp_path: Path) -> None:
    """``cardsheet spec --json`` 的每一项都能在文档表格里找到同名标签。"""
    from liveblocks import cli

    target = tmp_path / "numbers.json"
    assert cli.main(["cardsheet", "spec", "--json", str(target)]) == 0
    numbers = json.loads(target.read_text(encoding="utf-8"))
    markdown = CARD_SPEC.read_text(encoding="utf-8")
    for entry in numbers:
        assert entry["label"] in markdown
