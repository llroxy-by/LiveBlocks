"""``liveblocks e2e`` 子命令：``run`` / ``regression`` 的输出、退出码与文件产出。

口径与其它子命令一致：**stdout 放报告本身**（人读的结论表 + 失败摘要），诊断摘要走 stderr，
退出码只有两种含义——0 = 全部合格，非 0 = 有失败项且已经打印了失败摘要。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = REPO_ROOT / "fixtures" / "e2e"


def test_e2e_run_writes_a_record_and_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "runs"
    assert cli.main(["e2e", "run", "--scene", "normal", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    record = out / "normal.jsonl"
    assert record.is_file()
    assert str(record) in captured.out
    assert "分层耗时（实测，不是性能结论）" in captured.out
    assert "共 1 个场景" in captured.out
    first = json.loads(record.read_text(encoding="utf-8").splitlines()[0])
    assert first["kind"] == "RunHeader"
    assert first["scene"] == "normal"


def test_e2e_run_with_corruption_exits_non_zero_and_names_the_layer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "runs"
    assert cli.main(["e2e", "run", "--scene", "normal", "--out", str(out), "--corrupt", "layout"]) == 1
    captured = capsys.readouterr()
    assert "破坏注入" in captured.out
    assert "LB-E006" in captured.err
    assert "layout" in captured.err


def test_e2e_run_rejects_an_unknown_scene(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["e2e", "run", "--scene", "nope", "--out", str(tmp_path / "runs")])
    assert "未知场景预设" in str(error.value.code)


def test_e2e_regression_passes_and_writes_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "runs"
    report_path = tmp_path / "report.json"
    assert (
        cli.main(
            [
                "e2e",
                "regression",
                "--scene",
                "normal",
                "--out",
                str(out),
                "--json",
                str(report_path),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "端到端回归报告（LB-009" in captured.out
    assert "验收对照：" in captured.out
    assert "失败摘要：无" in captured.out
    assert (out / "normal.jsonl").is_file()
    assert (out / "corrupt" / "normal.jsonl").is_file()

    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert document["root_layer"] == ""
    assert document["corrupt"] == "none"
    assert [scene["scene"] for scene in document["scenes"]] == ["normal"]
    assert [check["ok"] for check in document["acceptance"]] == [True] * 5
    assert document["corruption_check"]["detected"] is True


def test_e2e_regression_names_the_layer_when_a_fixture_is_broken(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "e2e",
                "regression",
                "--scene",
                "normal",
                "--out",
                str(tmp_path / "runs"),
                "--corrupt",
                "layout",
                "--no-self-check",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "最早失败层：layout —— 排列（layout）" in captured.out
    assert "FAIL normal" in captured.out
    assert "--corrupt layout" in captured.out


def test_e2e_regression_update_baseline_writes_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "e2e"
    assert (
        cli.main(
            [
                "e2e",
                "regression",
                "--dir",
                str(directory),
                "--scene",
                "normal",
                "--out",
                str(tmp_path / "runs"),
                "--update-baseline",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "本轮已重写基准" in captured.out
    document = json.loads((directory / "baseline.json").read_text(encoding="utf-8"))
    assert document["kind"] == "E2eBaseline"
    assert [scene["name"] for scene in document["scenes"]] == ["normal"]


def test_e2e_regression_reports_a_missing_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["e2e", "regression", "--dir", str(tmp_path / "nope")]) == 1
    captured = capsys.readouterr()
    assert "端到端回归无法开始" in captured.err
    assert "--update-baseline" in captured.err


def test_e2e_regression_rejects_update_baseline_with_corrupt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "e2e",
                "regression",
                "--dir",
                str(tmp_path / "e2e"),
                "--scene",
                "normal",
                "--corrupt",
                "layout",
                "--update-baseline",
            ]
        )
        == 1
    )
    assert "不能同时用" in capsys.readouterr().err


def test_e2e_help_lists_both_subcommands() -> None:
    parser = cli.build_parser()
    text = parser.format_help()
    assert "e2e" in text
    with pytest.raises(SystemExit) as error:
        cli.main(["e2e", "--help"])
    assert error.value.code == 0


def test_regression_default_dir_is_the_committed_baseline() -> None:
    assert cli.DEFAULT_E2E_DIR == Path("fixtures/e2e")
    assert (REPO_ROOT / cli.DEFAULT_E2E_DIR / "baseline.json").is_file()
    assert cli.DEFAULT_E2E_OUT == Path("runs/e2e")
