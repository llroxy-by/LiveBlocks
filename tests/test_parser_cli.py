"""``liveblocks parse`` 子命令：ast / report。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.parser import validate_ast

REPO_ROOT = Path(__file__).resolve().parents[1]
AST_DIR = REPO_ROOT / "fixtures" / "ast"


def write_layout(tmp_path: Path, document: dict, name: str = "layout.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def slot(index: int, candidate_id: str, card_face: str) -> dict:
    return {
        "slot_index": index,
        "candidate_id": candidate_id,
        "card_face": card_face,
        "orientation_quadrant": 0,
        "x_px": 100 + 80 * index,
        "y_px": 200,
        "confidence": 0.9,
    }


def layout(*rows: list[dict], frame_id: str = "f-cli") -> dict:
    return {
        "schema_version": "0.0.1",
        "kind": "Layout",
        "frame_id": frame_id,
        "reading_direction": "ltr_ttb",
        "rows": [{"row_index": index, "slots": slots} for index, slots in enumerate(rows)],
        "diagnostics": [],
    }


def test_parse_ast_prints_section5_document(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["parse", "ast", "--layout", str(AST_DIR / "valid-basic.json")])
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    validate_ast(document)
    assert document["kind"] == "Ast"
    assert document["grammar_version"] == "0.0.1"
    assert document["frame_id"] == "f-valid-basic"
    assert [command["name"] for command in document["root"]["body"]] == [
        "move-forward",
        "turn-left",
        "jump",
    ]
    assert document["diagnostics"] == []


def test_parse_ast_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "nested" / "ast.json"
    exit_code = cli.main(
        ["parse", "ast", "--layout", str(AST_DIR / "valid-empty.json"), "--out", str(out)]
    )
    assert exit_code == 0
    printed = capsys.readouterr().out
    assert "Ast" in printed and "语句 0" in printed
    document = json.loads(out.read_text(encoding="utf-8"))
    validate_ast(document)
    assert document["root"]["body"] == []


def test_parse_ast_prints_diagnostics_but_exits_zero_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        ["parse", "ast", "--layout", str(AST_DIR / "invalid-number-first.json")]
    )
    captured = capsys.readouterr()
    assert exit_code == 0  # 默认：诊断随 Ast 输出，进程成功
    document = json.loads(captured.out)
    validate_ast(document)
    assert [item["code"] for item in document["diagnostics"]] == ["LB-P001"]
    assert "LB-P001" in captured.err  # 摘要走 stderr，不污染 stdout 的 JSON


def test_parse_ast_fail_on_error_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        [
            "parse",
            "ast",
            "--layout",
            str(AST_DIR / "invalid-control-flow.json"),
            "--fail-on-error",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert [item["code"] for item in json.loads(captured.out)["diagnostics"]] == [
        "LB-P004",
        "LB-P004",
    ]


def test_parse_ast_fail_on_error_is_still_zero_for_valid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        ["parse", "ast", "--layout", str(AST_DIR / "from-overlap-cards.json"), "--fail-on-error"]
    )
    assert exit_code == 0
    assert capsys.readouterr().err == ""


def test_parse_ast_rejects_non_layout_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_layout(tmp_path, {"kind": "CardCandidateList", "cards": []})
    with pytest.raises(SystemExit) as error:
        cli.main(["parse", "ast", "--layout", str(path)])
    assert "CardCandidateList" in str(error.value.code)
    assert capsys.readouterr().out == ""


def test_parse_ast_rejects_malformed_layout(tmp_path: Path) -> None:
    path = write_layout(tmp_path, {"schema_version": "0.0.1", "kind": "Layout", "frame_id": "f"})
    with pytest.raises(SystemExit) as error:
        cli.main(["parse", "ast", "--layout", str(path)])
    assert "Layout" in str(error.value.code)


def test_parse_ast_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["parse", "ast", "--layout", str(tmp_path / "nope.json")])
    assert "找不到" in str(error.value.code)


def test_parse_ast_rejects_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(["parse", "ast", "--layout", str(path)])
    assert "JSON" in str(error.value.code)


def test_parse_report_prints_acceptance_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["parse", "report", "--dir", str(AST_DIR)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "验收对照" in out
    assert "语法夹具目录" in out
    assert out.count("[通过]") == 3
    assert "文法文档缺口 0 项" in out


def test_parse_report_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "reports" / "parser.json"
    exit_code = cli.main(["parse", "report", "--dir", str(AST_DIR), "--json", str(out)])
    assert exit_code == 0
    assert "报告已写入" in capsys.readouterr().out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["acceptance"]
    assert all(entry["ok"] for entry in payload["acceptance"])
    assert payload["fixtures"]


def test_parse_report_on_empty_directory_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["parse", "report", "--dir", str(tmp_path)])
    assert exit_code == 1
    assert "没有语法夹具" in capsys.readouterr().out


def test_parse_is_listed_in_help(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main([])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "parse" in out
