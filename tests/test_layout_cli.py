"""``liveblocks layout`` 子命令：arrange / from-image / report。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.layout import validate_layout

REPO_ROOT = Path(__file__).resolve().parents[1]
LAYOUT_FIXTURES = REPO_ROOT / "fixtures" / "layouts"
SCENES_DIR = REPO_ROOT / "fixtures" / "scenes"


def test_layout_arrange_prints_layout_json(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        ["layout", "arrange", "--candidates", str(LAYOUT_FIXTURES / "multi-row-cards.json")]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_layout(document)
    assert document["kind"] == "Layout"
    assert document["frame_id"] == "f-multi-row"
    assert len(document["rows"]) == 2


def test_layout_arrange_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "nested" / "layout.json"
    exit_code = cli.main(
        [
            "layout",
            "arrange",
            "--candidates",
            str(LAYOUT_FIXTURES / "duplicates.json"),
            "--out",
            str(out),
        ]
    )
    assert exit_code == 0
    assert "Layout" in capsys.readouterr().out
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_layout(document)
    assert [slot["candidate_id"] for slot in document["rows"][0]["slots"]] == ["c-0", "c-2", "c-3"]
    assert document["diagnostics"][0]["code"] == "LB-L002"


def test_layout_arrange_honours_reading_direction(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "layout",
            "arrange",
            "--candidates",
            str(LAYOUT_FIXTURES / "multi-row-cards.json"),
            "--reading-direction",
            "rtl_ttb",
        ]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["reading_direction"] == "rtl_ttb"
    assert [slot["candidate_id"] for slot in document["rows"][0]["slots"]] == ["c-0", "c-1", "c-2"]


def test_layout_arrange_rejects_non_candidate_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema_version": "0.0.1", "kind": "Layout"}), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(["layout", "arrange", "--candidates", str(wrong)])
    assert "CardCandidateList" in str(error.value.value if hasattr(error.value, "value") else error.value)


def test_layout_arrange_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["layout", "arrange", "--candidates", str(tmp_path / "nope.json")])


def test_layout_from_image_goes_recognition_to_layout(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "layout",
            "from-image",
            "--image",
            str(SCENES_DIR / "multi-row.png"),
            "--card-layout",
            "card-40x40",
        ]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_layout(document)
    assert len(document["rows"]) == 2
    assert sum(len(row["slots"]) for row in document["rows"]) == 6


def test_layout_report_prints_acceptance(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["layout", "report"])
    assert exit_code == 0
    text = capsys.readouterr().out
    assert "验收对照" in text
    assert "[通过] 两行样例被正确分成两行" in text
    assert "LB-L001" in text and "LB-L002" in text


def test_layout_report_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.json"
    exit_code = cli.main(["layout", "report", "--json", str(out)])
    assert exit_code == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["reading_direction"] == "ltr_ttb"
    assert all(item["ok"] for item in document["acceptance"])
    for fixture in document["fixtures"]:
        validate_layout(fixture["layout"])


def test_layout_help_lists_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    # argparse 的 --help 通过 SystemExit(0) 退出（与 `liveblocks synth --help` 一致）
    with pytest.raises(SystemExit) as error:
        cli.main(["layout", "--help"])
    assert error.value.code == 0
    text = capsys.readouterr().out
    for keyword in ("arrange", "from-image", "report"):
        assert keyword in text
