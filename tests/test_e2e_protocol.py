"""端到端回归层（LB-009）的常量与**记录形状**校验。

这一组不跑链路（不生成图像、不识别），只核对协议常量与 :func:`validate_record` 的判定口径：
首行、层序、必填字段、事件 ``seq``、帧引用，以及每个 ``LB-E0xx`` 码都有真实触发路径。
"""

from __future__ import annotations

import pytest

from liveblocks.e2e.protocol import (
    CODE_BAD_EVENTS,
    CODE_BAD_FIRST_LINE,
    CODE_BAD_FRAME,
    CODE_BAD_HEADER,
    CODE_BAD_LAYER_FIELDS,
    CODE_MISSING_LAYER,
    CODE_ORDER,
    CODE_TITLES,
    CODE_UNKNOWN_KIND,
    CORRUPT_TARGETS,
    E2E_CODES,
    HEADER_REQUIRED_FIELDS,
    KIND_AST,
    KIND_CARD,
    KIND_EXEC_EVENT,
    KIND_EXEC_RESULT,
    KIND_IR,
    KIND_LAYOUT,
    KIND_MARKER,
    KIND_RANK,
    KIND_RUN_HEADER,
    LAYER_REQUIRED_FIELDS,
    RECORD_BOUND_MS,
    REQUIRED_KINDS,
    SCHEMA_VERSION,
    STAGE_KINDS,
    STAGE_LABELS,
    STAGE_OF_KIND,
    STAGES,
    TIME_BOUND_MS,
    E2eIssue,
    issue,
    validate_record,
)


# --- 最小合法记录（手写，足够让校验器全部通过） -------------------------------


def _header(**overrides: object) -> dict:
    document: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_RUN_HEADER,
        "run_id": "run-e2e-normal",
        "backend": "sim",
        "scene": "normal",
        "frame": {
            "frame_id": "f-e2e-normal",
            "image_ref": "scenes/normal.png",
            "width": 640,
            "height": 480,
            "source": "synthetic",
        },
        "generation": {
            "stage": "synth",
            "seed": 20261123,
            "image_ref": "scenes/normal.png",
            "image_sha256": "a" * 64,
            "ground_truth_ref": "scenes/normal.json",
            "ground_truth_sha256": "b" * 64,
            "cards_expected": 1,
        },
        "created_with": {
            "name": "liveblocks",
            "package_version": SCHEMA_VERSION,
            "e2e_version": "0.0.1",
            "layer_sources": list(STAGES),
        },
        "timings_ms": {stage: 1.0 for stage in STAGES},
    }
    document.update(overrides)
    return document


def _markers() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_MARKER,
        "frame_id": "f-e2e-normal",
        "markers": [],
    }


def _cards() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_CARD,
        "frame_id": "f-e2e-normal",
        "cards": [],
    }


def _layout() -> dict:
    return {"schema_version": SCHEMA_VERSION, "kind": KIND_LAYOUT, "frame_id": "f-e2e-normal", "rows": []}


def _ast() -> dict:
    return {"schema_version": SCHEMA_VERSION, "kind": KIND_AST, "frame_id": "f-e2e-normal", "root": {}}


def _ir() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_IR,
        "program": {"steps": [], "limits": {"max_steps": 1000, "timeout_ms": 5000}},
    }


def _event(seq: int) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_EXEC_EVENT,
        "run_id": "run-e2e-normal",
        "seq": seq,
        "at_ms": float(seq),
        "phase": "log",
        "backend": "sim",
    }


def _result() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND_EXEC_RESULT,
        "run_id": "run-e2e-normal",
        "status": "ok",
        "steps_executed": 0,
    }


def _record(*, header: dict | None = None, body: list[dict] | None = None) -> list[dict]:
    return [
        header if header is not None else _header(),
        *(
            body
            if body is not None
            else [_markers(), _cards(), _layout(), _ast(), _ir(), _event(1), _result()]
        ),
    ]


def _codes(documents: list[dict], **kwargs: object) -> list[str]:
    return [item.code for item in validate_record(documents, **kwargs)]


# --- 常量一致性 ---------------------------------------------------------------


def test_stages_are_the_six_documented_layers() -> None:
    assert STAGES == ("synth", "vision", "layout", "parser", "ir", "exec")
    assert set(STAGE_KINDS) == set(STAGES)
    assert set(STAGE_LABELS) == set(STAGES)
    assert STAGE_KINDS["synth"] == ()


def test_every_kind_belongs_to_exactly_one_stage() -> None:
    kinds = [kind for stage in STAGES for kind in STAGE_KINDS[stage]]
    assert len(kinds) == len(set(kinds))
    assert set(STAGE_OF_KIND) == set(kinds)
    assert set(KIND_RANK) == set(kinds)
    assert set(LAYER_REQUIRED_FIELDS) == set(kinds)


def test_required_kinds_cover_the_five_layer_objects() -> None:
    assert REQUIRED_KINDS == (KIND_CARD, KIND_LAYOUT, KIND_AST, KIND_IR, KIND_EXEC_RESULT)
    # ExecEvent 不在必填里：空程序也可能只有起步行，但绝不会有「没有 ExecResult」的合格记录。
    assert KIND_EXEC_EVENT not in REQUIRED_KINDS


def test_bounds_and_targets_are_declared() -> None:
    assert TIME_BOUND_MS == 60_000.0
    assert RECORD_BOUND_MS == 20_000.0
    assert CORRUPT_TARGETS == ("none", "scene-image", "vision", "layout", "parser", "ir", "exec")


def test_every_code_has_a_title_and_is_unique() -> None:
    assert len(E2E_CODES) == len(set(E2E_CODES))
    assert set(CODE_TITLES) == set(E2E_CODES)
    assert all(CODE_TITLES[code].strip() for code in E2E_CODES)
    assert all(code.startswith("LB-E0") for code in E2E_CODES)


def test_issue_rejects_unknown_code_severity_and_layer() -> None:
    with pytest.raises(ValueError):
        issue("LB-X999", "…")
    with pytest.raises(ValueError):
        issue(CODE_BAD_HEADER, "…", severity="fatal")
    with pytest.raises(ValueError):
        issue(CODE_BAD_HEADER, "…", layer="synthx")


def test_issue_describe_is_one_line_and_carries_scene_layer_line() -> None:
    entry = issue(CODE_BAD_LAYER_FIELDS, "缺字段 rows", line=4, layer="layout", scene="normal")
    text = entry.describe()
    assert isinstance(entry, E2eIssue)
    assert "\n" not in text
    assert text.startswith(f"{CODE_BAD_LAYER_FIELDS} error [normal/layout] 第 4 行:")
    assert "缺字段 rows" in text


# --- 合格记录 -----------------------------------------------------------------


def test_minimal_record_passes() -> None:
    assert validate_record(_record()) == []


def test_record_without_markers_line_still_passes() -> None:
    # MarkerCandidateList 是 S2a 的中间产物，可以不出现在记录里。
    documents = _record(body=[_cards(), _layout(), _ast(), _ir(), _event(1), _result()])
    assert validate_record(documents) == []


def test_record_with_many_events_passes() -> None:
    body = [_markers(), _cards(), _layout(), _ast(), _ir(), _event(1), _event(2), _event(3), _result()]
    assert validate_record(_record(body=body)) == []


# --- 每个码都有真实触发路径 ---------------------------------------------------


def test_empty_record_reports_e001() -> None:
    documents = validate_record([])
    assert [item.code for item in documents] == [CODE_BAD_FIRST_LINE]


def test_first_line_not_header_reports_e001() -> None:
    documents = _record(body=[_layout(), _ast(), _ir(), _result()])
    documents[0] = _layout()
    documents = [documents[0], *_record()[1:]]
    assert CODE_BAD_FIRST_LINE in _codes(documents)


def test_missing_layout_reports_e002_on_layout_layer() -> None:
    issues = validate_record(_record(body=[_markers(), _cards(), _ast(), _ir(), _event(1), _result()]))
    assert [(item.code, item.layer) for item in issues] == [(CODE_MISSING_LAYER, "layout")]


def test_duplicated_layer_reports_e003() -> None:
    body = [_markers(), _cards(), _layout(), _layout(), _ast(), _ir(), _event(1), _result()]
    issues = validate_record(_record(body=body))
    assert [(item.code, item.layer) for item in issues] == [(CODE_ORDER, "layout")]


def test_layer_out_of_order_reports_e003() -> None:
    body = [_markers(), _cards(), _ast(), _layout(), _ir(), _event(1), _result()]
    issues = validate_record(_record(body=body))
    assert [(item.code, item.layer) for item in issues] == [(CODE_ORDER, "layout")]


def test_header_missing_field_reports_e004() -> None:
    header = _header()
    header.pop("run_id")
    issues = validate_record(_record(header=header))
    assert [(item.code, item.line) for item in issues][0] == (CODE_BAD_HEADER, 1)
    assert "run_id" in issues[0].message


def test_header_missing_generation_reports_e004_on_synth_layer() -> None:
    header = _header()
    header.pop("generation")
    issues = validate_record(_record(header=header))
    assert [(item.code, item.layer) for item in issues] == [(CODE_BAD_HEADER, "synth")]


def test_header_missing_timing_reports_e004_on_that_layer() -> None:
    header = _header()
    header["timings_ms"].pop("parser")
    issues = validate_record(_record(header=header))
    assert [(item.code, item.layer) for item in issues] == [(CODE_BAD_HEADER, "parser")]


def test_unknown_kind_reports_e005() -> None:
    body = [_markers(), _cards(), _layout(), _ast(), _ir(), {"kind": "MysteryLayer"}, _event(1), _result()]
    assert CODE_UNKNOWN_KIND in _codes(_record(body=body))


def test_layout_missing_rows_reports_e006() -> None:
    layout = _layout()
    layout.pop("rows")
    body = [_markers(), _cards(), layout, _ast(), _ir(), _event(1), _result()]
    issues = validate_record(_record(body=body))
    assert [(item.code, item.layer) for item in issues] == [(CODE_BAD_LAYER_FIELDS, "layout")]


def test_event_seq_not_increasing_reports_e007() -> None:
    body = [_markers(), _cards(), _layout(), _ast(), _ir(), _event(2), _event(1), _result()]
    issues = validate_record(_record(body=body))
    assert [(item.code, item.layer) for item in issues] == [(CODE_BAD_EVENTS, "exec")]


def test_result_not_last_reports_e007() -> None:
    body = [_markers(), _cards(), _layout(), _ast(), _ir(), _result(), _event(1)]
    issues = validate_record(_record(body=body))
    assert (CODE_BAD_EVENTS, "exec") in [(item.code, item.layer) for item in issues]


def test_unresolvable_image_reports_e009() -> None:
    issues = validate_record(_record(), image_exists=lambda _ref: False)
    assert [(item.code, item.layer) for item in issues] == [(CODE_BAD_FRAME, "vision")]


def test_resolvable_image_does_not_report_e009() -> None:
    assert validate_record(_record(), image_exists=lambda _ref: True) == []


def test_issues_carry_scene_when_asked() -> None:
    issues = validate_record(_record(body=[_markers(), _cards(), _ast(), _ir(), _event(1), _result()]), scene="normal")
    assert issues and all(item.scene == "normal" for item in issues)


def test_header_required_fields_are_the_documented_list() -> None:
    assert HEADER_REQUIRED_FIELDS == (
        "schema_version",
        "kind",
        "run_id",
        "backend",
        "scene",
        "frame",
        "generation",
        "timings_ms",
        "created_with",
    )
    header = _header()
    for field in HEADER_REQUIRED_FIELDS:
        assert field in header
