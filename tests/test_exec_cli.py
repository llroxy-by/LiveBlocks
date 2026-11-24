"""``liveblocks exec`` 子命令：``run`` 与 ``report`` 的输出、退出码与文件产出口径。

- ``exec run``：stdout 是「摘要 + 最终状态 JSON（ExecState）」；``--events-out`` / ``--result-out`` /
  ``--state-out`` 各写一份文件；``status == "ok"`` 退出码 0，其余 1；
- ``--abort-at-step`` / ``--abort-at-ms``：确定性中断；
- ``exec report``：逐夹具结果 + 八条验收对照，退出码 0 表示全过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.runtime import validate_exec_result, validate_exec_state, validate_event_stream

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_DIR = REPO_ROOT / "fixtures" / "exec"
IR_DIR = REPO_ROOT / "fixtures" / "ir" / "golden"


def test_exec_run_prints_summary_and_final_state(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["exec", "run", "--ir", str(IR_DIR / "all-ops.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    lines = captured.out.splitlines()
    assert lines[0].startswith("运行 run-all-ops：ok")
    assert "backend=sim" in lines[0]
    assert "步骤 8/1000" in lines[0]
    assert "虚拟耗时 140 ms / 5000 ms" in lines[0]
    assert "事件 17 条" in lines[0]
    state = json.loads(captured.out[captured.out.index("{") :])
    validate_exec_state(state)
    assert state["kind"] == "ExecState"
    assert state["world"]["pos"] == {"x": 0, "y": -1}
    assert state["limits"] == {"max_steps": 1000, "timeout_ms": 5000}


def test_exec_run_writes_all_three_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    events_path = tmp_path / "nested" / "events.jsonl"
    result_path = tmp_path / "nested" / "result.json"
    state_path = tmp_path / "nested" / "state.json"
    exit_code = cli.main(
        [
            "exec",
            "run",
            "--ir",
            str(IR_DIR / "all-ops.json"),
            "--events-out",
            str(events_path),
            "--result-out",
            str(result_path),
            "--state-out",
            str(state_path),
        ]
    )
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "事件流已写入" in output and "ExecResult 已写入" in output and "最终状态已写入" in output
    # 与入库 golden 逐字节一致
    assert events_path.read_text(encoding="utf-8") == (
        EXEC_DIR / "events" / "all-ops.jsonl"
    ).read_text(encoding="utf-8")
    assert state_path.read_text(encoding="utf-8") == (
        EXEC_DIR / "state" / "all-ops.json"
    ).read_text(encoding="utf-8")

    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    validate_event_stream(events)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    validate_exec_result(result)
    assert result["status"] == "ok"


def test_exec_run_reports_limit_status_with_exit_code_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(["exec", "run", "--ir", str(EXEC_DIR / "ir" / "non-terminating-steps.json")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "limit_exceeded" in captured.out.splitlines()[0]
    assert "LB-R002" in captured.err
    state = json.loads(captured.out[captured.out.index("{") :])
    assert state["status"] == "limit_exceeded"
    assert state["steps_executed"] == 10


def test_exec_run_timeout_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["exec", "run", "--ir", str(EXEC_DIR / "ir" / "non-terminating-wait.json")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "timeout" in captured.out.splitlines()[0]
    state = json.loads(captured.out[captured.out.index("{") :])
    assert state["duration_ms"] == 1000.0


@pytest.mark.parametrize(
    ("flag", "value"),
    [("--abort-at-step", "3"), ("--abort-at-ms", "40")],
)
def test_exec_run_abort_flags(capsys: pytest.CaptureFixture[str], flag: str, value: str) -> None:
    exit_code = cli.main(
        ["exec", "run", "--ir", str(EXEC_DIR / "ir" / "abort.json"), flag, value]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "aborted" in captured.out.splitlines()[0]
    assert "LB-R004" in captured.err
    state = json.loads(captured.out[captured.out.index("{") :])
    assert state["status"] == "aborted"


def test_exec_run_accepts_explicit_run_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state_path = tmp_path / "state.json"
    exit_code = cli.main(
        [
            "exec",
            "run",
            "--ir",
            str(IR_DIR / "from-valid-empty.json"),
            "--run-id",
            "run-0042",
            "--state-out",
            str(state_path),
        ]
    )
    assert exit_code == 0
    assert "运行 run-0042：ok" in capsys.readouterr().out
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["run_id"] == "run-0042"
    assert state["events"] == 1
    assert state["steps_executed"] == 0


def test_exec_run_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="找不到 Ir 文件"):
        cli.main(["exec", "run", "--ir", str(tmp_path / "nope.json")])


def test_exec_run_rejects_a_non_ir_document(tmp_path: Path) -> None:
    path = tmp_path / "ast.json"
    path.write_text(json.dumps({"kind": "Ast"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="需要 Ir"):
        cli.main(["exec", "run", "--ir", str(path)])


def test_exec_run_rejects_an_invalid_ir(tmp_path: Path) -> None:
    document = json.loads((IR_DIR / "all-ops.json").read_text(encoding="utf-8"))
    document["program"]["steps"][0]["op"] = "teleport"
    path = tmp_path / "bad-ir.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="不是合法的 Ir"):
        cli.main(["exec", "run", "--ir", str(path)])


def test_exec_report_prints_acceptance_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["exec", "report", "--dir", str(EXEC_DIR)])
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "验收对照：" in output
    assert "[未通过]" not in output
    assert '事件 seq 严格递增且 backend: "sim"' in output
    assert "非终止样例以 limit_exceeded / timeout 结束而非挂死" in output
    assert "测试总耗时在设定上限内" in output
    assert "带一个可读的状态输出（最终状态 JSON）" in output
    assert "合计 8 个夹具" in output


def test_exec_report_writes_a_json_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    json_path = tmp_path / "report.json"
    exit_code = cli.main(["exec", "report", "--dir", str(EXEC_DIR), "--json", str(json_path)])
    assert exit_code == 0
    capsys.readouterr()
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(report["fixtures"]) == 8
    assert all(entry["ok"] for entry in report["acceptance"])
    assert report["doc_gaps"] == []
    all_ops = next(entry for entry in report["fixtures"] if entry["name"] == "all-ops")
    assert all_ops["actual"]["events"] == 17
    assert all_ops["final_state"]["kind"] == "ExecState"


def test_exec_report_on_an_empty_directory_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = cli.main(["exec", "report", "--dir", str(tmp_path)])
    assert exit_code == 1
    assert "没有执行夹具" in capsys.readouterr().out
