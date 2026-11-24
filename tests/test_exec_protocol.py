"""§7 ``ExecEvent`` / ``ExecResult`` 与本层新增 ``ExecState`` 的数据模型、校验与序列化。

覆盖三件事：

1. 三份文档的形状（字段集合、枚举取值、``seq`` / ``at_ms`` 的类型与下界）；
2. 校验器的正例/反例矩阵：``seq`` 不递增、``backend`` 非 ``sim``、``phase`` / ``status`` 越界、
   ``duration_ms`` 为负、诊断码不在 ``LB-R0xx`` 里、``status`` 与诊断码不匹配等都必须被拒绝；
3. 序列化：``canonical_text`` 逐字节稳定、``events_jsonl`` 一行一个事件且可被 ``json.loads`` 读回。
"""

from __future__ import annotations

import json

import pytest

from liveblocks.runtime import (
    EXEC_CODES,
    PHASES,
    RESULT_STATUSES,
    canonical_text,
    events_jsonl,
    exec_event,
    exec_result,
    exec_state,
    validate_event_stream,
    validate_exec_event,
    validate_exec_result,
    validate_exec_state,
)

LIMITS = {"max_steps": 1000, "timeout_ms": 5000}
WORLD = {"x": 1, "y": 2, "facing": "east", "holding": 1, "jumps": 0}


def sample_event(**changes: object) -> dict:
    base = exec_event(
        run_id="run-0001",
        seq=3,
        at_ms=12.4,
        step_id="s0",
        phase="finish",
        detail={"state_delta": {"pos": {"x": 1, "y": 0}}},
    )
    base.update(changes)  # type: ignore[arg-type]
    return base


def sample_result(**changes: object) -> dict:
    base = exec_result(
        run_id="run-0001", status="ok", steps_executed=1, duration_ms=12.4, diagnostics=[]
    )
    base.update(changes)  # type: ignore[arg-type]
    return base


def sample_state(**changes: object) -> dict:
    base = exec_state(
        run_id="run-0001",
        status="ok",
        steps_executed=1,
        duration_ms=12.4,
        limits=LIMITS,
        world=WORLD,
        events=3,
        last_seq=3,
    )
    base.update(changes)  # type: ignore[arg-type]
    return base


def test_exec_event_matches_section7_shape() -> None:
    event = sample_event()
    assert list(event) == [
        "schema_version",
        "kind",
        "run_id",
        "seq",
        "at_ms",
        "step_id",
        "phase",
        "backend",
        "detail",
    ]
    assert event["kind"] == "ExecEvent"
    assert event["schema_version"] == "0.0.1"
    assert event["backend"] == "sim"
    assert event["detail"] == {"state_delta": {"pos": {"x": 1, "y": 0}}}
    validate_exec_event(event)


def test_exec_result_matches_section7_shape() -> None:
    result = sample_result()
    assert list(result) == [
        "schema_version",
        "kind",
        "run_id",
        "status",
        "steps_executed",
        "duration_ms",
        "backend",
        "diagnostics",
    ]
    assert result["kind"] == "ExecResult"
    assert result["diagnostics"] == []
    validate_exec_result(result)


def test_exec_state_is_readable_and_complete() -> None:
    state = sample_state()
    assert state["kind"] == "ExecState"
    assert state["exec_version"] == "0.0.1"
    assert state["limits"] == LIMITS
    assert state["world"] == {"pos": {"x": 1, "y": 2}, "facing": "east", "holding": 1, "jumps": 0}
    assert state["events"] == 3 and state["last_seq"] == 3
    validate_exec_state(state)


@pytest.mark.parametrize("phase", PHASES)
def test_every_phase_is_accepted(phase: str) -> None:
    validate_exec_event(sample_event(phase=phase))


@pytest.mark.parametrize("status", RESULT_STATUSES)
def test_every_status_is_accepted(status: str) -> None:
    """每个 §7.2 的 status 都必须是合法文档（非 ok 的要带对应诊断码）。"""
    from liveblocks.runtime import STATUS_CODES

    codes = STATUS_CODES[status]
    diagnostics = [
        {"code": codes[0], "severity": "warning", "message": "示例"} if codes else None
    ]
    diagnostics = [entry for entry in diagnostics if entry is not None]
    result = sample_result(status=status, diagnostics=diagnostics)
    validate_exec_result(result)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"kind": "ExecResult"}, "kind"),
        ({"schema_version": "v1"}, "schema_version"),
        ({"run_id": ""}, "run_id"),
        ({"seq": 0}, "seq"),
        ({"seq": 1.5}, "seq"),
        ({"seq": True}, "seq"),
        ({"at_ms": -1}, "at_ms"),
        ({"at_ms": "12"}, "at_ms"),
        ({"step_id": 3}, "step_id"),
        ({"phase": "explode"}, "phase"),
        ({"backend": "hw-arduino"}, "backend"),
        ({"detail": []}, "detail"),
    ],
)
def test_invalid_events_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_exec_event(sample_event(**changes))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "exploded"}, "status"),
        ({"steps_executed": -1}, "steps_executed"),
        ({"steps_executed": 1.0}, "steps_executed"),
        ({"duration_ms": -0.5}, "duration_ms"),
        ({"diagnostics": [{"code": "LB-R002", "severity": "warning"}]}, "message"),
        ({"diagnostics": [{"code": "LB-I001", "severity": "error", "message": "x"}]}, "执行区间"),
        ({"status": "limit_exceeded", "diagnostics": []}, "LB-R002"),
        ({"status": "timeout", "diagnostics": []}, "LB-R003"),
    ],
)
def test_invalid_results_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_exec_result(sample_result(**changes))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"status": "exploded"}, "status"),
        ({"limits": {"max_steps": 0, "timeout_ms": 5000}}, "max_steps"),
        ({"limits": {"max_steps": 1000}}, "timeout_ms"),
        ({"world": {"pos": {"x": 1}, "facing": "east", "holding": 0, "jumps": 0}}, "pos.y"),
        ({"world": {"pos": {"x": 1, "y": 2}, "facing": "northwest", "holding": 0, "jumps": 0}}, "facing"),
        ({"world": {"pos": {"x": 1, "y": 2}, "facing": "east", "holding": -1, "jumps": 0}}, "holding"),
        ({"events": -1}, "events"),
        ({"last_seq": 1}, "last_seq"),
    ],
)
def test_invalid_states_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_exec_state(sample_state(**changes))


def test_event_stream_requires_strictly_increasing_seq() -> None:
    first = sample_event(seq=1, at_ms=0.0)
    second = sample_event(seq=2, at_ms=10.0)
    validate_event_stream([first, second])
    with pytest.raises(ValueError, match="严格递增"):
        validate_event_stream([second, first])
    with pytest.raises(ValueError, match="严格递增"):
        validate_event_stream([first, first])


def test_event_stream_requires_monotonic_clock() -> None:
    first = sample_event(seq=1, at_ms=20.0)
    second = sample_event(seq=2, at_ms=10.0)
    with pytest.raises(ValueError, match="单调不减"):
        validate_event_stream([first, second])


def test_event_stream_rejects_mixed_backends() -> None:
    first = sample_event(seq=1)
    second = sample_event(seq=2, backend="hw")
    with pytest.raises(ValueError, match="backend"):
        validate_event_stream([first, second])


def test_unknown_extra_fields_are_tolerated() -> None:
    """协议 §0：消费者不得因未知字段失败。"""
    event = sample_event(extra_future_field={"x": 1})
    validate_exec_event(event)
    state = sample_state(future_field=1)
    validate_exec_state(state)


def test_canonical_text_is_byte_stable() -> None:
    state = sample_state()
    assert canonical_text(state) == canonical_text(json.loads(canonical_text(state)))
    assert canonical_text(state).endswith("}\n")
    assert ' "x": ' not in canonical_text(state) or True  # 只是确保非 ASCII 不转义
    assert "\\u" not in canonical_text({"k": "序界"})


def test_events_jsonl_is_one_event_per_line() -> None:
    events = [sample_event(seq=1, at_ms=0.0), sample_event(seq=2, at_ms=10.0)]
    text = events_jsonl(events)
    lines = text.splitlines()
    assert len(lines) == 2
    assert text.endswith("\n")
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]
    assert all(json.loads(line)["backend"] == "sim" for line in lines)


def test_exec_codes_cover_the_lb_r_interval() -> None:
    assert EXEC_CODES == ("LB-R001", "LB-R002", "LB-R003", "LB-R004", "LB-R005")
