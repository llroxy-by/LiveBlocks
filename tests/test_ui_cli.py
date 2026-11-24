"""``liveblocks ui`` 子命令：``view`` / ``replay`` / ``report`` 的输出、退出码与文件产出。

口径与 LB-007 的 ``exec`` 子命令一致：**stdout 只放「视图本身」**（文本或 HTML），
诊断摘要走 stderr，这样 ``ui view --format html > view.html`` 拿到的就是可以直接打开的
单文件页面；``--fail-on-error`` 只在确实有 error 级问题时以非零码退出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.ui.protocol import LAYER_TITLES

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "fixtures" / "runs"
SAMPLE = RUN_DIR / "sample-clipped.jsonl"
CORRUPT = RUN_DIR / "corrupt" / "missing-fields.jsonl"


def test_ui_view_prints_the_text_view_and_reports_summary_on_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["ui", "view", "--record", str(SAMPLE)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "run_id：run-clipped" in captured.out
    for title in LAYER_TITLES.values():
        assert title in captured.out
    assert "记录" in captured.err and "五层：" in captured.err


def test_ui_view_html_format_stdout_is_a_standalone_page(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ui", "view", "--record", str(SAMPLE), "--format", "html"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.startswith("<!DOCTYPE html>")
    assert captured.out.rstrip().endswith("</html>")


def test_ui_view_step_changes_the_rendered_page(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert cli.main(["ui", "view", "--record", str(SAMPLE), "--format", "none", "--text-out", str(tmp_path / "a.txt")]) == 0
    assert cli.main(["ui", "view", "--record", str(SAMPLE), "--step", "0", "--format", "none", "--text-out", str(tmp_path / "b.txt")]) == 0
    capsys.readouterr()
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") != (tmp_path / "b.txt").read_text(encoding="utf-8")


def test_ui_view_writes_html_text_and_overlay_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    html_path = tmp_path / "view.html"
    text_path = tmp_path / "view.txt"
    overlay_path = tmp_path / "overlay.png"
    exit_code = cli.main(
        [
            "ui",
            "view",
            "--record",
            str(SAMPLE),
            "--format",
            "none",
            "--html-out",
            str(html_path),
            "--text-out",
            str(text_path),
            "--overlay-out",
            str(overlay_path),
        ]
    )
    capsys.readouterr()
    assert exit_code == 0
    assert html_path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert "只读回放" in text_path.read_text(encoding="utf-8")
    assert overlay_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_ui_view_fail_on_error_only_fires_on_error_issues(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ui", "view", "--record", str(SAMPLE), "--format", "none", "--fail-on-error"]) == 0
    assert cli.main(["ui", "view", "--record", str(CORRUPT), "--format", "none", "--fail-on-error"]) == 1
    assert cli.main(["ui", "view", "--record", str(CORRUPT), "--format", "none"]) == 0
    capsys.readouterr()


def test_ui_view_missing_record_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["ui", "view", "--record", str(tmp_path / "nope.jsonl")])
    assert "找不到运行记录" in str(excinfo.value)


def test_ui_replay_confirms_two_replays_are_identical(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ui", "replay", "--record", str(RUN_DIR / "sample-all-ops.jsonl")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "两次回放：HTML 一致 / 文本 一致 / 叠加图 一致 / 时间线 一致" in captured.out
    assert "实测（只是参考，不是性能结论）" in captured.out
    assert captured.out.count("[") >= 5  # 时间线逐帧打印


def test_ui_replay_single_step_prints_only_that_frame(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ui", "replay", "--record", str(RUN_DIR / "sample-all-ops.jsonl"), "--step", "4"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "当前帧" in captured.out


def test_ui_replay_max_steps_truncates(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ui", "replay", "--record", str(RUN_DIR / "sample-all-ops.jsonl"), "--max-steps", "3"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "还有" in captured.out


def test_ui_report_passes_and_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_path = tmp_path / "ui-report.json"
    exit_code = cli.main(["ui", "report", "--dir", str(RUN_DIR), "--json", str(json_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "全部通过" in captured.out
    for criterion in ("五层", "回放同一记录两次结果一致", "可见提示", "截图"):
        assert criterion in captured.out
    document = json.loads(json_path.read_text(encoding="utf-8"))
    assert [item["ok"] for item in document["acceptance"]] == [True, True, True, True]
    assert document["doc_gaps"] == []
    assert len(document["fixtures"]) == 9


def test_ui_report_without_fixtures_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["ui", "report", "--dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "没有运行记录夹具" in captured.out


def test_ui_help_lists_the_three_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["ui", "--help"])
    captured = capsys.readouterr()
    for command in ("view", "replay", "report"):
        assert command in captured.out
