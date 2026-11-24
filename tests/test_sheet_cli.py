"""LB-032 命令行：``cardsheet render / verify / stencil / spec`` 的出口、退出码与 golden。

golden 是入库的 ``fixtures/sheet/card-sheet.{png,pdf,json}``（见 ``fixtures/README.md`` §12）：
同一条命令重跑必须**逐字节**相同，所以它是本层最强的确定性证据。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from liveblocks import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
SHEET_DIR = REPO_ROOT / "fixtures" / "sheet"


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    out_dir = tmp_path_factory.mktemp("sheet-cli")
    png = out_dir / "card-sheet.png"
    pdf = out_dir / "card-sheet.pdf"
    manifest = out_dir / "card-sheet.json"
    code = cli.main(
        [
            "cardsheet",
            "render",
            "--out",
            str(png),
            "--pdf",
            str(pdf),
            "--manifest",
            str(manifest),
        ]
    )
    assert code == 0
    return png, pdf, manifest


def test_render_writes_png_pdf_and_manifest(rendered) -> None:
    png, pdf, manifest = rendered
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.read_bytes().startswith(b"%PDF-1.4")
    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert document["kind"] == "CardSheet"
    assert len(document["cards"]) == 16


def test_render_matches_the_committed_golden(rendered) -> None:
    png, pdf, manifest = rendered
    assert png.read_bytes() == (SHEET_DIR / "card-sheet.png").read_bytes()
    assert pdf.read_bytes() == (SHEET_DIR / "card-sheet.pdf").read_bytes()
    assert manifest.read_text(encoding="utf-8") == (SHEET_DIR / "card-sheet.json").read_text(
        encoding="utf-8"
    )


def test_golden_files_exist_and_are_not_empty() -> None:
    for name in ("card-sheet.png", "card-sheet.pdf", "card-sheet.json"):
        path = SHEET_DIR / name
        assert path.is_file(), f"缺少入库 golden {path}"
        assert path.stat().st_size > 0


def test_render_reports_what_it_cannot_claim(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["cardsheet", "render", "--out", str(tmp_path / "sheet.png")])
    assert code == 0
    out = capsys.readouterr().out
    assert "待人工验证" in out
    assert "静区" in out


def test_render_rejects_bad_layout_parameters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["cardsheet", "render", "--out", str(tmp_path / "sheet.png"), "--gutter-mm", "1"]
    )
    assert code == 1
    assert "静区" in capsys.readouterr().err


def test_render_accepts_a_card_subset(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    code = cli.main(
        [
            "cardsheet",
            "render",
            "--out",
            str(tmp_path / "sheet.png"),
            "--manifest",
            str(manifest),
            "--cards",
            "C01,O01",
        ]
    )
    assert code == 0
    document = json.loads(manifest.read_text(encoding="utf-8"))
    assert [card["card_id"] for card in document["cards"]] == ["C01", "O01"]


def test_verify_prints_the_acceptance_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_out = tmp_path / "verification.json"
    out_dir = tmp_path / "artifacts"
    code = cli.main(
        ["cardsheet", "verify", "--out-dir", str(out_dir), "--json", str(json_out)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "验收对照：" in out
    assert out.count("[通过]") == 5
    assert "待人工验证" in out
    assert (out_dir / "card-sheet.png").is_file()
    assert (out_dir / "card-sheet.pdf").is_file()
    document = json.loads(json_out.read_text(encoding="utf-8"))
    assert document["kind"] == "CardSheetVerification"
    assert len(document["cards"]) == 16


def test_stencil_prints_grids(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["cardsheet", "stencil"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("marker_id=") == 16

    target = tmp_path / "stencil.txt"
    code = cli.main(["cardsheet", "stencil", "--card", "C01", "--out", str(target)])
    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert text.count("marker_id=") == 1
    assert "C01" in text


def test_spec_lists_numbers_with_their_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_out = tmp_path / "numbers.json"
    code = cli.main(["cardsheet", "spec", "--json", str(json_out)])
    assert code == 0
    out = capsys.readouterr().out
    assert "待实测" in out
    assert "card_dictionary.json#cards" in out
    numbers = json.loads(json_out.read_text(encoding="utf-8"))
    assert sum(1 for entry in numbers if not entry["measured"]) >= 6
    assert any(entry["kind"] == "quiet_zone" for entry in numbers)


def test_spec_checks_the_document_against_the_implementation(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["cardsheet", "spec", "--doc", str(REPO_ROOT / "docs" / "CARD-SPEC.md")])
    assert code == 0
    assert "缺口 0 项" in capsys.readouterr().out


def test_spec_reportedly_fails_on_a_tampered_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    doc = tmp_path / "CARD-SPEC.md"
    doc.write_text(
        (REPO_ROOT / "docs" / "CARD-SPEC.md").read_text(encoding="utf-8").replace(
            "| 卡片数量 | 16 张 |", "| 卡片数量 | 99 张 |"
        ),
        encoding="utf-8",
    )
    code = cli.main(["cardsheet", "spec", "--doc", str(doc)])
    assert code == 1
    err = capsys.readouterr().err
    assert "LB-S007" in err


def test_module_entry_point_matches_the_console_script(tmp_path: Path) -> None:
    """``python -m liveblocks cardsheet …`` 与 ``liveblocks cardsheet …`` 是同一条路径。"""
    out = tmp_path / "sheet.png"
    result = subprocess.run(
        [sys.executable, "-m", "liveblocks", "cardsheet", "render", "--out", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "已写出标记页" in result.stdout
    assert out.read_bytes() == (SHEET_DIR / "card-sheet.png").read_bytes()
