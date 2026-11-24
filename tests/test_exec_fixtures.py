"""``fixtures/exec`` 的逐夹具核对：期望值、golden 快照、诊断码覆盖与验收对照。

每一份夹具都是**合法 §6 ``Ir``**（先用 ``validate_ir`` 校验），执行后的事件流（JSONL）与最终状态
（JSON）必须与入库 golden 逐字节一致；清单里声明的 ``status`` / 步数 / 事件条数 / 诊断码 / ``limits``
也要逐项对上，否则报告与测试都判失败。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.ir import validate_ir
from liveblocks.runtime import (
    EXEC_CODES,
    NON_TERMINATING_STATUSES,
    REPORT_WALL_MS_BOUND,
    build_report,
    load_manifest,
    run_fixture,
    validate_exec_event,
    validate_exec_result,
    validate_exec_state,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXEC_DIR = REPO_ROOT / "fixtures" / "exec"


def manifest() -> dict:
    return load_manifest(EXEC_DIR)


def entries() -> list[dict]:
    return list(manifest()["fixtures"])


def test_manifest_lists_the_eight_fixtures() -> None:
    data = manifest()
    assert data["kind"] == "ExecFixtureIndex"
    assert data["exec_version"] == "0.0.1"
    names = [entry["name"] for entry in entries()]
    assert names == [
        "all-ops",
        "from-valid-basic",
        "from-valid-empty",
        "non-terminating-steps",
        "non-terminating-wait",
        "drop-empty",
        "abort",
        "timeout-exact",
    ]
    for entry in entries():
        for key in ("ir", "events", "state", "status", "steps", "events_count", "codes", "limits"):
            assert key in entry, f"{entry['name']} 缺少清单字段 {key}"
        assert (EXEC_DIR / entry["ir"]).is_file()
        assert (EXEC_DIR / entry["events"]).is_file()
        assert (EXEC_DIR / entry["state"]).is_file()


@pytest.mark.parametrize("entry", entries(), ids=lambda entry: entry["name"])
def test_fixture_ir_is_a_valid_ir_document(entry: dict) -> None:
    document = json.loads((EXEC_DIR / entry["ir"]).read_text(encoding="utf-8"))
    assert document["kind"] == "Ir"
    validate_ir(document)


@pytest.mark.parametrize("entry", entries(), ids=lambda entry: entry["name"])
def test_fixture_matches_its_declared_outcome(entry: dict) -> None:
    fixture = run_fixture(entry, EXEC_DIR)
    assert fixture.ir_error == ""
    assert fixture.run_error == ""
    assert fixture.stream_errors == ()
    assert fixture.status == fixture.expected_status
    assert fixture.steps_executed == fixture.expected_steps
    assert fixture.events_count == fixture.expected_events
    assert fixture.codes == fixture.expected_codes
    assert fixture.limits == fixture.expected_limits
    assert fixture.seq_strictly_increasing
    assert fixture.sim_only
    assert fixture.clock_matches_semantics


@pytest.mark.parametrize("entry", entries(), ids=lambda entry: entry["name"])
def test_fixture_goldens_are_byte_identical(entry: dict) -> None:
    fixture = run_fixture(entry, EXEC_DIR)
    assert fixture.run is not None
    assert fixture.run.events_text() == (EXEC_DIR / entry["events"]).read_text(encoding="utf-8")
    assert fixture.run.state_text() == (EXEC_DIR / entry["state"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("entry", entries(), ids=lambda entry: entry["name"])
def test_golden_documents_pass_their_validators(entry: dict) -> None:
    lines = (EXEC_DIR / entry["events"]).read_text(encoding="utf-8").splitlines()
    assert lines
    events = [json.loads(line) for line in lines]
    for event in events:
        validate_exec_event(event)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["backend"] == "sim" for event in events)

    state = json.loads((EXEC_DIR / entry["state"]).read_text(encoding="utf-8"))
    validate_exec_state(state)
    assert state["events"] == len(events)

    fixture = run_fixture(entry, EXEC_DIR)
    assert fixture.run is not None
    validate_exec_result(fixture.run.result)


def test_every_result_status_but_parse_error_is_reachable() -> None:
    """§7.2 的六个 status：本层产出五个，``parse_error`` 属于上游（输入已是 Ir）。"""
    statuses = {run_fixture(entry, EXEC_DIR).status for entry in entries()}
    assert statuses == {"ok", "limit_exceeded", "timeout", "runtime_error", "aborted"}
    assert "parse_error" not in statuses


def test_every_exec_code_but_the_defensive_one_has_a_fixture() -> None:
    """``LB-R001`` 是防御路径（§6 的 op 只能来自文法，合法 IR 走不到），由单元测试覆盖。"""
    codes: set[str] = set()
    for entry in entries():
        codes.update(run_fixture(entry, EXEC_DIR).codes)
    assert codes == set(EXEC_CODES) - {"LB-R001"}


def test_non_terminating_fixtures_end_with_a_limit_status() -> None:
    statuses = {
        entry["name"]: run_fixture(entry, EXEC_DIR).status
        for entry in entries()
        if entry["status"] in NON_TERMINATING_STATUSES
    }
    assert statuses == {"non-terminating-steps": "limit_exceeded", "non-terminating-wait": "timeout"}


def test_report_acceptance_passes_and_stays_within_the_time_bound() -> None:
    report = build_report(EXEC_DIR)
    assert report.manifest_error == ""
    assert report.doc_gaps == ()
    assert report.wall_ms <= REPORT_WALL_MS_BOUND
    assert len(report.fixtures) == 8
    for fixture in report.fixtures:
        assert fixture.wall_ms <= report.run_bound_ms
    failed = [criterion for ok, criterion, _evidence in report.acceptance() if not ok]
    assert failed == []


def test_report_detects_a_broken_golden(tmp_path: Path) -> None:
    """反证：把 golden 改一个字节，报告必须判失败（golden 不是假通过）。"""
    broken = tmp_path / "exec"
    broken.mkdir()
    data = manifest()
    (broken / "index.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    for entry in entries():
        for key in ("ir", "events", "state"):
            target = broken / entry[key]
            target.parent.mkdir(parents=True, exist_ok=True)
            source = EXEC_DIR / entry[key]
            if key == "events" and entry["name"] == "all-ops":
                text = source.read_text(encoding="utf-8").replace('"seq": 2', '"seq": 22', 1)
                target.write_text(text, encoding="utf-8")
            else:
                target.write_bytes(source.read_bytes())
    report = build_report(broken)
    assert not all(ok for ok, _criterion, _evidence in report.acceptance())
    broken_fixtures = [fixture for fixture in report.fixtures if not fixture.events_match]
    assert [fixture.name for fixture in broken_fixtures] == ["all-ops"]
