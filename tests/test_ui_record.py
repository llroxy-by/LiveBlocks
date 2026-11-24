"""``liveblocks.ui`` 的运行记录解析：容错、可定位、每个问题码都有触发路径。

覆盖 Issue #20 第三条验收的一半（解析侧）：**缺失/损坏字段只产出 ``LB-U0xx`` 提示，
绝不抛异常**。另一半（界面侧「看得见」）在 ``tests/test_ui_render.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from liveblocks.ui.protocol import (
    ISSUE_BAD_EVENTS,
    ISSUE_BAD_FIELDS,
    ISSUE_BAD_FRAME,
    ISSUE_BAD_LINE,
    ISSUE_BAD_TIMINGS,
    ISSUE_CODES,
    ISSUE_MISSING_LAYER,
    ISSUE_NO_HEADER,
    ISSUE_ORDER,
    ISSUE_UNKNOWN_KIND,
    RecordIssue,
    check_header,
    issue,
)
from liveblocks.ui.record import parse_record_file, parse_record_text, resolve_frame_path
from liveblocks.ui.render import build_view

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / "fixtures" / "runs"
CORRUPT_DIR = RUN_DIR / "corrupt"

SAMPLE_FILES = ("sample-clipped.jsonl", "sample-multi-row.jsonl")
CORRUPT_FILES = tuple(sorted(path.name for path in CORRUPT_DIR.glob("*.jsonl")))


def _codes(record) -> set[str]:
    return {entry.code for entry in record.issues}


# --- 样例记录：字段齐全、层齐全 ------------------------------------------------


@pytest.mark.parametrize("name", SAMPLE_FILES)
def test_sample_record_parses_without_issues(name: str) -> None:
    record = parse_record_file(RUN_DIR / name)
    assert record.issues == []
    assert record.ok
    assert record.missing_layers == ()
    assert {"MarkerCandidateList", "CardCandidateList", "Layout", "Ast", "Ir", "ExecEvent", "ExecResult"} <= set(
        record.present_kinds
    )
    assert record.run_id.startswith("run-")
    assert record.backend == "sim"
    assert record.events
    assert record.timings.keys() >= {"vision", "layout", "parser", "ir", "exec"}


def test_sample_record_layer_counts() -> None:
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    assert len(record.markers) == 2
    assert len(record.cards) == 2
    assert len(record.rows) == 1
    assert [slot["card_face"] for slot in record.rows[0]["slots"]] == ["turn-right", "number-3"]
    assert [step["op"] for step in record.steps] == ["turn-right"]
    assert [event["phase"] for event in record.events] == ["log", "start", "finish"]
    assert record.result["status"] == "ok"
    assert record.limits == {"max_steps": 1000, "timeout_ms": 5000}


def test_partial_record_reports_missing_layers() -> None:
    record = parse_record_file(RUN_DIR / "sample-all-ops.jsonl")
    assert len(record.events) == 17
    assert len(record.steps) == 8
    assert set(record.missing_layers) == {"vision", "layout", "ast"}
    assert ISSUE_MISSING_LAYER in _codes(record)
    assert ISSUE_BAD_FRAME in _codes(record)  # 没有 frame 对象
    assert ISSUE_BAD_TIMINGS in _codes(record)  # 只测了 exec 一层
    assert record.has_error


def test_frame_ref_resolves_to_the_scene_png() -> None:
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    resolved = resolve_frame_path(record.frame["image_ref"], record_path=RUN_DIR / "sample-clipped.jsonl")
    assert resolved is not None
    assert resolved.name == "clipped.png"


# --- 损坏记录：给提示、不崩溃 --------------------------------------------------


@pytest.mark.parametrize("name", CORRUPT_FILES)
def test_corrupt_record_parses_without_raising(name: str) -> None:
    record = parse_record_file(CORRUPT_DIR / name)
    assert record.issues, f"{name} 应该有问题提示"
    assert all(isinstance(entry, RecordIssue) for entry in record.issues)
    assert all(entry.code in ISSUE_CODES for entry in record.issues)
    assert all(entry.severity in {"info", "warning", "error"} for entry in record.issues)


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("bad-json.jsonl", ISSUE_BAD_LINE),
        ("missing-fields.jsonl", ISSUE_BAD_FIELDS),
        ("no-header.jsonl", ISSUE_NO_HEADER),
        ("unknown-kind.jsonl", ISSUE_UNKNOWN_KIND),
        ("bad-frame.jsonl", ISSUE_BAD_FRAME),
        ("bad-events.jsonl", ISSUE_BAD_EVENTS),
    ),
)
def test_corrupt_record_reports_its_headline_code(name: str, expected: str) -> None:
    # 有的码是渲染阶段才发现的（例如帧图像找不到 = LB-U007），所以两边都算上
    record = parse_record_file(CORRUPT_DIR / name)
    view = build_view(record, record_path=CORRUPT_DIR / name)
    codes = _codes(record) | {entry.code for entry in view.issues}
    assert expected in codes


def test_order_code_from_unknown_kind_fixture() -> None:
    record = parse_record_file(CORRUPT_DIR / "unknown-kind.jsonl")
    assert ISSUE_ORDER in _codes(record)


def test_timings_code_from_missing_fields_fixture() -> None:
    record = parse_record_file(CORRUPT_DIR / "missing-fields.jsonl")
    assert ISSUE_BAD_TIMINGS in _codes(record)


def test_bad_json_line_is_dropped_but_the_rest_is_kept() -> None:
    record = parse_record_file(CORRUPT_DIR / "bad-json.jsonl")
    bad = [entry for entry in record.issues if entry.code == ISSUE_BAD_LINE]
    assert len(bad) == 1
    assert bad[0].line == 4
    assert "Ast" in record.present_kinds  # 第 5 行的 Ast 照常读到
    assert record.result["status"] == "ok"


def test_duplicate_layer_keeps_the_first_copy() -> None:
    text = "\n".join(
        [
            json.dumps({"kind": "RunHeader", "run_id": "run-x", "backend": "sim"}),
            json.dumps(
                {
                    "kind": "Layout",
                    "rows": [{"row_index": 0, "slots": [{"slot_index": 0, "card_face": "first"}]}],
                }
            ),
            json.dumps(
                {
                    "kind": "Layout",
                    "rows": [{"row_index": 0, "slots": [{"slot_index": 0, "card_face": "second"}]}],
                }
            ),
        ]
    )
    record = parse_record_text(text)
    assert [slot["card_face"] for slot in record.rows[0]["slots"]] == ["first"]
    assert ISSUE_ORDER in _codes(record)


def test_unknown_kind_line_is_ignored() -> None:
    text = "\n".join(
        [
            json.dumps({"kind": "RunHeader", "run_id": "run-x", "backend": "sim"}),
            json.dumps({"kind": "MysteryLayer", "payload": 1}),
        ]
    )
    record = parse_record_text(text)
    assert ISSUE_UNKNOWN_KIND in _codes(record)
    assert record.run_id == "run-x"


def test_missing_file_and_non_utf8_are_reported_not_raised(tmp_path: Path) -> None:
    missing = parse_record_file(tmp_path / "nope.jsonl")
    assert _codes(missing) == {ISSUE_BAD_LINE}
    assert "读不出来" in missing.issues[0].message

    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b"\xff\xfe\x00not utf-8")
    record = parse_record_file(broken)
    assert _codes(record) == {ISSUE_BAD_LINE}


def test_missing_marker_list_is_only_informational() -> None:
    text = "\n".join(
        [
            json.dumps({"kind": "RunHeader", "run_id": "run-x", "backend": "sim"}),
            json.dumps(
                {
                    "kind": "CardCandidateList",
                    "cards": [{"candidate_id": "c-0", "card_face": "jump", "center_px": [10, 20]}],
                }
            ),
        ]
    )
    record = parse_record_text(text)
    marker_notes = [
        entry
        for entry in record.issues
        if entry.code == ISSUE_MISSING_LAYER and "MarkerCandidateList" in entry.message
    ]
    assert marker_notes and all(entry.severity == "info" for entry in marker_notes)


def test_hw_backend_is_labelled_as_info() -> None:
    text = "\n".join([json.dumps({"kind": "RunHeader", "run_id": "run-hw", "backend": "hw"})])
    record = parse_record_text(text)
    assert any(entry.severity == "info" and "hw" in entry.message for entry in record.issues)


def test_bad_events_are_kept_in_file_order() -> None:
    record = parse_record_file(CORRUPT_DIR / "bad-events.jsonl")
    assert [event["seq"] for event in record.events] == [1, 2, 1]
    assert ISSUE_BAD_EVENTS in _codes(record)


def test_layer_diagnostics_are_deduplicated() -> None:
    # LB-003 会把标记层的问题同时写进 MarkerCandidateList 与 CardCandidateList
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    diagnostics = record.layer_diagnostics("vision")
    keys = [(entry.code, entry.message) for entry in diagnostics]
    assert len(keys) == len(set(keys))
    assert any(entry.code == "LB-V002" for entry in diagnostics)
    assert any(entry.code == "LB-V006" for entry in diagnostics)


# --- 每个问题码都要有真实触发路径 ----------------------------------------------


def _reachable_codes() -> set[str]:
    codes: set[str] = set()
    for path in sorted(RUN_DIR.glob("*.jsonl")) + sorted(CORRUPT_DIR.glob("*.jsonl")):
        codes |= _codes(parse_record_file(path))
    crafted = [
        # 没有 header
        json.dumps({"kind": "Layout", "rows": []}),
        # header 缺字段 + 帧引用缺失 + 耗时缺失
        json.dumps({"kind": "RunHeader", "frame": {"frame_id": "f-x"}}),
        # 耗时非法（负数 / 不是数字）
        json.dumps(
            {
                "kind": "RunHeader",
                "run_id": "run-y",
                "backend": "sim",
                "timings_ms": {"vision": -1, "layout": "slOW"},
            }
        ),
        # 卡片缺 center_px
        json.dumps({"kind": "CardCandidateList", "cards": [{"candidate_id": "c-0"}]}),
        # rows 不是数组
        json.dumps({"kind": "Layout", "rows": 3}),
        # Ast 缺 root
        json.dumps({"kind": "Ast"}),
        # Ir 缺 program
        json.dumps({"kind": "Ir"}),
        # 事件缺 seq / at_ms / phase
        json.dumps({"kind": "ExecEvent", "detail": {}}),
        # 结果缺 status
        json.dumps({"kind": "ExecResult"}),
        # 乱序（ExecResult 早于 Ir）
        json.dumps({"kind": "ExecResult", "status": "ok", "steps_executed": 0}),
        json.dumps({"kind": "Ir", "program": {"steps": [], "limits": {}}}),
        # 坏行
        "{not json",
        # 未知 kind
        json.dumps({"kind": "Nope"}),
    ]
    codes |= _codes(parse_record_text("\n".join(crafted)))
    return codes


def test_every_issue_code_has_a_reachable_trigger() -> None:
    missing = set(ISSUE_CODES) - _reachable_codes()
    assert not missing, f"这些问题码没有触发路径：{sorted(missing)}"


def test_issue_constructor_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        issue("LB-XXX", "warning", "x")
    with pytest.raises(ValueError):
        issue(ISSUE_BAD_LINE, "fatal", "x")
    assert issue(ISSUE_BAD_LINE, "error", "x", line=3, layer="ir").as_dict()["line"] == 3


def test_header_check_flags_missing_run_id() -> None:
    header, report = check_header({"kind": "RunHeader", "backend": "sim"}, line=1)
    assert header["run_id"] == ""
    assert report.issues and report.issues[0].code == ISSUE_BAD_FIELDS


def test_parsing_is_deterministic() -> None:
    first = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    second = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    assert first == second
    assert [entry.as_dict() for entry in first.issues] == [entry.as_dict() for entry in second.issues]


def test_record_summary_mentions_counts() -> None:
    record = parse_record_file(RUN_DIR / "sample-clipped.jsonl")
    summary = record.summary()
    assert "run-clipped" in summary
    assert "backend=sim" in summary
    assert "事件=3" in summary
