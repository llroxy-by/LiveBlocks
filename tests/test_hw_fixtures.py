"""``fixtures/hw`` 夹具：golden 事件流 / ExecResult / ExecState 逐字节可复现。

golden 是**模拟器证据**：它们钉住「协议 + 事件映射 + 设备语义」的输出形状，不代表真机行为。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.hw import HW_PROTOCOL_VERSION, run_ir_over_pty, validate_hw_run
from liveblocks.runtime import canonical_text, ir_from_document

REPO_ROOT = Path(__file__).resolve().parents[1]
HW_DIR = REPO_ROOT / "fixtures" / "hw"
INDEX = json.loads((HW_DIR / "index.json").read_text(encoding="utf-8"))


def _fixtures() -> list[dict]:
    return INDEX["fixtures"]


def test_index_is_a_versioned_manifest_of_the_hw_protocol() -> None:
    assert INDEX["kind"] == "HwFixtureIndex"
    assert INDEX["hw_protocol_version"] == HW_PROTOCOL_VERSION
    assert INDEX["board"] == "esp32c3-supermini"
    assert INDEX["fixtures"], "夹具清单不能为空"
    assert "模拟器证据" in INDEX["note"]
    assert "待人工验证" in INDEX["note"]


def test_every_fixture_entry_points_at_existing_files() -> None:
    assert {entry["name"] for entry in _fixtures()} >= {"all-ops", "from-valid-basic", "drop-empty"}
    for entry in _fixtures():
        for key in ("ir", "events", "result", "state"):
            assert (HW_DIR / entry[key]).is_file(), f"{entry['name']} 缺少 {key}"
        assert entry["parity"] is True


@pytest.mark.parametrize("entry", _fixtures(), ids=[entry["name"] for entry in _fixtures()])
def test_golden_events_are_regenerable_byte_for_byte(entry: dict) -> None:
    ir = ir_from_document(json.loads((HW_DIR / entry["ir"]).resolve().read_text(encoding="utf-8")))
    run = run_ir_over_pty(ir, run_id=f"run-{entry['name']}")
    validate_hw_run(run)
    assert run.events_text() == (HW_DIR / entry["events"]).read_text(encoding="utf-8")
    assert run.result_text() == (HW_DIR / entry["result"]).read_text(encoding="utf-8")
    assert canonical_text(run.state) == (HW_DIR / entry["state"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("entry", _fixtures(), ids=[entry["name"] for entry in _fixtures()])
def test_golden_metadata_matches_the_run(entry: dict) -> None:
    ir = ir_from_document(json.loads((HW_DIR / entry["ir"]).resolve().read_text(encoding="utf-8")))
    run = run_ir_over_pty(ir, run_id=f"run-{entry['name']}")
    assert run.status == entry["status"]
    assert run.steps_executed == entry["steps"]
    assert len(run.events) == entry["events_count"]
    assert list(run.codes) == entry["codes"]
    assert run.link["frames_out"] == entry["frames"]["out"]
    assert run.link["frames_in"] == entry["frames"]["in"]


def test_every_golden_event_carries_the_hw_backend() -> None:
    for entry in _fixtures():
        for line in (HW_DIR / entry["events"]).read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            assert event["kind"] == "ExecEvent"
            assert event["backend"] == "hw"
            assert event["run_id"] == f"run-{entry['name']}"
