"""``liveblocks ir`` 子命令：normalize / report。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.ir import LIMIT_DEFAULTS, validate_ir

REPO_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = REPO_ROOT / "fixtures" / "ir"
ASTS = IR_DIR / "asts"


def test_ir_normalize_prints_section6_document(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ir", "normalize", "--ast", str(ASTS / "all-ops.json")])
    assert exit_code == 0
    output = capsys.readouterr().out
    document = json.loads(output)
    validate_ir(document)
    assert document["kind"] == "Ir"
    assert document["ir_version"] == "0.0.1"
    assert len(document["program"]["steps"]) == 8
    assert document["diagnostics"] == []
    # stdout 就是可以原样管道给下游的 JSON：与 golden 逐字节一致
    assert output == (IR_DIR / "golden" / "all-ops.json").read_text(encoding="utf-8")


def test_ir_normalize_reports_diagnostics_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["ir", "normalize", "--ast", str(ASTS / "unknown-op.json")])
    assert exit_code == 0  # 默认：诊断随 Ir 输出，退出码 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)  # stdout 只有 JSON
    assert [entry["code"] for entry in document["diagnostics"]] == ["LB-I001", "LB-I001"]
    assert "LB-I001@行1槽0" in captured.err


def test_ir_normalize_fail_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        ["ir", "normalize", "--ast", str(ASTS / "unknown-op.json"), "--fail-on-error"]
    )
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["diagnostics"]


def test_ir_normalize_writes_file_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "nested" / "ir.json"
    exit_code = cli.main(["ir", "normalize", "--ast", str(ASTS / "all-ops.json"), "--out", str(out)])
    assert exit_code == 0
    text = out.read_text(encoding="utf-8")
    assert text == (IR_DIR / "golden" / "all-ops.json").read_text(encoding="utf-8")
    summary = capsys.readouterr().out
    assert "Ir，步骤 8" in summary
    assert "limits max_steps=1000 timeout_ms=5000" in summary
    assert "sha256=" in summary


def test_ir_normalize_limits_flags_are_written_into_ir(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "ir",
            "normalize",
            "--ast",
            str(ASTS / "all-ops.json"),
            "--max-steps",
            "12",
            "--timeout-ms",
            "800",
        ]
    )
    assert exit_code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["program"]["limits"] == {"max_steps": 12, "timeout_ms": 800}


def test_ir_normalize_default_limits_are_section6_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.main(["ir", "normalize", "--ast", str(ASTS / "from-valid-empty.json")])
    document = json.loads(capsys.readouterr().out)
    assert document["program"]["limits"] == dict(LIMIT_DEFAULTS)
    assert document["program"]["steps"] == []


def test_ir_normalize_rejects_limits_flags_out_of_range(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["ir", "normalize", "--ast", str(ASTS / "all-ops.json"), "--max-steps", "0"])
    assert error.value.code not in (0, None)


def test_ir_normalize_rejects_layout_document(tmp_path: Path) -> None:
    path = tmp_path / "layout.json"
    path.write_text(json.dumps({"kind": "Layout", "schema_version": "0.0.1"}), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        cli.main(["ir", "normalize", "--ast", str(path)])
    assert "需要 Ast" in str(error.value.code)


def test_ir_normalize_rejects_invalid_ast(tmp_path: Path) -> None:
    path = tmp_path / "ast.json"
    path.write_text(
        json.dumps({"kind": "Ast", "schema_version": "0.0.1", "root": {"node": "Program"}}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as error:
        cli.main(["ir", "normalize", "--ast", str(path)])
    assert "不是合法的 Ast" in str(error.value.code)


def test_ir_normalize_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["ir", "normalize", "--ast", str(tmp_path / "nope.json")])
    assert "找不到 Ast 文件" in str(error.value.code)


def test_ir_report_prints_acceptance_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ir", "report", "--dir", str(IR_DIR)])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "验收对照" in output
    for criterion in (
        "同一 AST 规范化两次输出字节一致",
        "未知 op 报 LB-I001",
        "IR 通过 schema 校验",
        "golden 文件快照测试通过",
        "limits 显式化",
    ):
        assert criterion in output
    assert "[未通过]" not in output


def test_ir_report_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.json"
    exit_code = cli.main(["ir", "report", "--dir", str(IR_DIR), "--json", str(out)])
    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["op_gaps"] == []
    assert payload["doc_gaps"] == []
    assert all(item["ok"] for item in payload["acceptance"])


def test_ir_report_detects_a_broken_golden(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """把 golden 改一个字节，报告必须失败（否则「快照测试」是假通过）。"""
    broken = tmp_path / "ir"
    (broken / "asts").mkdir(parents=True)
    (broken / "golden").mkdir(parents=True)
    (broken / "asts" / "all-ops.json").write_text(
        (ASTS / "all-ops.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    golden = json.loads((IR_DIR / "golden" / "all-ops.json").read_text(encoding="utf-8"))
    golden["program"]["limits"]["max_steps"] = 999
    (broken / "golden" / "all-ops.json").write_text(
        json.dumps(golden, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (broken / "index.json").write_text(
        json.dumps(
            {
                "schema_version": "0.0.1",
                "kind": "IrFixtureIndex",
                "ir_version": "0.0.1",
                "fixtures": [
                    {
                        "name": "all-ops",
                        "ast": "asts/all-ops.json",
                        "golden": "golden/all-ops.json",
                        "steps": 8,
                        "ops": ["jump"],
                        "codes": [],
                        "limits": {"max_steps": 1000, "timeout_ms": 5000},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    exit_code = cli.main(["ir", "report", "--dir", str(broken)])
    assert exit_code == 1
    assert "[未通过]" in capsys.readouterr().out


def test_ir_report_without_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ir", "report", "--dir", str(tmp_path)])
    assert exit_code == 1
    assert "没有 IR 夹具" in capsys.readouterr().out


def test_ir_help_lists_both_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["ir", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "normalize" in output and "report" in output
