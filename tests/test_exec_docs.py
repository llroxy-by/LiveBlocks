"""``docs/EXEC.md`` 与实现是否一致（含反向验证），以及文档/夹具索引的互相引用。

正向：``runtime_document_gaps`` 对入库文档返回空列表。
反向：删掉一行语义表 / 一个诊断码 / 改掉时钟换算 / 去掉一个枚举取值后必须报缺口——否则
「文档与实现一致」只是假通过（与 LB-004/LB-005/LB-006 同一做法）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from liveblocks.runtime import (
    BACKENDS,
    CLOCK_MS_PER_TICK,
    DIAGNOSTICS_BY_CODE,
    DOC_PATH,
    EXEC_VERSION,
    FACINGS,
    OP_SEMANTICS,
    PHASES,
    RESULT_STATUSES,
    runtime_document_gaps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_DOC = REPO_ROOT / DOC_PATH


def doc_text() -> str:
    return EXEC_DOC.read_text(encoding="utf-8")


def test_exec_doc_exists_and_is_linked() -> None:
    assert EXEC_DOC.is_file()
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    decisions = (REPO_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    fixtures_readme = (REPO_ROOT / "fixtures" / "README.md").read_text(encoding="utf-8")
    assert "docs/EXEC.md" in readme
    assert "docs/EXEC.md" in decisions
    assert "docs/EXEC.md" in progress
    assert "fixtures/exec" in fixtures_readme


def test_exec_doc_is_a_draft_with_a_version() -> None:
    text = doc_text()
    assert "草案" in text
    assert EXEC_VERSION in text


def test_exec_doc_has_no_gaps() -> None:
    assert runtime_document_gaps(doc_text()) == []


def test_exec_doc_documents_every_op_semantic() -> None:
    text = doc_text()
    for semantic in OP_SEMANTICS:
        assert f"`{semantic.op}`" in text
        assert f"`{semantic.effect}`" in text


def test_exec_doc_documents_every_diagnostic_code() -> None:
    text = doc_text()
    for code, (title, _meaning, severity) in DIAGNOSTICS_BY_CODE.items():
        row = next(
            (line for line in text.splitlines() if line.strip().startswith(f"| `{code}`")), None
        )
        assert row is not None, f"缺少诊断码行 {code}"
        assert title in row
        assert severity in row


def test_exec_doc_documents_enums_and_limits() -> None:
    text = doc_text()
    for status in RESULT_STATUSES:
        assert f"`{status}`" in text
    for phase in PHASES:
        assert f"`{phase}`" in text
    for backend in BACKENDS:
        assert f"`{backend}`" in text
    for facing in FACINGS:
        assert f"`{facing}`" in text
    assert f"{CLOCK_MS_PER_TICK:g} ms/tick" in text
    assert "`max_steps`" in text and "`timeout_ms`" in text


# -- 反向验证：文档一旦与实现脱节，必须报缺口 ---------------------------------
def test_missing_semantic_row_is_reported() -> None:
    text = doc_text()
    lines = text.splitlines()
    target = next(line for line in lines if line.strip().startswith("| `pick`"))
    gaps = runtime_document_gaps(text.replace(target + "\n", "", 1))
    assert any("pick" in gap for gap in gaps)


def test_wrong_ticks_in_semantic_row_is_reported() -> None:
    text = doc_text()
    broken = text.replace("| `move-forward` | 2 |", "| `move-forward` | 3 |", 1)
    gaps = runtime_document_gaps(broken)
    assert any("move-forward" in gap for gap in gaps)


def test_wrong_effect_in_semantic_row_is_reported() -> None:
    text = doc_text()
    broken = text.replace("`pos += 朝向 × distance`", "`pos 变了`", 1)
    gaps = runtime_document_gaps(broken)
    assert any("move-forward" in gap for gap in gaps)


def test_missing_diagnostic_row_is_reported() -> None:
    text = doc_text()
    target = next(line for line in text.splitlines() if line.strip().startswith("| `LB-R004`"))
    gaps = runtime_document_gaps(text.replace(target + "\n", "", 1))
    assert any("LB-R004" in gap for gap in gaps)


def test_missing_code_title_is_reported() -> None:
    text = doc_text()
    broken = text.replace("| `LB-R002` | 步数上限 |", "| `LB-R002` | - |", 1)
    gaps = runtime_document_gaps(broken)
    assert any("LB-R002" in gap for gap in gaps)


def test_wrong_clock_constant_is_reported() -> None:
    text = doc_text()
    gaps = runtime_document_gaps(text.replace(f"{CLOCK_MS_PER_TICK:g} ms/tick", "5 ms/tick"))
    assert any("ms/tick" in gap for gap in gaps)


@pytest.mark.parametrize("word", ["`limit_exceeded`", "`feedback`", "`hw`", "`south`", "`holding`"])
def test_missing_enum_or_state_field_is_reported(word: str) -> None:
    text = doc_text()
    gaps = runtime_document_gaps(text.replace(word, ""))
    assert gaps, f"删掉 {word} 后应当报缺口"


# -- 夹具清单与文档的互相引用 -------------------------------------------------
def test_fixture_manifest_points_at_existing_goldens_and_docs() -> None:
    fixture_index = json.loads(
        (REPO_ROOT / "fixtures" / "exec" / "index.json").read_text(encoding="utf-8")
    )
    assert fixture_index["exec_version"] == EXEC_VERSION
    for entry in fixture_index["fixtures"]:
        assert (REPO_ROOT / "fixtures" / "exec" / entry["events"]).is_file()
        assert (REPO_ROOT / "fixtures" / "exec" / entry["state"]).is_file()
    doc = doc_text()
    for entry in fixture_index["fixtures"]:
        # 文档的命令示例与清单里的夹具名字能对上（至少有一个通用示例）
        assert "fixtures/exec" in doc


def test_readme_and_progress_mention_the_exec_commands() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    progress = (REPO_ROOT / "docs" / "PROGRESS.md").read_text(encoding="utf-8")
    assert "liveblocks exec run" in readme
    assert "liveblocks exec report" in readme
    assert re.search(r"LB-007", progress) is not None


def test_command_paths_in_the_doc_exist() -> None:
    """文档 §9 的命令块里每个 ``--ir`` / ``--dir`` 路径都必须真实存在（AGENTS.md 的证据要求）。"""
    text = doc_text()
    paths = re.findall(r"--(?:ir|dir) (fixtures/[\w./-]+)", text)
    assert paths, "docs/EXEC.md 的命令示例里没有 --ir/--dir 路径"
    for relative in sorted(set(paths)):
        assert (REPO_ROOT / relative).exists(), f"文档里的路径不存在：{relative}"


def test_command_paths_in_the_readme_exist() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## 模拟执行（LB-007）") : readme.index("## 文档入口")]
    paths = re.findall(r"--(?:ir|dir) (fixtures/[\w./-]+)", section)
    assert paths
    for relative in sorted(set(paths)):
        assert (REPO_ROOT / relative).exists(), f"README 里的路径不存在：{relative}"
