"""`docs/HW.md` 与实现是否一致（含反向验证），以及文档 / 夹具 / 命令的互相引用。

正向：``hw_document_gaps`` 对入库文档返回空列表。
反向：删掉一条命令行 / 一个错误码 / 改掉 MAGIC 或载荷上限、去掉「模拟器证据」或「待人工验证」，
必须报缺口——否则「文档与实现一致」只是假通过（与 LB-004～LB-007 同一做法）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from liveblocks.hw import (
    COMMANDS,
    DOC_PATH,
    EVENTS,
    HW_DIAGNOSTICS,
    HW_PROTOCOL_VERSION,
    MAGIC,
    MAX_PAYLOAD,
    hw_document_gaps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HW_DOC = REPO_ROOT / DOC_PATH


def doc_text() -> str:
    return HW_DOC.read_text(encoding="utf-8")


def test_doc_exists_and_is_linked_everywhere() -> None:
    assert HW_DOC.is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    decisions = (REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    exec_doc = (REPO_ROOT / "docs" / "EXEC.md").read_text(encoding="utf-8")
    protocol = (REPO_ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
    fixtures_readme = (REPO_ROOT / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "docs/HW.md" in readme
    assert "docs/HW.md" in decisions
    assert "docs/HW.md" in progress
    assert "docs/HW.md" in exec_doc
    assert "fixtures/hw" in fixtures_readme
    assert "LB-H001" in protocol  # §9 的错误码表里登记了 hw 区间


def test_doc_is_a_draft_with_a_version() -> None:
    text = doc_text()
    assert "草案" in text
    assert HW_PROTOCOL_VERSION in text


def test_doc_has_no_gaps() -> None:
    assert hw_document_gaps(doc_text()) == []


def test_doc_documents_every_command_and_event() -> None:
    text = doc_text()
    for spec in COMMANDS:
        row = next((line for line in text.splitlines() if line.strip().startswith(f"| `0x{spec.code:02X}`")), None)
        assert row is not None, f"命令表缺少 0x{spec.code:02X}"
        assert spec.name in row
        assert list(spec.body_keys)[0] in row
    for spec in EVENTS:
        row = next((line for line in text.splitlines() if line.strip().startswith(f"| `0x{spec.code:02X}`")), None)
        assert row is not None, f"事件表缺少 0x{spec.code:02X}"
        assert spec.name in row


def test_doc_documents_every_error_code_with_title_and_severity() -> None:
    text = doc_text()
    for entry in HW_DIAGNOSTICS:
        row = next((line for line in text.splitlines() if line.strip().startswith(f"| `{entry.code}`")), None)
        assert row is not None, f"缺少错误码行 {entry.code}"
        assert entry.title in row
        assert entry.severity in row


def test_doc_states_the_evidence_boundary_explicitly() -> None:
    """「模拟器证据 ≠ 硬件证据」这条必须写死在文档里，不能只写在 PR 描述里。"""
    text = doc_text()
    assert "模拟器证据" in text
    assert "不是硬件证据" in text
    assert "LB-020" in text
    heading = text.index("## 8. 待人工验证")
    section = text[heading:]
    assert "待人工验证" in section
    for tag in ("HW-1", "HW-2", "HW-3", "HW-4"):
        assert tag in section, f"待人工验证表缺少 {tag}"
    assert "怎么验" in section and "通过标准" in section


def test_doc_does_not_claim_hardware_was_verified() -> None:
    """禁句检查：文档里不许出现「真机已验证 / 硬件验证通过」这类说法。"""
    text = doc_text()
    for banned in ("真机验证通过", "硬件验证通过", "已实测通过", "灯效已验证"):
        assert banned not in text, f"文档不应出现 {banned!r}"


# -- 反向验证 ---------------------------------------------------------------
def _drop_row(text: str, prefix: str) -> str:
    lines = text.splitlines()
    target = next(line for line in lines if line.strip().startswith(prefix))
    return text.replace(target + "\n", "", 1)


def test_missing_command_row_is_reported() -> None:
    gaps = hw_document_gaps(_drop_row(doc_text(), "| `0x03`"))
    assert any("STEP" in gap for gap in gaps)


def test_missing_event_row_is_reported() -> None:
    gaps = hw_document_gaps(_drop_row(doc_text(), "| `0x83`"))
    assert any("STEP_OK" in gap for gap in gaps)


def test_missing_error_code_row_is_reported() -> None:
    gaps = hw_document_gaps(_drop_row(doc_text(), "| `LB-H002`"))
    assert any("LB-H002" in gap for gap in gaps)


def test_wrong_error_title_is_reported() -> None:
    broken = doc_text().replace("| `LB-H001` | 串口断连 |", "| `LB-H001` | 断线 |", 1)
    gaps = hw_document_gaps(broken)
    assert any("LB-H001" in gap for gap in gaps)


def test_wrong_magic_is_reported() -> None:
    upper = MAGIC.hex(" ").upper()
    lower = MAGIC.hex(" ")
    broken = doc_text().replace(upper, "00 00").replace(lower, "00 00")
    assert upper not in broken.upper()
    gaps = hw_document_gaps(broken)
    assert any("MAGIC" in gap for gap in gaps)


def test_missing_payload_limit_is_reported() -> None:
    gaps = hw_document_gaps(doc_text().replace(str(MAX_PAYLOAD), "1024"))
    assert any("载荷上限" in gap for gap in gaps)


@pytest.mark.parametrize(
    "word",
    ["模拟器证据", "待人工验证", "Transport", "PtyTransport", "SerialTransport", '"hw"', "0x1021"],
)
def test_missing_required_statement_is_reported(word: str) -> None:
    gaps = hw_document_gaps(doc_text().replace(word, ""))
    assert gaps, f"删掉 {word!r} 后应当报缺口"


# -- 夹具与命令路径 ---------------------------------------------------------
def test_fixture_manifest_and_readme_agree() -> None:
    index = json.loads((REPO_ROOT / "fixtures" / "hw" / "index.json").read_text(encoding="utf-8"))
    assert index["hw_protocol_version"] == HW_PROTOCOL_VERSION
    fixtures_readme = (REPO_ROOT / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "## 13. `hw/`" in fixtures_readme
    for entry in index["fixtures"]:
        assert f"`{entry['name']}`" in fixtures_readme


def test_command_paths_in_the_doc_exist() -> None:
    """文档命令块里的 ``--ir`` 路径与夹具路径都必须真实存在（AGENTS.md 的证据要求）。"""
    text = doc_text()
    paths = re.findall(r"--ir (fixtures/[\w./-]+)", text)
    assert paths
    for relative in sorted(set(paths)):
        assert (REPO_ROOT / relative).exists(), f"文档里的路径不存在：{relative}"
    for relative in re.findall(r"`(fixtures/hw/[\w./-]*)`", text):
        assert (REPO_ROOT / relative).exists(), f"文档里的路径不存在：{relative}"


def test_readme_and_progress_mention_the_hw_commands() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "liveblocks hw run" in readme
    assert "liveblocks hw parity" in readme
    assert "liveblocks hw unplug-test" in readme
    assert re.search(r"LB-033", progress) is not None


def test_readme_hw_section_paths_exist() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 硬件后端协议层（LB-033）" in readme
    section = readme[readme.index("## 硬件后端协议层（LB-033）") : readme.index("## 文档入口")]
    paths = re.findall(r"(fixtures/[\w./-]+)", section)
    assert paths
    for relative in sorted(set(paths)):
        assert (REPO_ROOT / relative).exists(), f"README 里的路径不存在：{relative}"
