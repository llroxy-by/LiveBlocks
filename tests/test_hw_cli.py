"""``liveblocks hw`` 子命令：``run`` / ``parity`` / ``unplug-test`` / ``frames`` 的输出与退出码。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks import cli
from liveblocks.hw import HW_PROTOCOL_VERSION, validate_hw_run
from liveblocks.runtime import validate_exec_state

REPO_ROOT = Path(__file__).resolve().parents[1]
IR_DIR = REPO_ROOT / "fixtures" / "ir" / "golden"
EXEC_IR_DIR = REPO_ROOT / "fixtures" / "exec" / "ir"


def test_hw_run_prints_summary_and_final_state(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["hw", "run", "--ir", str(IR_DIR / "all-ops.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    first = captured.out.splitlines()[0]
    assert first.startswith("运行 run-all-ops：ok")
    assert "backend=hw" in first
    assert "模拟器证据" in first
    assert "步骤 8/1000" in first
    assert "虚拟耗时 140 ms / 5000 ms" in first
    assert "事件 17 条" in first
    state = json.loads(captured.out[captured.out.index("{"):])
    validate_exec_state(state)
    assert state["backend"] == "hw"
    assert state["world"]["pos"] == {"x": 0, "y": -1}


def test_hw_run_writes_events_result_and_state(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    result = tmp_path / "result.json"
    state = tmp_path / "state.json"
    exit_code = cli.main(
        [
            "hw",
            "run",
            "--ir",
            str(IR_DIR / "from-valid-basic.json"),
            "--events-out",
            str(events),
            "--result-out",
            str(result),
            "--state-out",
            str(state),
        ]
    )
    assert exit_code == 0
    lines = events.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    assert all(json.loads(line)["backend"] == "hw" for line in lines)
    assert json.loads(result.read_text(encoding="utf-8"))["backend"] == "hw"
    assert json.loads(state.read_text(encoding="utf-8"))["kind"] == "ExecState"


def test_hw_run_exit_code_is_nonzero_for_a_failing_run() -> None:
    exit_code = cli.main(["hw", "run", "--ir", str(EXEC_IR_DIR / "drop-empty.json")])
    assert exit_code == 1


def test_hw_run_abort_hook_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["hw", "run", "--ir", str(IR_DIR / "all-ops.json"), "--abort-at-step", "2"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "aborted" in captured.out.splitlines()[0]
    assert "LB-R004" in captured.err


def test_hw_run_unplug_option_exercises_the_reconnect_path(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(
        ["hw", "run", "--ir", str(IR_DIR / "all-ops.json"), "--unplug-after-steps", "2", "--reconnect-attempts", "2"]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "ok" in captured.out.splitlines()[0]
    assert "LB-H001" in captured.err  # 断连留痕（已恢复）


def test_hw_parity_reports_agreement(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["hw", "parity", "--ir", str(IR_DIR / "all-ops.json")])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "parity run-all-ops：一致" in captured.out
    assert "模拟器证据" in captured.out


def test_hw_parity_writes_json_report(tmp_path: Path) -> None:
    out = tmp_path / "parity.json"
    exit_code = cli.main(["hw", "parity", "--ir", str(EXEC_IR_DIR / "drop-empty.json"), "--json", str(out)])
    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["hw_frames"]["out"] > 0


def test_hw_unplug_test_proves_both_half(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    out = tmp_path / "unplug.json"
    exit_code = cli.main(
        ["hw", "unplug-test", "--ir", str(IR_DIR / "all-ops.json"), "--unplug-after-steps", "2", "--json", str(out)]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    assert "① 拔线不重连" in captured.out and "通过" in captured.out
    assert "② 拔线后重连" in captured.out
    assert "模拟器证据" in captured.out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["lost"]["codes"] == ["LB-H001"]
    assert payload["reconnected"]["status"] == "ok"
    assert payload["lost"]["wall_ms"] < 2000


def test_hw_frames_prints_the_spec_and_hexdump(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["hw", "frames", "--hexdump"])
    captured = capsys.readouterr()
    assert exit_code == 0
    text = captured.out
    assert HW_PROTOCOL_VERSION in text
    assert "4C 42" in text
    assert "0x29B1" in text
    assert "HELLO" in text and "STEP_OK" in text and "HEARTBEAT" in text
    assert "4c 42 01 02 86" in text  # 示例帧的帧头


def test_hw_run_rejects_a_non_ir_document(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"kind": "Layout"}), encoding="utf-8")
    with pytest.raises(SystemExit):
        cli.main(["hw", "run", "--ir", str(bogus)])


def test_hw_run_validates_its_own_output_via_library() -> None:
    from liveblocks.hw import run_ir_over_pty
    from liveblocks.runtime import ir_from_document

    ir = ir_from_document(json.loads((IR_DIR / "all-ops.json").read_text(encoding="utf-8")))
    validate_hw_run(run_ir_over_pty(ir, run_id="run-cli-check"))
